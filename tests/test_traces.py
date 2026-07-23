"""Generation-trace storage and retention tests."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from adamast.core.traces import (
    GenerationTrace,
    RetentionPolicy,
    TraceReadError,
    TraceStore,
)

FIXTURE = Path(__file__).parent / "fixtures" / "adamast_generation_trace.json"


def real_trace() -> GenerationTrace:
    return GenerationTrace.from_dict(
        json.loads(FIXTURE.read_text(encoding="utf-8"))
    )


class GenerationTraceTests(unittest.TestCase):
    def test_real_fixture_is_exact_generation_shape(self):
        trace = real_trace()
        self.assertEqual(
            set(trace.to_dict()),
            {"problem_id", "task", "raw_trajectory", "metadata"},
        )
        self.assertIsInstance(trace.raw_trajectory, str)

    def test_extra_runtime_fields_are_rejected(self):
        record = real_trace().to_dict()
        record["gate_status"] = "READY_TO_SUBMIT"
        with self.assertRaises(ValueError):
            GenerationTrace.from_dict(record)


class TraceStoreTests(unittest.TestCase):
    def test_invalid_trace_is_reported_instead_of_silently_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            store = TraceStore(td)
            store.root.mkdir(parents=True, exist_ok=True)
            broken = store.root / "trace-broken.json"
            broken.write_text("{not-json", encoding="utf-8")

            with self.assertRaisesRegex(TraceReadError, "trace-broken.json"):
                list(store.iter_traces())

    def test_append_creates_independent_json_records(self):
        with tempfile.TemporaryDirectory() as td:
            store = TraceStore(td)
            self.assertEqual(store.append_many([real_trace(), real_trace()]), 2)
            self.assertEqual(len(list(Path(td).glob("trace-*.json"))), 2)
            self.assertEqual(list(store.iter_traces()), [real_trace(), real_trace()])

    def test_integration_copies_verifies_then_removes_source(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as dest:
            pending = TraceStore(source)
            approved = TraceStore(dest)
            pending.append_many([real_trace()])
            self.assertEqual(pending.integrate_into(approved), 1)
            self.assertEqual(pending.count(), 0)
            self.assertEqual(approved.count(), 1)

    def test_limits_warn_but_do_not_expire_data(self):
        with tempfile.TemporaryDirectory() as td:
            store = TraceStore(
                td,
                policy=RetentionPolicy(max_total_records=1, max_age_days=1),
            )
            store.append_many([real_trace(), real_trace()])
            for path in Path(td).glob("trace-*.json"):
                old = time.time() - 2 * 86_400
                os.utime(path, (old, old))

            report = store.retention_report()
            self.assertTrue(report.record_limit_exceeded)
            self.assertTrue(report.age_limit_exceeded)
            self.assertTrue(report.needs_attention)
            self.assertFalse(report.automatic_deletion)
            self.assertEqual(store.count(), 2)


if __name__ == "__main__":
    unittest.main()
