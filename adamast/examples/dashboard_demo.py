"""Launch a disposable taxonomy dashboard populated with placeholder evidence.

Run from the repository root:

    python -m adamast.examples.dashboard_demo

The temporary program and taxonomy store disappear when the server stops.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from adamast.dashboard.server import RUNTIME_EVIDENCE, run_dashboard
from adamast.core.program import ProgramWorkspace
from adamast.core import store

DEMO_TAXONOMY_ID = "tax-skylab-orbital-demo-001"

DEMO_TAXONOMY = {
    "taxonomy_id": DEMO_TAXONOMY_ID,
    "repo": "demo/skylab-control",
    "domain": "orbital-task-scheduling",
    "codes": [
        {
            "id": "ORB-01",
            "name": "Stale ephemeris used for scheduling",
            "description": (
                "A task window is computed from cached orbital data after a "
                "new ephemeris has arrived, placing the operation outside its "
                "valid visibility interval."
            ),
            "category": "Runtime",
            "severity": "high",
            "fire_count": 7,
            "task_firings": [
                {"task_id": "TASK-1042", "count": 3},
                {"task_id": "TASK-1061", "count": 1},
                {"task_id": "TASK-1098", "count": 2},
                {"task_id": "TASK-1120", "count": 1},
            ],
        },
        {
            "id": "ORB-02",
            "name": "Resource lock released before handoff",
            "description": (
                "Exclusive antenna or compute capacity is released before the "
                "downstream task confirms ownership, allowing overlapping "
                "operations to claim the same resource."
            ),
            "category": "Coordination",
            "severity": "critical",
            "fire_count": 4,
            "task_firings": [
                {"task_id": "TASK-1033", "count": 1},
                {"task_id": "TASK-1087", "count": 2},
                {"task_id": "TASK-1120", "count": 1},
            ],
        },
        {
            "id": "ORB-03",
            "name": "Retry duplicates a completed command",
            "description": (
                "A timeout is treated as proof that the remote command failed, "
                "so the scheduler repeats an operation that actually completed."
            ),
            "category": "Runtime",
            "severity": "medium",
            "fire_count": 2,
            "task_firings": [
                {"task_id": "TASK-1074", "count": 1},
                {"task_id": "TASK-1135", "count": 1},
            ],
        },
        {
            "id": "ORB-04",
            "name": "Clock offset omitted at a boundary",
            "description": (
                "One scheduling boundary uses local station time while the "
                "rest of the plan uses UTC, shifting a narrow operation window."
            ),
            "category": "Specification",
            "severity": "high",
            "fire_count": 5,
            "task_firings": [
                {"task_id": "TASK-1019", "count": 2},
                {"task_id": "TASK-1055", "count": 1},
                {"task_id": "TASK-1114", "count": 2},
            ],
        },
        {
            "id": "ORB-05",
            "name": "Validation covers the plan but not execution",
            "description": (
                "The generated schedule is validated before dispatch, but the "
                "mutated execution payload is never checked against the same "
                "constraints."
            ),
            "category": "Verification",
            "severity": "medium",
            "fire_count": 0,
            "task_firings": [],
        },
    ],
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Open a disposable AdaMAST dashboard with placeholder metrics."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory(prefix="adamast-dashboard-demo-") as td:
        root = Path(td)
        store_dir = root / "taxonomies"
        program_dir = root / "program"
        store.register(DEMO_TAXONOMY, store_dir)
        workspace = ProgramWorkspace(program_dir)
        workspace.bind_inherited_taxonomy(DEMO_TAXONOMY_ID)
        _write_demo_runtime_evidence(program_dir)

        print("Loading disposable demo data.")
        print("Counts and task IDs are placeholders for layout review only.")
        print("Press Ctrl+C to stop the dashboard and remove the demo data.")
        run_dashboard(
            program_dir,
            store_dir,
            args.host,
            args.port,
            open_browser=not args.no_browser,
        )
    return 0


def _write_demo_runtime_evidence(program_dir: Path) -> None:
    (program_dir / ".adamast-task-labels.json").write_text(
        json.dumps(
            {
                "task-1042": {"label": "UID1042", "correct": False},
                "task-1061": {"label": "UID1061", "correct": True},
                "task-1087": {"label": "UID1087", "correct": False},
                "task-1120": {"label": "UID1120", "correct": False},
                "task-1135": {"label": "UID1135", "correct": True},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (program_dir / RUNTIME_EVIDENCE).write_text(
        json.dumps(
            {
                "version": 1,
                "checkpoints": [
                    {
                        "taxonomy_id": DEMO_TAXONOMY_ID,
                        "checkpoint_id": "cp-1042-plan",
                        "timestamp": 1,
                        "gate": "tool_boundary",
                        "task_id": "task-1042",
                        "fired_codes": ["ORB-01"],
                    },
                    {
                        "taxonomy_id": DEMO_TAXONOMY_ID,
                        "checkpoint_id": "cp-1061-clean",
                        "timestamp": 2,
                        "gate": "final_gate",
                        "task_id": "task-1061",
                        "none_apply": True,
                        "considered_codes": ["ORB-01", "ORB-05"],
                        "fired_codes": [],
                        "observe": "The agent checked the updated window and payload.",
                        "correlate": "No evidence-supported failure mode remained.",
                        "decide": "Submit without repair.",
                    },
                    {
                        "taxonomy_id": DEMO_TAXONOMY_ID,
                        "checkpoint_id": "cp-1087-handoff",
                        "timestamp": 3,
                        "gate": "subtask_boundary",
                        "task_id": "task-1087",
                        "fired_codes": ["ORB-02"],
                    },
                    {
                        "taxonomy_id": DEMO_TAXONOMY_ID,
                        "checkpoint_id": "cp-1120-final",
                        "timestamp": 4,
                        "gate": "final_gate",
                        "task_id": "task-1120",
                        "fired_codes": ["ORB-01", "ORB-04"],
                    },
                    {
                        "taxonomy_id": DEMO_TAXONOMY_ID,
                        "checkpoint_id": "cp-1135-retry",
                        "timestamp": 5,
                        "gate": "tool_failure",
                        "task_id": "task-1135",
                        "fired_codes": ["ORB-03"],
                    },
                ],
                "taxonomies": {
                    DEMO_TAXONOMY_ID: {
                        "codes": {
                            "ORB-01": {
                                "fire_count": 2,
                                "task_firings": {"task-1042": 1, "task-1120": 1},
                                "events": [
                                    {
                                        "checkpoint_id": "cp-1042-plan",
                                        "timestamp": 1,
                                        "gate": "tool_boundary",
                                        "task_id": "task-1042",
                                        "evidence": (
                                            "The schedule reused the cached "
                                            "AOS/LOS window after a newer "
                                            "ephemeris was loaded."
                                        ),
                                        "correlate": (
                                            "The task crossed the exact boundary "
                                            "described by ORB-01."
                                        ),
                                        "decide": "Recompute the window before dispatch.",
                                    },
                                    {
                                        "checkpoint_id": "cp-1120-final",
                                        "timestamp": 4,
                                        "gate": "final_gate",
                                        "task_id": "task-1120",
                                        "evidence": (
                                            "The final plan still cites the older "
                                            "ephemeris revision."
                                        ),
                                        "correlate": "The stale input explains the invalid window.",
                                        "decide": "Block final submission until recomputed.",
                                    },
                                ],
                            },
                            "ORB-02": {
                                "fire_count": 1,
                                "task_firings": {"task-1087": 1},
                                "events": [
                                    {
                                        "checkpoint_id": "cp-1087-handoff",
                                        "timestamp": 3,
                                        "gate": "subtask_boundary",
                                        "task_id": "task-1087",
                                        "evidence": (
                                            "The antenna lock was released before "
                                            "the downstream scheduler confirmed ownership."
                                        ),
                                        "correlate": "The handoff lost exclusive ownership.",
                                        "decide": "Hold the lock until acknowledgement.",
                                    }
                                ],
                            },
                            "ORB-03": {
                                "fire_count": 1,
                                "task_firings": {"task-1135": 1},
                                "events": [
                                    {
                                        "checkpoint_id": "cp-1135-retry",
                                        "timestamp": 5,
                                        "gate": "tool_failure",
                                        "task_id": "task-1135",
                                        "evidence": (
                                            "A timeout triggered a duplicate command "
                                            "without checking command completion."
                                        ),
                                        "correlate": "The retry path matched ORB-03.",
                                        "decide": "Check idempotency before retrying.",
                                    }
                                ],
                            },
                            "ORB-04": {
                                "fire_count": 1,
                                "task_firings": {"task-1120": 1},
                                "events": [
                                    {
                                        "checkpoint_id": "cp-1120-final",
                                        "timestamp": 4,
                                        "gate": "final_gate",
                                        "task_id": "task-1120",
                                        "evidence": (
                                            "The uplink boundary was expressed in "
                                            "station local time while the rest used UTC."
                                        ),
                                        "correlate": "The time basis mismatch shifted the window.",
                                        "decide": "Normalize the boundary before release.",
                                    }
                                ],
                            },
                        }
                    }
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
