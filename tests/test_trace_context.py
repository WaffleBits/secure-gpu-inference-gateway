import unittest

from gateway.trace_context import format_traceparent, parse_traceparent, resolve_trace_context


class TraceContextTest(unittest.TestCase):
    def test_accepts_remote_traceparent_and_creates_child_span(self) -> None:
        trace = resolve_trace_context(
            "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
            span_id_factory=lambda: "1111111111111111",
        )

        self.assertEqual(trace.source, "remote")
        self.assertEqual(trace.trace_id, "4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertEqual(trace.parent_span_id, "00f067aa0ba902b7")
        self.assertTrue(trace.sampled)
        self.assertEqual(
            format_traceparent(trace),
            "00-4bf92f3577b34da6a3ce929d0e0e4736-1111111111111111-01",
        )

    def test_generates_trace_when_header_is_missing_or_invalid(self) -> None:
        trace = resolve_trace_context(
            "not-valid",
            trace_id_factory=lambda: "22222222222222222222222222222222",
            span_id_factory=lambda: "3333333333333333",
        )

        self.assertEqual(trace.source, "generated")
        self.assertEqual(trace.trace_id, "22222222222222222222222222222222")
        self.assertEqual(trace.span_id, "3333333333333333")
        self.assertIsNone(trace.parent_span_id)
        self.assertFalse(trace.sampled)

    def test_rejects_zero_trace_ids(self) -> None:
        self.assertIsNone(
            parse_traceparent(
                "00-00000000000000000000000000000000-00f067aa0ba902b7-01"
            )
        )


if __name__ == "__main__":
    unittest.main()
