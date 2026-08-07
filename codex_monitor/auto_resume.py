"""受控的 VS Code 自动恢复与输入框校准。"""

from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .events import AutoResumeResult


VK_CONTROL = 0x11
VK_SHIFT = 0x10
VK_P = 0x50
VK_RETURN = 0x0D
VK_LBUTTON = 0x01
SW_RESTORE = 9
GA_ROOT = 2


def _enable_dpi_awareness() -> None:
    """Use physical screen coordinates so Win32 and UI Automation agree."""

    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


_enable_dpi_awareness()


@dataclass(frozen=True)
class TargetWindow:
    handle: int
    title: str
    rect: tuple[int, int, int, int]


class _KeyInput(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_ushort),
        ("wParamH", ctypes.c_ushort),
    ]


class _InputUnion(ctypes.Union):
    # INPUT uses the size of its largest union member. SendInput rejects the
    # shortened keyboard-only layout with ERROR_INVALID_PARAMETER on Win64.
    _fields_ = [("mi", _MouseInput), ("ki", _KeyInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", _InputUnion)]


def _send_unicode(text: str) -> None:
    """使用 SendInput 发送 Unicode 字符，避免 PyAutoGUI 无法输入中文的问题。"""

    inputs: list[_Input] = []
    for char in text:
        inputs.extend(
            [
                _Input(1, _InputUnion(ki=_KeyInput(0, ord(char), 0x0004, 0, 0))),
                _Input(1, _InputUnion(ki=_KeyInput(0, ord(char), 0x0004 | 0x0002, 0, 0))),
            ]
        )
    array_type = _Input * len(inputs)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    ctypes.set_last_error(0)
    sent = user32.SendInput(len(inputs), array_type(*inputs), ctypes.sizeof(_Input))
    if sent != len(inputs):
        error_code = ctypes.get_last_error()
        raise OSError(error_code, "SendInput 未能完整输入恢复文案")


def _press_virtual_key(key: int) -> None:
    user32 = ctypes.windll.user32
    user32.keybd_event(key, 0, 0, 0)
    user32.keybd_event(key, 0, 0x0002, 0)


def _open_codex_sidebar() -> None:
    """扩展没有公开聚焦输入框命令，先通过命令面板打开其侧边栏。"""

    user32 = ctypes.windll.user32
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_SHIFT, 0, 0, 0)
    _press_virtual_key(VK_P)
    user32.keybd_event(VK_SHIFT, 0, 0x0002, 0)
    user32.keybd_event(VK_CONTROL, 0, 0x0002, 0)
    time.sleep(0.25)
    _send_unicode("chatgpt.openSidebar")
    _press_virtual_key(VK_RETURN)
    time.sleep(1.0)


class WindowLocator:
    """只匹配用户校准过的唯一 VS Code 工作区窗口。"""

    @staticmethod
    def find(title_contains: str) -> TargetWindow | None:
        try:
            import win32gui
        except ImportError:
            return None
        matches: list[TargetWindow] = []

        def visitor(handle: int, _: Any) -> None:
            title = win32gui.GetWindowText(handle)
            if not win32gui.IsWindowVisible(handle) or not title:
                return
            if title_contains.lower() in title.lower():
                matches.append(TargetWindow(handle, title, tuple(win32gui.GetWindowRect(handle))))

        win32gui.EnumWindows(visitor, None)
        if not matches:
            # Workspace names change after renaming or reopening a folder. If
            # the configured title is stale, accept one unambiguous VS Code
            # window rather than guessing among multiple editor windows.
            fallback: list[TargetWindow] = []

            def fallback_visitor(handle: int, _: Any) -> None:
                title = win32gui.GetWindowText(handle)
                if (
                    win32gui.IsWindowVisible(handle)
                    and title
                    and "visual studio code" in title.lower()
                ):
                    fallback.append(TargetWindow(handle, title, tuple(win32gui.GetWindowRect(handle))))

            win32gui.EnumWindows(fallback_visitor, None)
            if len(fallback) == 1:
                return fallback[0]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def activate(target: TargetWindow) -> bool:
        try:
            import win32api
            import win32con
            import win32gui
            import win32process
        except ImportError:
            return False
        attached_threads: list[int] = []
        try:
            # Restoring a maximized window changes its layout and invalidates
            # calibrated coordinates. Only restore a genuinely minimized one.
            if win32gui.IsIconic(target.handle):
                win32gui.ShowWindow(target.handle, win32con.SW_RESTORE)
            current_thread = win32api.GetCurrentThreadId()
            foreground = win32gui.GetForegroundWindow()
            for handle in (foreground, target.handle):
                thread_id, _ = win32process.GetWindowThreadProcessId(handle)
                if thread_id != current_thread and thread_id not in attached_threads:
                    win32process.AttachThreadInput(current_thread, thread_id, True)
                    attached_threads.append(thread_id)
            win32gui.BringWindowToTop(target.handle)
            win32gui.SetForegroundWindow(target.handle)
            win32gui.SetFocus(target.handle)
            time.sleep(0.25)
            return win32gui.GetForegroundWindow() == target.handle
        except Exception:
            return False
        finally:
            current_thread = win32api.GetCurrentThreadId()
            for thread_id in reversed(attached_threads):
                try:
                    win32process.AttachThreadInput(current_thread, thread_id, False)
                except Exception:
                    pass


def _relative_point(settings: Settings, target: TargetWindow) -> tuple[int, int]:
    point = settings.data["target_window"]["input_point"]
    left, top, right, bottom = target.rect
    return (
        round(left + (right - left) * float(point["relative_x"])),
        round(top + (bottom - top) * float(point["relative_y"])),
    )


def _value_from_edit(element: Any) -> str | None:
    try:
        value = str(element.get_value())
        info = element.element_info
        # VS Code's ProseMirror exposes its placeholder as both the accessible
        # name and value even when the editor is empty.
        if "ProseMirror" in str(info.class_name) and value.strip() == str(info.name).strip():
            return ""
        return value
    except Exception:
        return None


def _find_codex_input(target: TargetWindow) -> tuple[tuple[int, int], str] | None:
    """Return the visible Codex editor center and value via UI Automation."""

    try:
        from pywinauto import Desktop

        window = Desktop(backend="uia").window(handle=target.handle)
        editors = [
            element
            for element in window.descendants(control_type="Edit")
            if "ProseMirror" in str(element.element_info.class_name)
        ]
        if not editors:
            return None
        # The compose editor is the lowest ProseMirror editor in the sidebar.
        editor = max(editors, key=lambda item: item.element_info.rectangle.bottom)
        value = _value_from_edit(editor)
        if value is None:
            return None
        rect = editor.element_info.rectangle
        return ((round((rect.left + rect.right) / 2), round((rect.top + rect.bottom) / 2)), value)
    except Exception:
        return None


def _read_input_value(x: int, y: int) -> str | None:
    """通过 UI Automation 读取输入框值；无法证明为空时必须拒绝发送。"""

    try:
        from pywinauto import Desktop
    except ImportError:
        return None
    try:
        desktop = Desktop(backend="uia")
        element = desktop.from_point(x, y)
        for _ in range(8):
            if element.element_info.control_type == "Edit":
                return _value_from_edit(element)
            element = element.parent()

        # Electron may report the webview container for a point inside the
        # contenteditable element. Search only the containing top-level window.
        import win32gui

        root_handle = win32gui.GetAncestor(win32gui.WindowFromPoint((x, y)), GA_ROOT)
        edits = desktop.window(handle=root_handle).descendants(control_type="Edit")
        prose_mirror_edits = [item for item in edits if "ProseMirror" in str(item.element_info.class_name)]
        for candidate in prose_mirror_edits:
            rect = candidate.element_info.rectangle
            if rect.left <= x <= rect.right and rect.top <= y <= rect.bottom:
                return _value_from_edit(candidate)
        if len(prose_mirror_edits) == 1:
            return _value_from_edit(prose_mirror_edits[0])
    except Exception:
        return None
    return None


class AutoResumer:
    """只有已校准、唯一窗口且已确认输入框为空时才会提交恢复文案。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def attempt(self) -> AutoResumeResult:
        if not self.settings.is_calibrated:
            return AutoResumeResult("skipped", "未完成输入框校准")
        target = WindowLocator.find(self.settings.window_title)
        if not target:
            return AutoResumeResult("skipped", "未找到唯一的目标 VS Code 窗口")
        if not WindowLocator.activate(target):
            return AutoResumeResult("skipped", "目标 VS Code 窗口不可操作")
        try:
            _open_codex_sidebar()
            # Opening the sidebar can change the UI tree. Re-fetch both the
            # window rectangle and the actual compose editor before clicking.
            target = WindowLocator.find(self.settings.window_title) or target
            input_box = _find_codex_input(target)
            if input_box:
                point, draft = input_box
            else:
                point = _relative_point(self.settings, target)
                draft = _read_input_value(*point)
            if draft is None:
                return AutoResumeResult("skipped", "无法安全读取 Codex 输入框")
            if draft.strip():
                return AutoResumeResult("skipped", "Codex 输入框存在草稿")
            ctypes.windll.user32.SetCursorPos(*point)
            ctypes.windll.user32.mouse_event(0x0002, 0, 0, 0, 0)
            ctypes.windll.user32.mouse_event(0x0004, 0, 0, 0, 0)
            time.sleep(0.1)
            _send_unicode(str(self.settings.data["resume_message"]))
            _press_virtual_key(VK_RETURN)
            return AutoResumeResult("dispatched", "已发送继续，等待同线程日志确认")
        except Exception as error:
            return AutoResumeResult("skipped", f"自动恢复未派发：{type(error).__name__}")


def calibrate_input_box(settings: Settings) -> AutoResumeResult:
    """等待用户下一次鼠标左键释放，将该位置保存为固定工作区的输入框坐标。"""

    try:
        import win32api
        import win32gui
    except ImportError:
        return AutoResumeResult("skipped", "缺少 pywin32，无法校准")

    try:
        import tkinter.messagebox as messagebox

        messagebox.showinfo("Codex 输入框校准", "关闭此提示后，请在目标 Codex 输入框中点击一次。工具会记录该位置。")
    except Exception:
        pass

    deadline = time.time() + 30
    pressed = False
    while time.time() < deadline:
        is_pressed = bool(win32api.GetAsyncKeyState(VK_LBUTTON) & 0x8000)
        if is_pressed:
            pressed = True
        elif pressed:
            x, y = win32api.GetCursorPos()
            handle = win32gui.GetAncestor(win32gui.WindowFromPoint((x, y)), GA_ROOT)
            title = win32gui.GetWindowText(handle)
            if "visual studio code" not in title.lower():
                return AutoResumeResult("skipped", "校准点不属于 VS Code 窗口")
            settings.update_calibration(title, x, y, tuple(win32gui.GetWindowRect(handle)))
            return AutoResumeResult("calibrated", "输入框位置已保存")
        time.sleep(0.05)
    return AutoResumeResult("skipped", "校准超时")
