"""Robustness tests: evidence-lock staleness and corrupt manifests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from adamast.core.evidence import _file_lock
from adamast.core.program import MANIFEST_NAME, ProgramConflict, ProgramWorkspace


class EvidenceLockTests(unittest.TestCase):
    def test_stale_lock_is_broken(self):
        # Regression: a writer killed while holding the lock directory used
        # to disable evidence recording forever (every write timed out).
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "evidence.json"
            lock = target.with_suffix(target.suffix + ".lock")
            lock.mkdir()
            old = time.time() - 3600
            os.utime(lock, (old, old))
            with _file_lock(target, timeout=0.5):
                pass  # a stale lock must be broken, not waited on

    def test_fresh_lock_still_times_out(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "evidence.json"
            lock = target.with_suffix(target.suffix + ".lock")
            lock.mkdir()
            with self.assertRaises(TimeoutError):
                with _file_lock(target, timeout=0.2):
                    pass


class CorruptManifestTests(unittest.TestCase):
    def test_corrupt_manifest_is_quarantined_with_actionable_error(self):
        # Regression: a half-written manifest made every CLI crash with a raw
        # JSONDecodeError traceback and no recovery path.
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "program"
            workspace = ProgramWorkspace(root)
            manifest = root / MANIFEST_NAME
            manifest.write_text("{not valid json", encoding="utf-8")
            with self.assertRaisesRegex(ProgramConflict, "corrupt"):
                workspace.load()
            quarantined = list(root.glob(f"{MANIFEST_NAME}.corrupt-*"))
            self.assertEqual(len(quarantined), 1)
            # The next load starts clean instead of failing forever.
            self.assertEqual(workspace.load(), {})

    def test_quarantine_inside_locked_manifest_releases_the_lock(self):
        # Regression: the quarantine raised inside locked_manifest() before
        # the lock-releasing try/finally, so the very next workspace call
        # timed out on the leftover .manifest.lock (found live in the E2E).
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "program"
            ProgramWorkspace(root)
            (root / MANIFEST_NAME).write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(ProgramConflict, "corrupt"):
                ProgramWorkspace(root)  # __init__ goes through locked_manifest
            self.assertFalse((root / ".manifest.lock").exists())
            # Immediate retry must succeed with a fresh manifest, not
            # TimeoutError on a leaked lock.
            fresh = ProgramWorkspace(root)
            self.assertTrue(fresh.load().get("program_id"))


if __name__ == "__main__":
    unittest.main()
