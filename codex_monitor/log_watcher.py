"""以只读方式增量读取 Codex 的 WAL 日志库。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from urllib.parse import quote

from .events import LogRecord
from .history import HistoryStore


class CodexLogWatcher:
    """游标存放在工具历史库，首次启动从当前末尾开始，避免处理陈旧故障。"""

    def __init__(self, log_db: Path, store: HistoryStore, batch_size: int = 500) -> None:
        self.log_db = log_db
        self.store = store
        self.batch_size = batch_size

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        uri = f"file:{quote(self.log_db.as_posix())}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            connection.execute("PRAGMA query_only = ON")
            yield connection
        finally:
            connection.close()

    def poll(self) -> list[LogRecord]:
        if not self.log_db.exists():
            return []
        with self._connect() as connection:
            maximum = connection.execute("SELECT MAX(id) FROM logs").fetchone()[0]
            if maximum is None:
                return []
            cursor = self.store.get_cursor()
            if cursor is None or maximum < cursor:
                self.store.set_cursor(int(maximum))
                return []
            rows = connection.execute(
                "SELECT id, ts, level, target, feedback_log_body, thread_id, process_uuid "
                "FROM logs WHERE id > ? ORDER BY id ASC LIMIT ?",
                (cursor, self.batch_size),
            ).fetchall()
        records = [
            LogRecord(
                id=int(row[0]),
                ts=int(row[1] or 0),
                level=str(row[2] or ""),
                target=str(row[3] or ""),
                body=str(row[4] or ""),
                thread_id=str(row[5]) if row[5] else None,
                process_uuid=str(row[6]) if row[6] else None,
            )
            for row in rows
        ]
        if records:
            self.store.set_cursor(records[-1].id)
        return records
