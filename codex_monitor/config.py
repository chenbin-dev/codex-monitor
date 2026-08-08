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
    "targets": [
        {
            "id": "vscode",
            "kind": "vscode",
            "enabled": True,
            "title_contains": "",
            "input_point": {
                "x": None,
                "y": None,
                "relative_x": None,
                "relative_y": None,
            },
        },
        {
            "id": "desktop",
            "kind": "desktop",
            "enabled": True,
            "protocol_enabled": True,
            "title_contains": "Codex",
            "input_point": {
                "x": None,
                "y": None,
                "relative_x": None,
                "relative_y": None,
            },
        },
        {
            "id": "cli",
            "kind": "cli",
            "enabled": True,
            "title_contains": "Codex CLI",
            "allow_blind_terminal_input": True,
            "protocol_enabled": True,
            "protocol_endpoint": "ws://127.0.0.1:8765",
        },
    ],
    "resume_message": "继续",
    "timing": {
        "initial_delay_sec": 30,
        "observe_sec": 30,
        "max_fast_attempts": 3,
        "long_retry_sec": 300,
    },
    "additional_recoverable_patterns": [],
}


def _migrate_legacy_target(data: dict[str, Any]) -> dict[str, Any]:
    """Convert the original single VS Code calibration to the target list."""

    if isinstance(data.get("targets"), list):
        normalized = copy.deepcopy(data)
        defaults = {str(target["id"]): target for target in DEFAULT_CONFIG["targets"]}
        configured = {
            str(target.get("id")): target
            for target in data["targets"]
            if isinstance(target, dict) and target.get("id")
        }
        normalized["targets"] = [
            _deep_merge(default_target, configured.get(target_id, {}))
            for target_id, default_target in defaults.items()
        ]
        return normalized
    legacy = data.get("target_window")
    if not isinstance(legacy, dict):
        return data
    migrated = copy.deepcopy(data)
    targets = copy.deepcopy(DEFAULT_CONFIG["targets"])
    targets[0]["title_contains"] = str(legacy.get("title_contains") or "")
    if isinstance(legacy.get("input_point"), dict):
        targets[0]["input_point"].update(legacy["input_point"])
    migrated["targets"] = targets
    return migrated


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
            migrated = _migrate_legacy_target(loaded)
            data = _deep_merge(DEFAULT_CONFIG, migrated)
        else:
            data = copy.deepcopy(DEFAULT_CONFIG)
        settings = cls(path, data)
        if not path.exists() or data != loaded:
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
        return str(self.target("vscode")["title_contains"]).strip()

    @property
    def is_calibrated(self) -> bool:
        point = self.target("vscode")["input_point"]
        return bool(self.window_title and point.get("relative_x") is not None and point.get("relative_y") is not None)

    @property
    def targets(self) -> list[dict[str, Any]]:
        return [target for target in self.data["targets"] if isinstance(target, dict)]

    def target(self, target_id: str) -> dict[str, Any]:
        for target in self.targets:
            if target.get("id") == target_id:
                return target
        raise KeyError(f"未知目标: {target_id}")

    def enabled_targets(self) -> list[dict[str, Any]]:
        return [target for target in self.targets if bool(target.get("enabled", True))]

    def is_target_calibrated(self, target_id: str) -> bool:
        target = self.target(target_id)
        point = target.get("input_point")
        return bool(
            target.get("title_contains")
            and isinstance(point, dict)
            and point.get("relative_x") is not None
            and point.get("relative_y") is not None
        )

    def update_calibration(
        self, title: str, x: int, y: int, rect: tuple[int, int, int, int], target_id: str = "vscode"
    ) -> None:
        """将屏幕坐标保存为窗口相对坐标，窗口缩放后仍可定位输入框。"""

        left, top, right, bottom = rect
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            raise ValueError("无法读取 VS Code 窗口尺寸")
        target = self.target(target_id)
        point = target["input_point"]
        point.update(
            {
                "x": x,
                "y": y,
                "relative_x": round((x - left) / width, 6),
                "relative_y": round((y - top) / height, 6),
            }
        )
        target["title_contains"] = title
        self.save()
