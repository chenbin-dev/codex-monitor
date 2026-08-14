"""Install the optional transparent `codex` wrapper for CLI monitoring."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import Settings
from .events import AutoResumeResult


def _wrapper_directory() -> Path:
    local_app_data = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return local_app_data / "CodexMonitor" / "bin"


def _find_original_command(wrapper_path: Path) -> str | None:
    """Find the currently installed Codex command without selecting our wrapper."""

    # `codex.cmd` 可能已经是本工具的转发器，排除后优先内置可执行文件。
    candidates = [shutil.which("codex.exe"), shutil.which("codex.ps1"), shutil.which("codex.cmd")]
    for candidate in candidates:
        if candidate and Path(candidate).resolve() != wrapper_path.resolve():
            return str(Path(candidate).resolve())
    return None


def install_cli_wrapper(settings: Settings) -> AutoResumeResult:
    """Put a lightweight `codex.cmd` before the normal user PATH entries."""

    directory = _wrapper_directory()
    wrapper_path = directory / "codex.cmd"
    original = _find_original_command(wrapper_path)
    if not original:
        return AutoResumeResult("skipped", "找不到原始 Codex 命令，未安装全局 CLI 监测")
    endpoint = str(settings.target("cli").get("protocol_endpoint", "ws://127.0.0.1:8765"))
    try:
        host_port = endpoint.removeprefix("ws://").split("/", 1)[0]
        host, port = host_port.rsplit(":", 1)
        int(port)
    except ValueError:
        return AutoResumeResult("skipped", "CLI 协议地址格式错误，未安装全局 CLI 监测")
    directory.mkdir(parents=True, exist_ok=True)
    wrapper_path.write_text(
        "\n".join(
            [
                "@echo off",
                "setlocal",
                "title Codex CLI - %CD%",
                "set \"CODEX_MONITOR_MANAGED=1\"",
                f"powershell.exe -NoProfile -Command \"$client = New-Object Net.Sockets.TcpClient; try {{ $client.Connect('{host}', {port}); exit 0 }} catch {{ exit 1 }} finally {{ $client.Dispose() }}\" >nul 2>&1",
                "if errorlevel 1 (",
                f"  call \"{original}\" %*",
                ") else (",
                f"  call \"{original}\" --remote \"{endpoint}\" %*",
                ")",
                "set \"EXIT_CODE=%ERRORLEVEL%\"",
                "endlocal & exit /b %EXIT_CODE%",
                "",
            ]
        ),
        encoding="utf-8",
        newline="\r\n",
    )
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
            stored_path, _ = winreg.QueryValueEx(key, "Path")
    except FileNotFoundError:
        stored_path = ""
    except OSError:
        return AutoResumeResult("skipped", "转发器已生成，但无法读取用户 PATH")
    entries = [entry for entry in str(stored_path).split(os.pathsep) if entry]
    if str(directory).lower() not in {entry.lower() for entry in entries}:
        entries.insert(0, str(directory))
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, os.pathsep.join(entries))
        except OSError:
            return AutoResumeResult("skipped", "转发器已生成，但无法更新用户 PATH")
    settings.target("cli")["wrapper_path"] = str(wrapper_path)
    settings.target("cli")["original_command"] = original
    app_server_command = shutil.which("codex.exe")
    if app_server_command:
        settings.target("cli")["app_server_command"] = str(Path(app_server_command).resolve())
    settings.save()
    return AutoResumeResult("installed", "全局 CLI 监测已安装。关闭并重新打开终端后，照常输入 codex 即可")
