"""Windows-safe shared-file primitives and manifest concurrency tests."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from adamast.core.fsio import (
    read_text_retry,
    replace_retry,
    write_text_atomic_retry,
)
from adamast.core.program import ProgramWorkspace


class FsioPrimitiveTests(unittest.TestCase):
    def test_atomic_write_round_trips_and_leaves_no_temporaries(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "value.json"
            write_text_atomic_retry(path, '{"n": 1}\n')
            write_text_atomic_retry(path, '{"n": 2}\n')
            self.assertEqual(json.loads(read_text_retry(path)), {"n": 2})
            leftovers = [p.name for p in Path(td).iterdir() if p.name != "value.json"]
            self.assertEqual(leftovers, [])

    def test_replace_retry_moves_the_file(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td) / "source.txt"
            target = Path(td) / "target.txt"
            source.write_text("payload", encoding="utf-8")
            target.write_text("old", encoding="utf-8")
            replace_retry(source, target)
            self.assertFalse(source.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "payload")

    def test_read_text_retry_propagates_missing_file(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(FileNotFoundError):
                read_text_retry(Path(td) / "absent.txt")


class ManifestConcurrencyTests(unittest.TestCase):
    def test_readers_survive_concurrent_locked_manifest_cycles(self):
        # Regression for the Windows CI flake: an unlocked load() racing the
        # locked writer's atomic replace raised PermissionError, which the
        # generation job's catch-all recorded as action "failed". Pre-fix
        # this reproduced in well under a second on Windows.
        with tempfile.TemporaryDirectory() as td:
            workspace = ProgramWorkspace(Path(td) / "program")
            errors: list[BaseException] = []
            stop = threading.Event()

            def writer() -> None:
                while not stop.is_set():
                    try:
                        with workspace.locked_manifest() as manifest:
                            counter = int(manifest.get("stress_counter", 0))
                            manifest["stress_counter"] = counter + 1
                    except BaseException as exc:  # noqa: BLE001
                        errors.append(exc)
                        stop.set()
                        return

            def reader() -> None:
                while not stop.is_set():
                    try:
                        workspace.load()
                    except BaseException as exc:  # noqa: BLE001
                        errors.append(exc)
                        stop.set()
                        return

            threads = [
                threading.Thread(target=writer),
                threading.Thread(target=reader),
                threading.Thread(target=reader),
            ]
            for thread in threads:
                thread.start()
            time.sleep(1.5)
            stop.set()
            for thread in threads:
                thread.join(10)
            self.assertEqual(errors, [])
            self.assertGreater(
                int(workspace.load().get("stress_counter", 0)), 0
            )

    def test_readonly_lock_exit_does_not_rewrite_the_manifest(self):
        # Activation polls hold the lock every few milliseconds without
        # changing anything; rewriting on each exit multiplies the replace/
        # read collision surface for every unlocked reader.
        with tempfile.TemporaryDirectory() as td:
            workspace = ProgramWorkspace(Path(td) / "program")
            before = workspace.manifest_path.stat().st_mtime_ns
            for _ in range(3):
                with workspace.locked_manifest():
                    pass
            self.assertEqual(
                workspace.manifest_path.stat().st_mtime_ns, before
            )

    def test_mutating_lock_exit_persists(self):
        with tempfile.TemporaryDirectory() as td:
            workspace = ProgramWorkspace(Path(td) / "program")
            with workspace.locked_manifest() as manifest:
                manifest["adamast_model"] = "test-model"
            self.assertEqual(workspace.load()["adamast_model"], "test-model")


class HookStdioEncodingTests(unittest.TestCase):
    def test_force_utf8_stdio_emits_utf8_on_piped_streams(self):
        # Piped hook stdio on Windows defaults to the ANSI code page, which
        # turns taxonomy em-dashes into mojibake in the host conversation.
        script = (
            "from adamast.hosts.shared import force_utf8_stdio\n"
            "force_utf8_stdio()\n"
            "print('gate \\u2014 ready')\n"
        )
        env = {
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONIOENCODING", "PYTHONUTF8"}
        }
        completed = subprocess.run(
            [sys.executable, "-X", "utf8=0", "-c", script],
            capture_output=True,
            timeout=60,
            env=env,
            cwd=Path(__file__).resolve().parent.parent,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("gate — ready", completed.stdout.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
