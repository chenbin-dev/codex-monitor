import sqlite3
import time
import tempfile
import unittest
from pathlib import Path

from codex_monitor.history import HistoryStore
from codex_monitor.log_watcher import CodexLogWatcher


class HistoryAndWatcherTests(unittest.TestCase):
    def test_incident_transitions_to_long_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            tmp_path = Path(temporary_directory)
            store = HistoryStore(tmp_path / "history.db")
            incident_id = store.create_or_refresh_incident(
                process_uuid="process", thread_id="thread", turn_id="turn", error_kind="gateway_503", log_id=1, initial_delay_sec=0
            )
            self.assertTrue(store.due_incidents())
            for _ in range(3):
                store.mark_dispatched(incident_id, observe_sec=0)
                self.assertIn(store.advance_after_observation(incident_id, max_fast_attempts=3, long_retry_sec=300), {"waiting", "long_wait"})
            row = next(item for item in store.due_incidents(time.time() + 301) if item["incident_id"] == incident_id)
            self.assertEqual(row["phase"], "long_wait")

    def test_new_incident_for_same_turn_after_cancellation_has_a_new_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = HistoryStore(Path(temporary_directory) / "history.db")
            first_id = store.create_or_refresh_incident(
                process_uuid="process", thread_id="thread", turn_id="turn", error_kind="gateway_503", log_id=1, initial_delay_sec=0
            )
            store.cancel_thread("process", "thread", "terminal error")
            second_id = store.create_or_refresh_incident(
                process_uuid="process", thread_id="thread", turn_id="turn", error_kind="gateway_503", log_id=2, initial_delay_sec=0
            )
            self.assertNotEqual(first_id, second_id)

    def test_records_without_turn_id_refresh_the_same_active_thread_incident(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = HistoryStore(Path(temporary_directory) / "history.db")
            first_id = store.create_or_refresh_incident(
                process_uuid="process", thread_id="thread", turn_id="turn", error_kind="gateway_503", log_id=1, initial_delay_sec=0
            )
            refreshed_id = store.create_or_refresh_incident(
                process_uuid="process", thread_id="thread", turn_id=None, error_kind="gateway_503", log_id=2, initial_delay_sec=0
            )
            self.assertEqual(first_id, refreshed_id)
            self.assertEqual(len(store.due_incidents()), 1)

    def test_unrelated_completion_does_not_create_history_noise(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            store = HistoryStore(Path(temporary_directory) / "history.db")
            store.resolve_thread("process", "thread", "同线程完成")
            self.assertEqual(list(store.recent_events()), [])


    def test_watcher_initializes_at_tail_then_reads_new_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            tmp_path = Path(temporary_directory)
            log_db = tmp_path / "logs.sqlite"
            connection = sqlite3.connect(log_db)
            try:
                connection.execute(
                    "CREATE TABLE logs(id INTEGER PRIMARY KEY, ts INTEGER, level TEXT, target TEXT, feedback_log_body TEXT, thread_id TEXT, process_uuid TEXT)"
                )
                connection.execute("INSERT INTO logs VALUES(1, 0, 'TRACE', 'codex_http_client::client', 'old', 't', 'p')")
                connection.commit()
            finally:
                connection.close()
            store = HistoryStore(tmp_path / "history.db")
            watcher = CodexLogWatcher(log_db, store)
            self.assertEqual(watcher.poll(), [])
            connection = sqlite3.connect(log_db)
            try:
                connection.execute("INSERT INTO logs VALUES(2, 0, 'TRACE', 'codex_http_client::client', 'new', 't', 'p')")
                connection.commit()
            finally:
                connection.close()
            records = watcher.poll()
            self.assertEqual([record.id for record in records], [2])
