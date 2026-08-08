import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from codex_monitor.config import Settings
from codex_monitor.events import AutoResumeResult, LogRecord
from codex_monitor.history import HistoryStore
from codex_monitor.cli_protocol import _recoverable_kind
from codex_monitor.recovery import CliAdapter, CliProcess, DesktopAdapter, RecoveryRegistry, launch_managed_cli
from codex_monitor.service import MonitorService


class StubAdapter:
    def __init__(self, available: bool, result: AutoResumeResult | None = None) -> None:
        self.available = available
        self.result = result or AutoResumeResult("dispatched", "ok")
        self.calls = 0

    def is_available(self) -> bool:
        return self.available

    def attempt(self, thread_id: str | None = None) -> AutoResumeResult:
        del thread_id
        self.calls += 1
        return self.result


def record(target: str = "codex_api::sse::responses") -> LogRecord:
    return LogRecord(10, 0, "ERROR", target, "503 Service Unavailable", "thread-1", "process-1")


class MultiTargetRecoveryTests(unittest.TestCase):
    def settings(self, path: Path) -> Settings:
        return Settings.load(path / "config.json")

    def test_legacy_config_is_migrated_to_vscode_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"target_window": {"title_contains": "workspace", "input_point": {"relative_x": 0.2, "relative_y": 0.8}}}),
                encoding="utf-8",
            )
            settings = Settings.load(path)
            self.assertEqual(settings.target("vscode")["title_contains"], "workspace")
            self.assertTrue(settings.is_target_calibrated("vscode"))
            self.assertEqual({target["id"] for target in settings.targets}, {"vscode", "desktop", "cli"})
            self.assertIn('"targets"', path.read_text(encoding="utf-8"))

    def test_registry_requires_exactly_one_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            only_cli = RecoveryRegistry(settings, {"cli": StubAdapter(True), "vscode": StubAdapter(False)})
            self.assertEqual(only_cli.select(record()), "cli")
            ambiguous = RecoveryRegistry(settings, {"cli": StubAdapter(True), "vscode": StubAdapter(True)})
            self.assertIsNone(ambiguous.select(record()))

    def test_cli_retry_source_prefers_cli_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            only_cli = RecoveryRegistry(settings, {"cli": StubAdapter(True), "vscode": StubAdapter(True)})
            self.assertEqual(only_cli.select(LogRecord(1, 0, "ERROR", "codex_core::responses_retry", "503 Service Unavailable", None, "process")), "cli")

    def test_cli_never_dispatches_when_multiple_processes_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            adapter = CliAdapter(settings, lambda: [CliProcess(1, "codex"), CliProcess(2, "codex")])
            self.assertFalse(adapter.is_available())
            self.assertEqual(adapter.attempt().outcome, "skipped")

    def test_protocol_cli_dispatches_to_the_reported_thread_without_window_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            bridge = Mock()
            bridge.is_available.return_value = True
            bridge.dispatch.return_value = AutoResumeResult("dispatched", "protocol")
            adapter = CliAdapter(settings, lambda: [], bridge)
            result = adapter.attempt("thread-503")
            self.assertEqual(result.outcome, "dispatched")
            bridge.dispatch.assert_called_once_with("thread-503", settings.data["resume_message"])

    def test_protocol_turn_error_classifies_only_transient_failures(self) -> None:
        self.assertEqual(
            _recoverable_kind({"status": "failed", "error": {"codexErrorInfo": {"httpConnectionFailed": {"httpStatusCode": 503}}}}),
            "gateway_503",
        )
        self.assertIsNone(_recoverable_kind({"status": "failed", "error": {"codexErrorInfo": "unauthorized"}}))

    @patch("codex_monitor.recovery._send_unicode")
    @patch("codex_monitor.recovery._press_virtual_key")
    @patch("codex_monitor.recovery.WindowLocator.activate", return_value=True)
    @patch("codex_monitor.recovery._find_terminal_window")
    def test_unique_cli_dispatches_once(self, find_window: Mock, *_: Mock) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            find_window.return_value = Mock()
            adapter = CliAdapter(settings, lambda: [CliProcess(1, "codex")])
            result = adapter.attempt()
            self.assertEqual(result.outcome, "dispatched")

    def test_desktop_uses_ui_when_protocol_has_no_resume_rpc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            bridge = Mock()
            bridge.is_available.return_value = True
            bridge.dispatch.return_value = None
            adapter = DesktopAdapter(settings, bridge)
            adapter.ui = StubAdapter(True, AutoResumeResult("dispatched", "ui fallback"))
            result = adapter.attempt()
            self.assertEqual(result.detail, "ui fallback")
            self.assertEqual(adapter.ui.calls, 1)

    def test_desktop_uses_protocol_when_thread_dispatch_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings = self.settings(Path(directory))
            bridge = Mock()
            bridge.is_available.return_value = True
            bridge.dispatch.return_value = AutoResumeResult("dispatched", "protocol")
            adapter = DesktopAdapter(settings, bridge)
            adapter.ui = StubAdapter(True)
            result = adapter.attempt("thread-1")
            self.assertEqual(result.detail, "protocol")
            bridge.dispatch.assert_called_once_with("thread-1", settings.data["resume_message"])
            self.assertEqual(adapter.ui.calls, 0)

    def test_service_records_unattributed_error_without_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            settings = self.settings(path)
            store = HistoryStore(path / "history.db")
            service = MonitorService(settings, store)
            registry = Mock()
            registry.select.return_value = None
            service.registry = registry
            service._handle_record(record())
            self.assertEqual(store.due_incidents(), [])
            event_types = [event["event_type"] for event in store.recent_events()]
            self.assertIn("recoverable_error", event_types)
            self.assertIn("history_only", event_types)

    def test_service_persists_routed_target_on_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            settings = self.settings(path)
            settings.data["timing"]["initial_delay_sec"] = 0
            settings.data["timing"]["observe_sec"] = 30
            store = HistoryStore(path / "history.db")
            service = MonitorService(settings, store)
            registry = Mock()
            registry.select.return_value = "cli"
            registry.attempt.return_value = AutoResumeResult("dispatched", "sent")
            service.registry = registry
            service._handle_record(record())
            service._process_due_incidents()
            event = next(item for item in store.recent_events() if item["event_type"] == "continue_dispatched")
            self.assertEqual(event["target_id"], "cli")

    def test_simulation_uses_the_same_target_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            settings = self.settings(path)
            store = HistoryStore(path / "history.db")
            service = MonitorService(settings, store)
            registry = Mock()
            registry.attempt.return_value = AutoResumeResult("dispatched", "sent")
            service.registry = registry
            service.simulate_recovery("desktop")
            registry.attempt.assert_called_once_with("desktop")
            event = next(item for item in store.recent_events() if item["event_type"] == "simulation_recovery")
            self.assertEqual(event["target_id"], "desktop")

    @patch("codex_monitor.recovery.subprocess.Popen")
    @patch("codex_monitor.recovery.Path.home")
    @patch("codex_monitor.recovery.Path.exists", return_value=True)
    def test_managed_cli_passes_wrapper_as_a_separate_cmd_argument(self, _exists: Mock, home: Mock, popen: Mock) -> None:
        home.return_value = Path("C:/Users/tester")
        with patch.dict("os.environ", {"LOCALAPPDATA": "C:/Users/tester/AppData/Local"}):
            result = launch_managed_cli()
        self.assertEqual(result.outcome, "launched")
        command = popen.call_args.args[0]
        self.assertEqual(command[:3], ["cmd.exe", "/d", "/k"])
        self.assertEqual(command[3], str(Path("C:/Users/tester/AppData/Local/CodexMonitor/bin/codex.cmd")))


if __name__ == "__main__":
    unittest.main()
