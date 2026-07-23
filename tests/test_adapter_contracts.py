"""Shared behavioral contract for hook adapters.

These tests deliberately exercise Claude Code and Codex through the same
scenario so adapter-specific fixes do not silently land in only one sibling.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from adamast.hosts.claude_code.config import ClaudeCodeConfig
from adamast.hosts.claude_code.checkpoint import (
    record_checkpoint as record_claude_checkpoint,
)
from adamast.hosts.claude_code.hooks import session_start as claude_start
from adamast.hosts.claude_code.hooks import stop as claude_stop
from adamast.hosts.codex.config import CodexConfig
from adamast.hosts.codex.checkpoint import (
    record_checkpoint as record_codex_checkpoint,
)
from adamast.hosts.codex.runtime import session_start as codex_start
from adamast.hosts.codex.runtime import stop as codex_stop
from adamast.core.evidence import EVIDENCE_FILE

ROOT = Path(__file__).resolve().parent.parent
STORE_DIR = ROOT / "tests" / "fixtures" / "taxonomies"


@dataclass(frozen=True)
class AdapterCase:
    name: str
    make_config: Callable[[Path], Any]
    session_start: Callable[[dict, Any], Any]
    stop: Callable[[dict, Any], tuple[int, str] | dict]
    record: Callable[[Path, str, str], dict]


def _append_transcript(path: Path, text: str, *, role: str = "assistant") -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "type": role,
                    "message": {
                        "role": role,
                        "content": [{"type": "text", "text": text}],
                    },
                }
            )
            + "\n"
        )


PRIVATE_CHECKPOINT = """Checkpoint: shared adapter contract verified
Relevant codes: none apply
Evidence: verification is present
Next action: complete
"""


class AdapterContractTests(unittest.TestCase):
    def cases(self) -> tuple[AdapterCase, ...]:
        return (
            AdapterCase(
                name="claude_code",
                make_config=lambda root: ClaudeCodeConfig(
                    trace_output=root / "program",
                    adamast_model="test-model",
                    store_dir=STORE_DIR,
                    dashboard=False,
                ),
                session_start=lambda event, config: claude_start.handle(event, config),
                stop=lambda event, config: claude_stop.handle(event, config),
                record=record_claude_checkpoint,
            ),
            AdapterCase(
                name="codex",
                make_config=lambda root: CodexConfig(
                    trace_output=root / "program",
                    adamast_model="test-model",
                    store_dir=STORE_DIR,
                    dashboard=False,
                ),
                session_start=codex_start,
                stop=codex_stop,
                record=record_codex_checkpoint,
            ),
        )

    def test_session_start_stop_gate_and_evidence_contract(self):
        for case in self.cases():
            with self.subTest(adapter=case.name):
                with tempfile.TemporaryDirectory() as td:
                    root = Path(td)
                    transcript = root / "transcript.jsonl"
                    transcript.write_text("", encoding="utf-8")
                    config = case.make_config(root)
                    event = {
                        "hook_event_name": "SessionStart",
                        "session_id": f"{case.name}-session",
                        "cwd": str(root),
                        "transcript_path": str(transcript),
                    }

                    start = case.session_start(event, config)
                    rendered_start = json.dumps(start) if isinstance(start, dict) else str(start)
                    self.assertIn("AdaMAST runtime interaction is active", rendered_start)

                    _append_transcript(transcript, "Solve the task.", role="user")
                    _append_transcript(transcript, "Verified final answer.")
                    case.record(
                        config.trace_output,
                        event["session_id"],
                        PRIVATE_CHECKPOINT,
                    )
                    result = case.stop(
                        {**event, "hook_event_name": "Stop"},
                        config,
                    )
                    if isinstance(result, tuple):
                        code, message = result
                        self.assertEqual((code, message), (0, ""))
                    else:
                        self.assertTrue(result["continue"])
                        self.assertNotIn("systemMessage", result)

                    evidence = json.loads(
                        (config.trace_output / EVIDENCE_FILE).read_text(
                            encoding="utf-8"
                        )
                    )
                    self.assertEqual(len(evidence["checkpoints"]), 1)


if __name__ == "__main__":
    unittest.main()
