"""Codex CLI remote-session bridge used for precise recovery routing."""

from __future__ import annotations

import itertools
import json
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .events import AutoResumeResult


def _recoverable_kind(turn: dict[str, Any]) -> str | None:
    """Classify only transient failures reported by the local app-server."""

    if turn.get("status") != "failed":
        return None
    error = turn.get("error")
    if not isinstance(error, dict):
        return None
    encoded = json.dumps(error, ensure_ascii=False).lower()
    for code in (429, 502, 503, 504):
        if str(code) in encoded:
            return f"gateway_{code}"
    if any(marker in encoded for marker in ("timeout", "timed out", "connectionfailed", "disconnected", "serveroverloaded")):
        return "connection_interrupted"
    return None


class CliProtocolBridge:
    """Receive per-thread CLI failures and send recovery through app-server.

    Every CLI launched through the installed wrapper connects to this local
    server. Notifications include the thread id, so multiple terminals never
    need to be distinguished by title, focus, or screen coordinates.
    """

    def __init__(
        self,
        settings: Settings,
        on_recoverable: Callable[[str, str], None],
        on_completed: Callable[[str], None],
    ) -> None:
        self.settings = settings
        self.on_recoverable = on_recoverable
        self.on_completed = on_completed
        target = settings.target("cli")
        self.endpoint = str(target.get("protocol_endpoint", "ws://127.0.0.1:8765"))
        self.enabled = bool(target.get("protocol_enabled", True))
        # Never start the local server through the public `codex` wrapper:
        # doing so would make the server try to connect back to itself.
        self._codex_command = self._resolve_app_server_command(target)
        self._server: subprocess.Popen[str] | None = None
        self._socket: Any = None
        self._receiver: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._request_ids = itertools.count(1)
        self._pending: dict[int, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _resolve_app_server_command(target: dict[str, Any]) -> str:
        """优先使用存在的命令，避免扩展升级后旧路径阻止监测器启动。"""

        configured = str(target.get("app_server_command") or "").strip()
        if configured and Path(configured).is_file():
            return configured
        discovered = shutil.which("codex.exe")
        if discovered:
            return discovered
        original = str(target.get("original_command") or "").strip()
        if original and Path(original).is_file():
            return original
        return "codex"

    def is_available(self) -> bool:
        """Return whether the monitor has an active protocol connection."""

        return self._ready.is_set() and self._socket is not None

    def start(self) -> None:
        """Start the local server and subscribe to thread notifications."""

        if not self.enabled or self.is_available():
            return
        try:
            import websocket
        except ImportError:
            return
        self._stop.clear()
        if self._connect(websocket):
            return
        try:
            self._server = subprocess.Popen(
                [self._codex_command, "app-server", "--listen", self.endpoint],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError:
            # CLI 协议仅是可选能力，不能阻止 VS Code/桌面端监测启动。
            return
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and self._server.poll() is None:
            if self._connect(websocket):
                return
            time.sleep(0.2)
        self.stop()

    def _connect(self, websocket: Any) -> bool:
        """Open an app-server connection without a browser Origin header."""

        try:
            # The local app-server deliberately rejects browser origins. This
            # is a native process-to-process connection, like the Codex CLI.
            self._socket = websocket.create_connection(self.endpoint, timeout=1, suppress_origin=True)
            self._initialize()
            self._ready.set()
            self._receiver = threading.Thread(target=self._receive, name="codex-cli-protocol", daemon=True)
            self._receiver.start()
            return True
        except Exception:
            self._close_socket()
            return False

    def stop(self) -> None:
        """Close only the app-server process started by this monitor."""

        self._stop.set()
        self._ready.clear()
        self._close_socket()
        if self._server and self._server.poll() is None:
            self._server.terminate()
            try:
                self._server.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._server.kill()
        self._server = None

    def dispatch(self, thread_id: str, message: str) -> AutoResumeResult | None:
        """Send a new turn to the exact CLI thread that reported a failure."""

        if not self.is_available():
            return None
        resumed = self._request("thread/resume", {"threadId": thread_id, "excludeTurns": True})
        if not resumed or "error" in resumed:
            return AutoResumeResult("skipped", "CLI 会话已关闭，未发送 continue")
        started = self._request(
            "turn/start",
            {"threadId": thread_id, "input": [{"type": "text", "text": message}]},
        )
        if not started or "error" in started:
            return AutoResumeResult("skipped", "CLI 会话拒绝 continue，未向其他终端发送")
        return AutoResumeResult("dispatched", "已通过会话连接向报错的 Codex CLI 发送 continue")

    def _initialize(self) -> None:
        """Complete the required JSON-RPC handshake before receiving events."""

        if not self._socket:
            raise RuntimeError("app-server socket is not connected")
        request_id = next(self._request_ids)
        self._socket.send(
            json.dumps(
                {
                    "id": request_id,
                    "method": "initialize",
                    "params": {"clientInfo": {"name": "codex-monitor", "version": "1.0"}, "capabilities": {"experimentalApi": True}},
                }
            )
        )
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            message = json.loads(self._socket.recv())
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError("app-server initialization failed")
                return
        raise TimeoutError("app-server initialization timed out")

    def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Issue a request while the receiver thread owns socket reads."""

        if not self.is_available() or not self._socket:
            return None
        request_id = next(self._request_ids)
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._lock:
            self._pending[request_id] = response_queue
            try:
                self._socket.send(json.dumps({"id": request_id, "method": method, "params": params}))
            except Exception:
                self._pending.pop(request_id, None)
                self._ready.clear()
                return None
        try:
            return response_queue.get(timeout=8)
        except queue.Empty:
            return None
        finally:
            self._pending.pop(request_id, None)

    def _receive(self) -> None:
        while not self._stop.is_set() and self._socket:
            try:
                message = json.loads(self._socket.recv())
            except Exception:
                self._ready.clear()
                return
            response_id = message.get("id")
            if isinstance(response_id, int):
                pending = self._pending.get(response_id)
                if pending:
                    pending.put(message)
                continue
            if message.get("method") != "turn/completed":
                continue
            params = message.get("params")
            if not isinstance(params, dict):
                continue
            thread_id = params.get("threadId")
            turn = params.get("turn")
            if not isinstance(thread_id, str) or not isinstance(turn, dict):
                continue
            kind = _recoverable_kind(turn)
            if kind:
                self.on_recoverable(thread_id, kind)
            elif turn.get("status") == "completed":
                self.on_completed(thread_id)

    def _close_socket(self) -> None:
        socket = self._socket
        self._socket = None
        if socket:
            try:
                socket.close()
            except Exception:
                pass
