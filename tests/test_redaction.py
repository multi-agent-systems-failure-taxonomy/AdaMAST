"""Trace redaction helper tests."""

import unittest

from adamast.core.redaction import REDACTION, redact_text, redact_trace
from adamast.core.traces import GenerationTrace


class RedactionTests(unittest.TestCase):
    def test_redact_text_masks_common_secret_shapes(self):
        text = (
            "Authorization: Bearer abcdefghijklmnopqrstuvwxyz\n"
            "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwxyz123456\n"
            "Cookie: sessionid=secret-value"
        )
        redacted = redact_text(text)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz123456", redacted)
        self.assertNotIn("sessionid=secret-value", redacted)
        self.assertGreaterEqual(redacted.count(REDACTION), 3)

    def test_extra_pattern_can_mask_project_specific_values(self):
        redacted = redact_text(
            "customer id: cust_12345",
            extra_patterns=[r"cust_\d+"],
        )
        self.assertEqual(redacted, "customer id: [REDACTED]")

    def test_redact_trace_copies_task_raw_trajectory_and_metadata(self):
        trace = GenerationTrace(
            problem_id="p1",
            task="use access_token=secret-task-token",
            raw_trajectory="called with ghp_abcdefghijklmnopqrstuvwxyz123456",
            metadata={
                "nested": ["Bearer abcdefghijklmnopqrstuvwxyz"],
                "safe": 3,
            },
        )
        redacted = redact_trace(trace)
        self.assertEqual(trace.problem_id, redacted.problem_id)
        self.assertNotIn("secret-task-token", redacted.task)
        self.assertNotIn("ghp_", redacted.raw_trajectory)
        self.assertEqual(redacted.metadata["safe"], 3)
        self.assertIn(REDACTION, redacted.metadata["nested"][0])


if __name__ == "__main__":
    unittest.main()
