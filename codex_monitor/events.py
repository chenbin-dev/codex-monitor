"""监测模块之间共享的轻量数据结构。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LogRecord:
    """从 Codex 日志库读取的一条日志，正文不会被持久化到本工具历史。"""

    id: int
    ts: int
    level: str
    target: str
    body: str
    thread_id: str | None
    process_uuid: str | None


@dataclass(frozen=True)
class Classification:
    """日志分类结果。kind 为空表示与监测无关。"""

    category: str
    kind: str | None = None
    turn_id: str | None = None


@dataclass(frozen=True)
class AutoResumeResult:
    """一次 UI 自动化尝试的结果。"""

    outcome: str
    detail: str
