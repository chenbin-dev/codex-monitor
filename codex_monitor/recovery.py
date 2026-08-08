"""Multi-target recovery adapters for VS Code, Codex Desktop, and Codex CLI."""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from .auto_resume import AutoResumer, TargetWindow, WindowLocator, _press_virtual_key, _send_unicode
from .cli_protocol import CliProtocolBridge
from .config import Settings
from .events import AutoResumeResult, LogRecord


VK_RETURN = 0x0D


@dataclass(frozen=True)
class CliProcess:
    """A locally running interactive Codex CLI process, without command output."""

    pid: int
    command_line: str


class RecoveryAdapter(Protocol):
    """A target that can safely accept a recovery message."""

    target_id: str

    def is_available(self) -> bool: ...

    def attempt(self, thread_id: str | None = None) -> AutoResumeResult: ...


def list_interactive_cli_processes() -> list[CliProcess]:
    """Discover CLI processes while excluding the VS Code app-server child process."""

    command = (
        "Get-CimInstance Win32_Process -Filter \"Name='codex.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 or not completed.stdout.strip():
            return []
        raw = json.loads(completed.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    rows = raw if isinstance(raw, list) else [raw]
    processes: list[CliProcess] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        command_line = str(row.get("CommandLine") or "")
        if "app-server" in command_line.lower():
            continue
        pid = row.get("ProcessId")
        if isinstance(pid, int):
            processes.append(CliProcess(pid, command_line))
    return processes


def _find_terminal_window(title_contains: str) -> TargetWindow | None:
    """Find a single visible terminal whose title identifies the Codex session."""

    try:
        import win32gui
    except ImportError:
        return None
    matches: list[TargetWindow] = []
    generic_matches: list[TargetWindow] = []
    requested = title_contains.lower().strip()

    def visitor(handle: int, _: object) -> None:
        title = win32gui.GetWindowText(handle)
        lowered = title.lower()
        if not win32gui.IsWindowVisible(handle) or not title:
            return
        if requested and requested in lowered:
            matches.append(TargetWindow(handle, title, tuple(win32gui.GetWindowRect(handle))))
        elif "codex" in lowered and "visual studio code" not in lowered:
            generic_matches.append(TargetWindow(handle, title, tuple(win32gui.GetWindowRect(handle))))

    win32gui.EnumWindows(visitor, None)
    if len(matches) == 1:
        return matches[0]
    if not matches and len(generic_matches) == 1:
        return generic_matches[0]
    return None


def _find_vscode_window(settings: Settings) -> TargetWindow | None:
    """Find the VS Code host window used by an integrated terminal."""

    target = settings.target("vscode")
    return WindowLocator.find(str(target.get("title_contains") or ""), "Visual Studio Code")


def _is_foreground_window(target: TargetWindow) -> bool:
    """Only use the integrated-terminal fallback while VS Code is foreground."""

    try:
        import win32gui

        return win32gui.GetForegroundWindow() == target.handle
    except ImportError:
        return False


class UIAdapter:
    """The calibrated UI path shared by VS Code and the Desktop application."""

    def __init__(self, settings: Settings, target_id: str) -> None:
        self.settings = settings
        self.target_id = target_id
        self._resumer = AutoResumer(settings, target_id)

    def is_available(self) -> bool:
        if not self.settings.is_target_calibrated(self.target_id):
            return False
        target = self.settings.target(self.target_id)
        fallback = "Visual Studio Code" if target.get("kind") == "vscode" else None
        return WindowLocator.find(str(target.get("title_contains", "")), fallback) is not None

    def attempt(self, thread_id: str | None = None) -> AutoResumeResult:
        del thread_id
        return self._resumer.attempt()


class DesktopProtocolBridge:
    """Send a follow-up through the local app-server JSON-RPC protocol.

    Windows cannot manage the CLI's long-running daemon, so each dispatch owns
    a short-lived stdio app-server process. It only receives the thread id and
    resume message already held by the recovery state; no log body or auth file
    is read by this tool.
    """

    def is_available(self) -> bool:
        try:
            completed = subprocess.run(
                ["codex", "--version"],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return completed.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _call(process: subprocess.Popen[str], messages: queue.Queue[str], request_id: int, method: str, params: dict[str, object]) -> dict[str, object] | None:
        if not process.stdin:
            return None
        process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                line = messages.get(timeout=max(0.05, deadline - time.monotonic()))
            except queue.Empty:
                break
            try:
                response = json.loads(line)
            except json.JSONDecodeError:
                continue
            if response.get("id") == request_id:
                return response if isinstance(response, dict) else None
        return None

    def dispatch(self, thread_id: str | None, message: str) -> AutoResumeResult | None:
        if not thread_id or not self.is_available():
            return None
        process: subprocess.Popen[str] | None = None
        try:
            process = subprocess.Popen(
                ["codex", "app-server", "--stdio"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if not process.stdout:
                return None
            messages: queue.Queue[str] = queue.Queue()

            def read_messages() -> None:
                for line in process.stdout:
                    messages.put(line)

            threading.Thread(target=read_messages, name="codex-app-server-rpc", daemon=True).start()
            initialized = self._call(
                process,
                messages,
                1,
                "initialize",
                {"clientInfo": {"name": "codex-monitor", "version": "1.0"}, "capabilities": {"experimentalApi": True}},
            )
            if not initialized or "error" in initialized:
                return None
            resumed = self._call(process, messages, 2, "thread/resume", {"threadId": thread_id, "excludeTurns": True})
            if not resumed or "error" in resumed:
                return None
            started = self._call(
                process,
                messages,
                3,
                "turn/start",
                {"threadId": thread_id, "input": [{"type": "text", "text": message}]},
            )
            if started and "error" not in started:
                return AutoResumeResult("dispatched", "已通过 Codex app-server 向桌面端会话发送 continue")
        except (OSError, subprocess.SubprocessError, BrokenPipeError):
            return None
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
        return None


class DesktopAdapter:
    """Prefer a protocol bridge when one is available, otherwise use safe UI input."""

    target_id = "desktop"

    def __init__(self, settings: Settings, bridge: DesktopProtocolBridge | None = None) -> None:
        self.settings = settings
        self.bridge = bridge or DesktopProtocolBridge()
        self.ui = UIAdapter(settings, self.target_id)

    def is_available(self) -> bool:
        # Calibration is the ownership proof that keeps a similarly titled CLI
        # window from being treated as a Desktop conversation.
        return self.ui.is_available()

    def attempt(self, thread_id: str | None = None) -> AutoResumeResult:
        if bool(self.settings.target(self.target_id).get("protocol_enabled", True)) and self.bridge.is_available():
            result = self.bridge.dispatch(thread_id, str(self.settings.data["resume_message"]))
            if result is not None:
                return result
        return self.ui.attempt(thread_id)


class CliAdapter:
    """Prefer precise protocol dispatch, with a single-window UI fallback."""

    target_id = "cli"

    def __init__(
        self,
        settings: Settings,
        process_provider: Callable[[], list[CliProcess]] = list_interactive_cli_processes,
        bridge: CliProtocolBridge | None = None,
    ) -> None:
        self.settings = settings
        self._process_provider = process_provider
        self.bridge = bridge

    def _single_process(self) -> CliProcess | None:
        processes = self._process_provider()
        return processes[0] if len(processes) == 1 else None

    def owns_process(self, process_uuid: str | None) -> bool:
        """Match a shared-log record to a live CLI PID when available."""

        if not process_uuid:
            return False
        match = re.search(r"(?:^|:)pid:(\d+)(?::|$)", process_uuid, re.IGNORECASE)
        if not match:
            return False
        pid = int(match.group(1))
        return any(process.pid == pid for process in self._process_provider())

    def is_available(self) -> bool:
        if not bool(self.settings.target(self.target_id).get("enabled", True)):
            return False
        if self.bridge and self.bridge.is_available():
            return True
        if not self._single_process():
            return False
        title = str(self.settings.target(self.target_id).get("title_contains") or "")
        if _find_terminal_window(title) is not None:
            return True
        if bool(self.settings.target(self.target_id).get("allow_vscode_terminal_input", True)):
            return _find_vscode_window(self.settings) is not None
        return False

    def attempt(self, thread_id: str | None = None) -> AutoResumeResult:
        if thread_id and self.bridge:
            result = self.bridge.dispatch(thread_id, str(self.settings.data["resume_message"]))
            if result is not None:
                return result
        if not bool(self.settings.target(self.target_id).get("allow_blind_terminal_input", True)):
            return AutoResumeResult("skipped", "CLI 终端自动输入已在配置中关闭")
        if not self._single_process():
            return AutoResumeResult("skipped", "未找到唯一的交互式 Codex CLI 进程")
        title = str(self.settings.target(self.target_id).get("title_contains") or "")
        terminal = _find_terminal_window(title)
        integrated = False
        if not terminal and bool(self.settings.target(self.target_id).get("allow_vscode_terminal_input", True)):
            terminal = _find_vscode_window(self.settings)
            integrated = terminal is not None
        if not terminal:
            return AutoResumeResult("skipped", "未找到唯一的 Codex CLI 终端窗口")
        if integrated and not _is_foreground_window(terminal):
            return AutoResumeResult("skipped", "VS Code 不是当前活动窗口，未向集成终端输入")
        if not WindowLocator.activate(terminal):
            return AutoResumeResult("skipped", "Codex CLI 终端窗口不可操作")
        try:
            _send_unicode(str(self.settings.data["resume_message"]))
            _press_virtual_key(VK_RETURN)
            return AutoResumeResult("dispatched", "已向唯一 Codex CLI 终端发送 continue")
        except Exception as error:
            return AutoResumeResult("skipped", f"CLI 自动恢复未派发：{type(error).__name__}")


class RecoveryRegistry:
    """Resolve an error to exactly one available target before any input is sent."""

    def __init__(
        self,
        settings: Settings,
        adapters: dict[str, RecoveryAdapter] | None = None,
        cli_bridge: CliProtocolBridge | None = None,
    ) -> None:
        self.settings = settings
        self.adapters: dict[str, RecoveryAdapter] = adapters or {
            "vscode": UIAdapter(settings, "vscode"),
            "desktop": DesktopAdapter(settings),
            "cli": CliAdapter(settings, bridge=cli_bridge),
        }

    def select(self, record: LogRecord) -> str | None:
        # CLI retry events have a distinct source. Prefer the CLI adapter so a
        # VS Code plugin calibration cannot steal an integrated-terminal error.
        cli = self.adapters.get("cli")
        if cli and "codex_core::responses_retry" in record.target.lower() and cli.is_available():
            return "cli"
        if isinstance(cli, CliAdapter) and cli.owns_process(record.process_uuid):
            return "cli"
        available = [target_id for target_id, adapter in self.adapters.items() if adapter.is_available()]
        return available[0] if len(available) == 1 else None

    def attempt(self, target_id: str, thread_id: str | None = None) -> AutoResumeResult:
        adapter = self.adapters.get(target_id)
        if not adapter:
            return AutoResumeResult("skipped", "恢复目标已不存在")
        return adapter.attempt(thread_id)

    def status_lines(self) -> list[str]:
        return [
            f"{target_id}: {'可恢复' if adapter.is_available() else '未就绪或不唯一'}"
            for target_id, adapter in self.adapters.items()
            if bool(self.settings.target(target_id).get("enabled", True))
        ]


def launch_managed_cli() -> AutoResumeResult:
    """Open a normal CLI in the user's home directory for a quick check."""

    try:
        wrapper = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "CodexMonitor" / "bin" / "codex.cmd"
        # Pass the batch file as its own argument.  Combining `call "..."`
        # into one argument makes Python escape its quotes as `\"` on Windows,
        # which cmd.exe then treats as literal characters instead of a path.
        command = str(wrapper) if wrapper.exists() else "codex"
        subprocess.Popen(
            ["cmd.exe", "/d", "/k", command],
            cwd=str(Path.home()),
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
        return AutoResumeResult("launched", "已启动受管 Codex CLI 终端")
    except OSError as error:
        return AutoResumeResult("skipped", f"无法启动 Codex CLI：{type(error).__name__}")
