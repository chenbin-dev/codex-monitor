"""本工具的历史与恢复状态持久化。"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator


ACTIVE_PHASES = ("waiting", "observing", "long_wait")


class HistoryStore:
    """使用独立 SQLite 保存游标、事件摘要和跨重启的重试状态。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """提交后显式关闭连接，避免 Windows 长时间占用 history.db。"""

        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    thread_id TEXT,
                    process_uuid TEXT,
                    log_id INTEGER
                );
                CREATE TABLE IF NOT EXISTS incidents (
                    incident_id TEXT PRIMARY KEY,
                    process_uuid TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    turn_id TEXT,
                    error_kind TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error_id INTEGER NOT NULL,
                    next_action_at REAL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_incidents_due
                    ON incidents(phase, next_action_at);
                """
            )

    def get_cursor(self) -> int | None:
        with self._connect() as connection:
            row = connection.execute("SELECT value FROM state WHERE key = 'log_cursor'").fetchone()
        return int(row["value"]) if row else None

    def set_cursor(self, cursor: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO state(key, value) VALUES('log_cursor', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(cursor),),
            )

    def record_event(
        self,
        event_type: str,
        detail: str,
        *,
        thread_id: str | None = None,
        process_uuid: str | None = None,
        log_id: int | None = None,
    ) -> None:
        """只保留分类摘要，避免将 Codex 日志正文和敏感提示词复制到历史库。"""

        with self._connect() as connection:
            connection.execute(
                "INSERT INTO events(created_at, event_type, detail, thread_id, process_uuid, log_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (time.time(), event_type, detail, thread_id, process_uuid, log_id),
            )

    @staticmethod
    def _incident_id(process_uuid: str, thread_id: str, turn_id: str | None, log_id: int) -> str:
        # A new error can arrive for a turn after its previous incident closed.
        # Keep the primary key unique while active incidents are still refreshed.
        raw = f"{process_uuid}|{thread_id}|{turn_id or ''}|{log_id}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:24]

    def _find_active(self, connection: sqlite3.Connection, process_uuid: str, thread_id: str) -> sqlite3.Row | None:
        placeholders = ",".join("?" for _ in ACTIVE_PHASES)
        query = (
            "SELECT * FROM incidents WHERE process_uuid = ? AND thread_id = ? "
            f"AND phase IN ({placeholders}) "
            "ORDER BY updated_at DESC LIMIT 1"
        )
        return connection.execute(query, (process_uuid, thread_id, *ACTIVE_PHASES)).fetchone()

    def create_or_refresh_incident(
        self,
        *,
        process_uuid: str,
        thread_id: str,
        turn_id: str | None,
        error_kind: str,
        log_id: int,
        initial_delay_sec: int,
    ) -> str:
        """同一会话的新错误会重置静默等待窗口，避免抢在 Codex 自重试前发送。"""

        now = time.time()
        with self._connect() as connection:
            # A single backend failure is often logged twice: one record has a
            # turn id and the transport record does not. Keep one recovery task
            # per Codex thread until that task is resolved or cancelled.
            existing = self._find_active(connection, process_uuid, thread_id)
            if existing:
                incident_id = existing["incident_id"]
                connection.execute(
                    "UPDATE incidents SET error_kind = ?, last_error_id = ?, phase = 'waiting', "
                    "next_action_at = ?, updated_at = ? WHERE incident_id = ?",
                    (error_kind, log_id, now + initial_delay_sec, now, incident_id),
                )
                return str(incident_id)

            incident_id = self._incident_id(process_uuid, thread_id, turn_id, log_id)
            connection.execute(
                "INSERT INTO incidents(incident_id, process_uuid, thread_id, turn_id, error_kind, phase, "
                "attempts, last_error_id, next_action_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'waiting', 0, ?, ?, ?, ?)",
                (incident_id, process_uuid, thread_id, turn_id, error_kind, log_id, now + initial_delay_sec, now, now),
            )
            return incident_id

    def resolve_thread(self, process_uuid: str | None, thread_id: str | None, detail: str) -> None:
        if not thread_id:
            return
        now = time.time()
        changed = 0
        with self._connect() as connection:
            conditions = "thread_id = ?"
            params: list[Any] = [thread_id]
            if process_uuid:
                conditions += " AND process_uuid = ?"
                params.append(process_uuid)
            placeholders = ",".join("?" for _ in ACTIVE_PHASES)
            cursor = connection.execute(
                f"UPDATE incidents SET phase = 'resolved', next_action_at = NULL, updated_at = ? "
                f"WHERE {conditions} AND phase IN ({placeholders})",
                (now, *params, *ACTIVE_PHASES),
            )
            changed = cursor.rowcount
        if changed:
            self.record_event("recovered", detail, thread_id=thread_id, process_uuid=process_uuid)

    def cancel_thread(self, process_uuid: str | None, thread_id: str | None, detail: str) -> None:
        if not thread_id:
            return
        now = time.time()
        changed = 0
        with self._connect() as connection:
            conditions = "thread_id = ?"
            params: list[Any] = [thread_id]
            if process_uuid:
                conditions += " AND process_uuid = ?"
                params.append(process_uuid)
            placeholders = ",".join("?" for _ in ACTIVE_PHASES)
            cursor = connection.execute(
                f"UPDATE incidents SET phase = 'cancelled', next_action_at = NULL, updated_at = ? "
                f"WHERE {conditions} AND phase IN ({placeholders})",
                (now, *params, *ACTIVE_PHASES),
            )
            changed = cursor.rowcount
        if changed:
            self.record_event("terminal", detail, thread_id=thread_id, process_uuid=process_uuid)

    def due_incidents(self, now: float | None = None) -> list[dict[str, Any]]:
        now = time.time() if now is None else now
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM incidents WHERE phase IN ('waiting', 'observing', 'long_wait') "
                "AND next_action_at <= ? ORDER BY next_action_at ASC",
                (now,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_dispatched(self, incident_id: str, observe_sec: int) -> int:
        """只有真正提交了“继续”才增加尝试次数。"""

        now = time.time()
        with self._connect() as connection:
            connection.execute(
                "UPDATE incidents SET attempts = attempts + 1, phase = 'observing', "
                "next_action_at = ?, updated_at = ? WHERE incident_id = ?",
                (now + observe_sec, now, incident_id),
            )
            attempts = connection.execute(
                "SELECT attempts FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()["attempts"]
        return int(attempts)

    def advance_after_observation(self, incident_id: str, max_fast_attempts: int, long_retry_sec: int) -> str:
        """观察窗口未见恢复时，按用户确认的 3 次快速重试再转五分钟持续重试。"""

        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempts FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            if not row:
                return "missing"
            phase = "waiting" if row["attempts"] < max_fast_attempts else "long_wait"
            next_action_at = now if phase == "waiting" else now + long_retry_sec
            connection.execute(
                "UPDATE incidents SET phase = ?, next_action_at = ?, updated_at = ? WHERE incident_id = ?",
                (phase, next_action_at, now, incident_id),
            )
        return phase

    def mark_skipped(self, incident_id: str, detail: str) -> None:
        """窗口、输入框或草稿条件不满足时结束本次事件，不进入重试。"""

        now = time.time()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT thread_id, process_uuid FROM incidents WHERE incident_id = ?", (incident_id,)
            ).fetchone()
            connection.execute(
                "UPDATE incidents SET phase = 'skipped', next_action_at = NULL, updated_at = ? WHERE incident_id = ?",
                (now, incident_id),
            )
        if row:
            self.record_event("skipped", detail, thread_id=row["thread_id"], process_uuid=row["process_uuid"])

    def recent_events(self, limit: int = 80) -> Iterable[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT created_at, event_type, detail, thread_id, process_uuid, log_id "
                "FROM events ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
