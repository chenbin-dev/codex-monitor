"""应用入口和 Windows 托盘菜单。"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from .auto_resume import calibrate_input_box
from .config import Settings
from .history import HistoryStore
from .history_window import show_history_async
from .service import MonitorService


class TrayApplication:
    """将后台服务绑定到托盘生命周期，退出时确保线程停止。"""

    def __init__(self, project_dir: Path) -> None:
        self.settings = Settings.load(project_dir / "config.json")
        self.store = HistoryStore(project_dir / "history.db")
        self.service = MonitorService(self.settings, self.store)
        self.icon = None

    def _calibrate(self, *_: object) -> None:
        def worker() -> None:
            result = calibrate_input_box(self.settings)
            self.store.record_event(result.outcome, result.detail)

        threading.Thread(target=worker, name="codex-calibration", daemon=True).start()

    def _toggle_pause(self, *_: object) -> None:
        paused = self.service.toggle_pause()
        self.store.record_event("paused" if paused else "resumed", "监测已暂停" if paused else "监测已恢复")

    def _show_history(self, *_: object) -> None:
        show_history_async(self.store)

    def _exit(self, *_: object) -> None:
        self.service.stop()
        if self.icon:
            self.icon.stop()

    def run(self) -> None:
        try:
            import pystray
            from PIL import Image, ImageDraw
        except ImportError as error:
            raise RuntimeError("请先按 README 安装 requirements.txt 中的依赖") from error

        image = Image.new("RGBA", (64, 64), (30, 36, 46, 255))
        draw = ImageDraw.Draw(image)
        draw.ellipse((12, 12, 52, 52), fill=(57, 196, 140, 255))
        draw.rectangle((28, 21, 36, 43), fill=(255, 255, 255, 255))
        menu = pystray.Menu(
            pystray.MenuItem(lambda _: f"状态：{self.service.status_text()}", None, enabled=False),
            pystray.MenuItem("校准 Codex 输入框", self._calibrate),
            pystray.MenuItem("查看历史", self._show_history),
            pystray.MenuItem(lambda _: "恢复监测" if self.service.paused else "暂停监测", self._toggle_pause),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._exit),
        )
        self.icon = pystray.Icon("codex_monitor", image, "Codex 健康监测", menu)
        self.service.start()
        self.icon.run()


def main() -> int:
    project_dir = Path(__file__).resolve().parent.parent
    app = TrayApplication(project_dir)
    try:
        app.run()
    except KeyboardInterrupt:
        app.service.stop()
    except Exception as error:
        print(f"启动失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
