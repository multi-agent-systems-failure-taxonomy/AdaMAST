"""Direct checkpoint transport for the Codex integration.

The main Codex agent still evaluates the existing four checkpoint fields. This
module moves those fields out of the assistant message and into the local
AdaMAST evidence store before the host Stop hook commits the episode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adamast.hosts.shared import force_utf8_stdio
from adamast.core.evidence import record_reflection

from .runtime import _harvest_codex_checkpoint
from .state import load_state, save_state

_GATE = re.compile(r"^[a-z][a-z0-9_-]{0,47}$")


def record_checkpoint(
    trace_output: Path | str,
    session_id: str,
    text: str,
    *,
    gate: str = "stop",
) -> dict[str, Any]:
    """Validate and persist one existing AdaMAST checkpoint gate."""
    root = Path(trace_output).expanduser().resolve()
    normalized_gate = str(gate or "").strip().lower()
    if not _GATE.fullmatch(normalized_gate):
        raise ValueError("gate must start with a letter and contain only a-z, 0-9, _ or -")
    state = load_state(root, str(session_id))
    if not state:
        raise RuntimeError("AdaMAST has no active state for this Codex conversation")
    selection = state.get("selection") or {}
    if selection.get("status") == "disabled":
        raise RuntimeError("AdaMAST is disabled for this Codex conversation")
    if state.get("finished"):
        raise RuntimeError("the current AdaMAST episode is already committed")

    reflection, status, error = _harvest_codex_checkpoint(
        text,
        state,
        gate=normalized_gate,
    )
    if reflection is None:
        raise ValueError(error or "checkpoint is missing or invalid")

    episode_sequence = int(state.get("episode_sequence", 1))
    payload_hash = hashlib.sha256(
        (normalized_gate + "\0" + "\n".join(line.rstrip() for line in text.splitlines()))
        .encode("utf-8", "replace")
    ).hexdigest()
    existing = next(
        (
            item
            for item in reversed(state.get("recorded_checkpoints", []))
            if isinstance(item, dict)
            and item.get("payload_hash") == payload_hash
            and int(item.get("episode_sequence", -1)) == episode_sequence
        ),
        None,
    )
    if existing:
        return {
            "checkpoint_id": existing["checkpoint_id"],
            "gate": existing["gate"],
            "status": existing["status"],
            "recorded": False,
            "duplicate": True,
        }

    checkpoint_id = record_reflection(
        root,
        state,
        reflection,
        gate=normalized_gate,
        task_id=str(state.get("session_id") or session_id),
        agent_type="codex",
        episode_sequence=episode_sequence,
        task_group=state.get("task_group"),
        gate_status=status,
        source="codex_direct",
    )
    entry = {
        "checkpoint_id": checkpoint_id,
        "gate": normalized_gate,
        "status": status,
        "error": error,
        "episode_sequence": episode_sequence,
        "recorded_at": time.time(),
        "payload_hash": payload_hash,
    }
    checkpoints = state.setdefault("recorded_checkpoints", [])
    checkpoints.append(entry)
    del checkpoints[:-50]
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_state(root, str(session_id), state)
    return {
        "checkpoint_id": checkpoint_id,
        "gate": normalized_gate,
        "status": status,
        "recorded": True,
        "duplicate": False,
    }


def _checkpoint_text(args: argparse.Namespace) -> str:
    supplied = (
        args.checkpoint,
        args.relevant_codes,
        args.evidence,
        args.next_action,
    )
    if any(value is not None for value in supplied):
        if not all(value is not None and str(value).strip() for value in supplied):
            raise ValueError(
                "--checkpoint, --relevant-codes, --evidence, and --next-action "
                "must be supplied together"
            )
        return (
            f"Checkpoint: {args.checkpoint}\n"
            f"Relevant codes: {args.relevant_codes}\n"
            f"Evidence: {args.evidence}\n"
            f"Next action: {args.next_action}\n"
        )
    text = sys.stdin.read()
    if not text.strip():
        raise ValueError("pass the four-line checkpoint on stdin or with field options")
    return text


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Record an AdaMAST Codex checkpoint without printing it in chat."
    )
    parser.add_argument("--trace-output", "--trace_output", required=True, type=Path)
    parser.add_argument("--session-id", "--session_id", required=True)
    parser.add_argument("--gate", default="stop")
    parser.add_argument("--checkpoint")
    parser.add_argument("--relevant-codes", "--relevant_codes")
    parser.add_argument("--evidence")
    parser.add_argument("--next-action", "--next_action")
    args = parser.parse_args(argv)
    try:
        result = record_checkpoint(
            args.trace_output,
            args.session_id,
            _checkpoint_text(args),
            gate=args.gate,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        parser.exit(2, f"AdaMAST checkpoint was not recorded: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
