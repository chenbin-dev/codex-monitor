"""工具配置加载、校准数据保存与默认值管理。"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "log_db": "~/.codex/logs_2.sqlite",
    "codex_config": "~/.codex/config.toml",
    "poll_interval_sec": 2,
    "target_window": {
        "title_contains": "",
        "input_point": {
            "x": None,
            "y": None,
            "relative_x": None,
            "relative_y": None,
        },
    },
    "resume_message": "继续",
    "timing": {
        "initial_delay_sec": 30,
        "observe_sec": 30,
        "max_fast_attempts": 3,
        "long_retry_sec": 300,
    },
    "additional_recoverable_patterns": [],
}


def _deep_merge(defaults: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    """保留默认配置中新增的字段，同时接受用户已经保存的设置。"""

    merged = copy.deepcopy(defaults)
    for key, value in values.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class Settings:
    """封装 config.json；该文件只保存路径和 UI 校准信息。"""

    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self.path = path
        self.data = data

    @classmethod
    def load(cls, path: Path) -> "Settings":
        if path.exists():
            with path.open("r", encoding="utf-8") as file:
                loaded = json.load(file)
            if not isinstance(loaded, dict):
                raise ValueError("config.json 必须是 JSON 对象")
            data = _deep_merge(DEFAULT_CONFIG, loaded)
        else:
            data = copy.deepcopy(DEFAULT_CONFIG)
        settings = cls(path, data)
        if not path.exists():
            settings.save()
        return settings

    def save(self) -> None:
        """原子替换配置文件，避免运行中的读取看到半写入内容。"""

        temp_path = self.path.with_suffix(".json.tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(self.data, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temp_path, self.path)

    def expanded_path(self, key: str) -> Path:
        return Path(str(self.data[key])).expanduser()

    @property
    def timing(self) -> dict[str, int]:
        return self.data["timing"]

    @property
    def window_title(self) -> str:
        return str(self.data["target_window"]["title_contains"]).strip()

    @property
    def is_calibrated(self) -> bool:
        point = self.data["target_window"]["input_point"]
        return bool(self.window_title and point.get("relative_x") is not None and point.get("relative_y") is not None)

    def update_calibration(self, title: str, x: int, y: int, rect: tuple[int, int, int, int]) -> None:
        """将屏幕坐标保存为窗口相对坐标，窗口缩放后仍可定位输入框。"""

        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            raise ValueError("无法读取 VS Code 窗口尺寸")
        point = self.data["target_window"]["input_point"]
        point.update(
            {
                "x": x,
                "y": y,
                "relative_x": round((x - left) / width, 6),
                "relative_y": round((y - top) / height, 6),
            }
        )
        self.data["target_window"]["title_contains"] = title
        self.save()
