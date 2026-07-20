from __future__ import annotations

import json
from pathlib import Path

import pytest

from adamast.traces import (
    TraceFormatError,
    load_trace_bundle,
    load_traces,
    write_normalized_jsonl,
)


def test_normalizes_stable_message_record(tmp_path: Path) -> None:
    source = tmp_path / "traces.json"
    source.write_text(
        json.dumps(
            {
                "trace_id": "trace-7",
                "task": "Use the tool",
                "messages": [
                    {"role": "user", "content": "Begin"},
                    {
                        "role": "assistant",
                        "content": "Checking",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "lookup",
                                    "arguments": "{\"id\": 7}",
                                }
                            }
                        ],
                    },
                ],
                "outcome": {"status": "failure"},
                "metadata": {"split": "validation"},
            }
        ),
        encoding="utf-8",
    )

    bundle = load_trace_bundle(source)

    assert bundle.report()["trace_count"] == 1
    assert bundle.report()["formats"] == {"messages": 1}
    trace = bundle.traces[0]
    assert trace["problem_id"] == "trace-7"
    assert "[USER]\nBegin" in trace["raw_trajectory"]
    assert "[ASSISTANT TOOL CALL] lookup" in trace["raw_trajectory"]
    assert trace["metadata"]["outcome"]["status"] == "failure"
    assert trace["metadata"]["split"] == "validation"


def test_explicit_empty_raw_trajectory_is_valid_failure_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "empty.jsonl"
    source.write_text(
        '{"trace_id":"empty-1","task":"Produce output","raw_trajectory":""}\n',
        encoding="utf-8",
    )

    bundle = load_trace_bundle(source)

    assert bundle.traces[0]["raw_trajectory"] == ""
    assert bundle.report()["empty_trajectories"] == 1


def test_rejects_record_without_trajectory_field(tmp_path: Path) -> None:
    source = tmp_path / "bad.json"
    source.write_text('{"trace_id":"bad-1","task":"Missing output"}', encoding="utf-8")

    with pytest.raises(TraceFormatError, match="needs raw_trajectory"):
        load_traces(source)


def test_rejects_invalid_jsonl_with_line_number(tmp_path: Path) -> None:
    source = tmp_path / "bad.jsonl"
    source.write_text('{"trace_id":"ok","raw_trajectory":"x"}\n{bad}\n', encoding="utf-8")

    with pytest.raises(TraceFormatError, match=r"bad\.jsonl:2"):
        load_traces(source)


def test_collapses_codex_session_jsonl(tmp_path: Path) -> None:
    source = tmp_path / "session.jsonl"
    entries = [
        {
            "type": "session_meta",
            "payload": {"instructions": "Repair the service", "model_provider": "openai"},
        },
        {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"text": "I inspected the logs."}],
            },
        },
    ]
    source.write_text(
        "\n".join(json.dumps(item) for item in entries) + "\n",
        encoding="utf-8",
    )

    traces = load_traces(source)

    assert len(traces) == 1
    assert traces[0]["_format"] == "codex_session"
    assert traces[0]["metadata"]["source_format"] == "codex-session"
    assert "[ASSISTANT]\nI inspected the logs." in traces[0]["raw_trajectory"]


def test_collapses_event_log_jsonl(tmp_path: Path) -> None:
    source = tmp_path / "events.jsonl"
    events = [
        {
            "event": "run_start",
            "problem_id": "event-3",
            "task": "Answer the request",
        },
        {
            "event": "response_received",
            "agent": "solver",
            "data": {"content": "A partial answer"},
        },
        {
            "event": "run_end",
            "data": {"final_answer": "A final answer"},
        },
    ]
    source.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )

    traces = load_traces(source)

    assert len(traces) == 1
    assert traces[0]["problem_id"] == "event-3"
    assert traces[0]["metadata"]["source_format"] == "event-log"
    assert "[solver RESPONSE]\nA partial answer" in traces[0]["raw_trajectory"]


def test_imports_tau_bench_record(tmp_path: Path) -> None:
    source = tmp_path / "airline.json"
    source.write_text(
        json.dumps(
            {
                "task_id": 19,
                "trial": 2,
                "reward": 0.0,
                "info": {"task": {"instruction": "Change the reservation"}},
                "traj": [
                    {"role": "user", "content": "Move my flight"},
                    {"role": "assistant", "content": "I cannot find it"},
                ],
            }
        ),
        encoding="utf-8",
    )

    trace = load_traces(source)[0]

    assert trace["_format"] == "tau_bench"
    assert trace["problem_id"] == "tau_bench_airline_19_trial2"
    assert trace["metadata"]["source_format"] == "tau-bench"
    assert "FAILURE (reward=0.0)" in trace["raw_trajectory"]


def test_imports_mad_envelope(tmp_path: Path) -> None:
    source = tmp_path / "mad.json"
    source.write_text(
        json.dumps(
            {
                "mas_name": "MetaGPT",
                "llm_name": "model-x",
                "benchmark_name": "coding",
                "trace_id": "44",
                "trace": {"trajectory": "[2026-01-01] FROM: Coder TO: Reviewer"},
            }
        ),
        encoding="utf-8",
    )

    trace = load_traces(source)[0]

    assert trace["_format"] == "mad"
    assert trace["problem_id"] == "MetaGPT_coding_44"
    assert trace["metadata"]["source_format"] == "mad"


def test_normalized_writer_round_trips(tmp_path: Path) -> None:
    traces = [
        {
            "problem_id": "one",
            "task": "A task",
            "raw_trajectory": "A trajectory",
            "metadata": {"source_format": "adamast"},
        }
    ]
    target = write_normalized_jsonl(traces, tmp_path / "normalized.jsonl")

    assert load_traces(target) == traces
