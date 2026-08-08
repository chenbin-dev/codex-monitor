"""只对 Codex HTTP/流式模块发出的明确错误执行自动恢复。"""

from __future__ import annotations

import re

from .events import Classification, LogRecord


TURN_ID_PATTERNS = (
    re.compile(r'\bturn_id[=:]\s*["\']?([A-Za-z0-9_-]+)', re.IGNORECASE),
    re.compile(r'"turn_id"\s*:\s*"([A-Za-z0-9_-]+)"', re.IGNORECASE),
)

SOURCE_MARKERS = (
    "codex_http_client",
    "codex_api",
    "codex_core::responses",
    "codex_core::session::turn",
    "codex_core::stream",
    "codex_api::sse::responses",
    "codex_app_server::",
    "codex_app_server_transport::",
)


def extract_turn_id(body: str) -> str | None:
    for pattern in TURN_ID_PATTERNS:
        match = pattern.search(body)
        if match:
            return match.group(1)
    return None


def _is_codex_transport_record(record: LogRecord) -> bool:
    target = record.target.lower()
    return any(marker in target for marker in SOURCE_MARKERS)


def _has_status(text: str, status: int) -> bool:
    return bool(
        re.search(rf"\bstatus(?:\s+code)?\s*[=:]?\s*{status}\b", text)
        or re.search(rf"\b{status}\s+(?:bad gateway|service unavailable|gateway timeout|unauthorized|payment required|not found)\b", text)
    )


def classify(record: LogRecord, extra_recoverable_patterns: list[str] | None = None) -> Classification:
    """返回可恢复、不可恢复、成功或无关分类，避免裸数字误报。"""

    text = record.body.lower()
    turn_id = extract_turn_id(record.body)
    source_is_transport = _is_codex_transport_record(record)

    if source_is_transport and (
        "turn completed" in text
        or "response completed" in text
        or "completed successfully" in text
    ):
        return Classification("success", turn_id=turn_id)

    if not source_is_transport:
        return Classification("none", turn_id=turn_id)

    if (
        "authentication" in text
        or "unauthorized" in text
        or _has_status(text, 401)
        or "insufficient" in text
        or "quota" in text
        or _has_status(text, 402)
        or "model not found" in text
        or _has_status(text, 404)
        or "approval" in text
    ):
        return Classification("terminal", "terminal_service_error", turn_id)

    recoverable_rules = (
        ("rate_limited", "too many requests" in text or "rate limit" in text or _has_status(text, 429)),
        ("gateway_502", "502 bad gateway" in text or _has_status(text, 502)),
        ("gateway_503", "503 service unavailable" in text or _has_status(text, 503)),
        ("gateway_504", "504 gateway timeout" in text or _has_status(text, 504)),
        ("stream_disconnected", "stream disconnected" in text),
        ("model_capacity", "model is at capacity" in text),
        ("transport_timeout", "timed out" in text or "timeout" in text or "connection reset" in text),
    )
    for kind, matches in recoverable_rules:
        if matches:
            return Classification("recoverable", kind, turn_id)

    for pattern in extra_recoverable_patterns or []:
        if pattern and pattern.lower() in text:
            return Classification("recoverable", "custom_pattern", turn_id)
    return Classification("none", turn_id=turn_id)
