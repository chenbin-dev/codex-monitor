import unittest

from codex_monitor.classifier import classify, extract_thread_id
from codex_monitor.events import LogRecord


def record(body: str, target: str = "codex_http_client::client") -> LogRecord:
    return LogRecord(1, 0, "TRACE", target, body, "thread-1", "process-1")


class ClassifierTests(unittest.TestCase):
    def test_retryable_gateway_error_requires_codex_source(self) -> None:
        self.assertEqual(classify(record("503 Service Unavailable")).kind, "gateway_503")
        self.assertEqual(classify(record("503 Service Unavailable", "unrelated::logger")).category, "none")

    def test_terminal_error_never_enters_auto_recovery(self) -> None:
        result = classify(record("status=401 authentication failed"))
        self.assertEqual(result.category, "terminal")

    def test_stream_error_extracts_turn_id(self) -> None:
        result = classify(record('stream disconnected turn_id="turn-123"'))
        self.assertEqual(result.category, "recoverable")
        self.assertEqual(result.kind, "stream_disconnected")
        self.assertEqual(result.turn_id, "turn-123")

    def test_app_server_and_sse_sources_are_recoverable(self) -> None:
        self.assertEqual(classify(record("503 Service Unavailable", "codex_api::sse::responses")).category, "recoverable")
        self.assertEqual(classify(record("timeout", "codex_app_server::message_processor")).category, "recoverable")

    def test_cli_retry_log_with_503_is_recoverable(self) -> None:
        result = classify(
            record(
                "stream disconnected - retrying sampling request "
                "unexpected status 503 Service Unavailable",
                "codex_core::responses_retry",
            )
        )
        self.assertEqual(result.category, "recoverable")
        self.assertEqual(result.kind, "gateway_503")

    def test_cli_retry_log_extracts_thread_id_from_body(self) -> None:
        self.assertEqual(extract_thread_id("thread.id=019abc123 stream disconnected"), "019abc123")
