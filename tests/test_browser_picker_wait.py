"""Liveness semantics of the synchronous picker wait.

Regression coverage for the selector flow returning a bogus "selection
timed out" while the user was still choosing: the first liveness probe
used to fire immediately after launch with a 0.1s HTTP timeout against a
single-threaded page server, and one failed probe was treated as picker
death.
"""

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from adamast.hosts.interactive.browser_picker import wait_for_browser_choice


def _picker(result_path: Path) -> dict:
    return {
        "url": "http://127.0.0.1:1/",
        "result_path": str(result_path),
        "ready_path": str(result_path.with_suffix(".ready.json")),
    }


def _write_receipt(result_path: Path, choice: str = "mast") -> None:
    result_path.write_text(
        json.dumps({"choice": choice}) + "\n", encoding="utf-8"
    )


class WaitForBrowserChoiceTests(unittest.TestCase):
    def test_failed_probes_below_threshold_do_not_end_the_wait(self):
        # The user's repro: probes fail while the browser is still loading
        # the page, then the receipt lands. The wait must survive the
        # failing probes and return the choice instead of a false timeout.
        with tempfile.TemporaryDirectory() as td:
            result_path = Path(td) / "session.result.json"
            timer = threading.Timer(0.3, _write_receipt, args=(result_path,))
            timer.start()
            try:
                with patch(
                    "adamast.hosts.interactive.browser_picker.picker_alive",
                    return_value=False,
                ):
                    choice = wait_for_browser_choice(
                        _picker(result_path),
                        store_dir=Path(td),
                        timeout_seconds=5,
                        poll_seconds=0.01,
                        startup_grace_seconds=0.0,
                        probe_timeout_seconds=0.05,
                        probe_failures_required=1000,
                        liveness_interval_seconds=0.01,
                    )
            finally:
                timer.cancel()
        self.assertEqual(choice, "mast")

    def test_consecutive_probe_failures_still_detect_a_dead_picker(self):
        with tempfile.TemporaryDirectory() as td:
            result_path = Path(td) / "session.result.json"
            started = time.monotonic()
            with patch(
                "adamast.hosts.interactive.browser_picker.picker_alive",
                return_value=False,
            ) as alive:
                choice = wait_for_browser_choice(
                    _picker(result_path),
                    store_dir=Path(td),
                    timeout_seconds=30,
                    poll_seconds=0.01,
                    startup_grace_seconds=0.0,
                    probe_timeout_seconds=0.05,
                    probe_failures_required=3,
                    liveness_interval_seconds=0.01,
                )
            elapsed = time.monotonic() - started
        self.assertIsNone(choice)
        self.assertEqual(alive.call_count, 3)
        self.assertLess(elapsed, 5, "death must be detected well before timeout")

    def test_receipt_written_just_before_exit_beats_the_death_verdict(self):
        # The picker writes its receipt immediately before exiting; the
        # final re-read must return that choice instead of None.
        with tempfile.TemporaryDirectory() as td:
            result_path = Path(td) / "session.result.json"
            probes = {"count": 0}

            def dying_picker(_picker_dict, *, timeout):
                probes["count"] += 1
                if probes["count"] == 3:
                    _write_receipt(result_path)
                return False

            with patch(
                "adamast.hosts.interactive.browser_picker.picker_alive",
                side_effect=dying_picker,
            ):
                choice = wait_for_browser_choice(
                    _picker(result_path),
                    store_dir=Path(td),
                    timeout_seconds=30,
                    poll_seconds=0.01,
                    startup_grace_seconds=0.0,
                    probe_timeout_seconds=0.05,
                    probe_failures_required=3,
                    liveness_interval_seconds=0.01,
                )
        self.assertEqual(choice, "mast")

    def test_single_transient_probe_failure_recovers(self):
        # One hiccup (GC pause, busy page render) must reset on the next
        # healthy probe rather than accumulate toward a death verdict.
        with tempfile.TemporaryDirectory() as td:
            result_path = Path(td) / "session.result.json"
            outcomes = iter([False, True, False, True])

            def flaky_picker(_picker_dict, *, timeout):
                try:
                    return next(outcomes)
                except StopIteration:
                    _write_receipt(result_path)
                    return True

            with patch(
                "adamast.hosts.interactive.browser_picker.picker_alive",
                side_effect=flaky_picker,
            ):
                choice = wait_for_browser_choice(
                    _picker(result_path),
                    store_dir=Path(td),
                    timeout_seconds=30,
                    poll_seconds=0.01,
                    startup_grace_seconds=0.0,
                    probe_timeout_seconds=0.05,
                    probe_failures_required=2,
                    liveness_interval_seconds=0.01,
                )
        self.assertEqual(choice, "mast")


if __name__ == "__main__":
    unittest.main()
