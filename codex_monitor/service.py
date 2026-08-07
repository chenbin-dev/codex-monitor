"""协调日志监听、事件状态机与自动恢复动作。"""

from __future__ import annotations

import threading
import time

from .auto_resume import AutoResumer
from .classifier import classify
from .config import Settings
from .history import HistoryStore
from .log_watcher import CodexLogWatcher


class MonitorService:
    """后台单线程状态机，避免同一会话的恢复动作并发执行。"""

    def __init__(self, settings: Settings, store: HistoryStore) -> None:
        self.settings = settings
        self.store = store
        self.watcher = CodexLogWatcher(settings.expanded_path("log_db"), store)
        self.resumer = AutoResumer(settings)
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_error: str | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="codex-monitor", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def toggle_pause(self) -> bool:
        if self._paused.is_set():
            self._paused.clear()
        else:
            self._paused.set()
        return self._paused.is_set()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def status_text(self) -> str:
        if self.paused:
            return "已暂停"
        active = len(self.store.due_incidents(time.time() + 365 * 24 * 3600))
        suffix = f"，待处理会话 {active}" if active else "，正常监听"
        return f"运行中{suffix}"

    def _handle_record(self, record: object) -> None:
        classification = classify(record, self.settings.data["additional_recoverable_patterns"])
        process_uuid = getattr(record, "process_uuid")
        thread_id = getattr(record, "thread_id")
        if classification.category == "success":
            self.store.resolve_thread(process_uuid, thread_id, "同线程出现完成日志")
            return
        if classification.category == "terminal":
            self.store.cancel_thread(process_uuid, thread_id, classification.kind or "不可恢复错误")
            return
        if classification.category != "recoverable":
            return

        self.store.record_event(
            "recoverable_error",
            classification.kind or "可恢复异常",
            thread_id=thread_id,
            process_uuid=process_uuid,
            log_id=getattr(record, "id"),
        )
        # 无 thread_id 的日志不能可靠映射到 VS Code 对话，因此只留历史。
        if not thread_id:
            self.store.record_event("history_only", "异常日志缺少 thread_id，未自动恢复", log_id=getattr(record, "id"))
            return
        self.store.create_or_refresh_incident(
            process_uuid=process_uuid or "unknown",
            thread_id=thread_id,
            turn_id=classification.turn_id,
            error_kind=classification.kind or "recoverable_error",
            log_id=getattr(record, "id"),
            initial_delay_sec=int(self.settings.timing["initial_delay_sec"]),
        )

    def _process_due_incidents(self) -> None:
        timing = self.settings.timing
        for incident in self.store.due_incidents():
            incident_id = incident["incident_id"]
            if incident["phase"] == "observing":
                next_phase = self.store.advance_after_observation(
                    incident_id,
                    int(timing["max_fast_attempts"]),
                    int(timing["long_retry_sec"]),
                )
                self.store.record_event(
                    "not_recovered",
                    "观察窗口未见同线程恢复，转入" + ("五分钟持续重试" if next_phase == "long_wait" else "下一次快速重试"),
                    thread_id=incident["thread_id"],
                    process_uuid=incident["process_uuid"],
                )
                continue

            result = self.resumer.attempt()
            if result.outcome == "dispatched":
                attempt = self.store.mark_dispatched(incident_id, int(timing["observe_sec"]))
                self.store.record_event(
                    "continue_dispatched",
                    f"{result.detail}（第 {attempt} 次实际发送）",
                    thread_id=incident["thread_id"],
                    process_uuid=incident["process_uuid"],
                )
            else:
                self.store.mark_skipped(incident_id, result.detail)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                if not self.paused:
                    for record in self.watcher.poll():
                        self._handle_record(record)
                    self._process_due_incidents()
                self._last_error = None
            except Exception as error:
                self._last_error = f"{type(error).__name__}: {error}"
                self.store.record_event("monitor_error", self._last_error)
            self._stop.wait(float(self.settings.data["poll_interval_sec"]))
