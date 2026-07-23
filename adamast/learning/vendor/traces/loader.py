"""File-based trace loading with auto-detection of format.

``TraceLoader`` and ``load_traces`` are the high-level entry points. They
accept a single file, a directory (recursively scanned for ``*.json`` /
``*.jsonl``), or an in-memory iterable, and produce a list of dicts in
the unified trace format expected by the pipeline.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from adamast.learning.vendor.traces.normalizer import (
    UnifiedTrace,
    _convert_claude_stream_session,
    _convert_codex_session,
    _convert_generic_trace,
    _convert_tau_bench,
    _is_claude_stream_entry,
    _is_codex_entry,
    _is_event_log_entry,
    _is_tau_bench,
    _reconstruct_from_events,
    normalize_trace,
)
from adamast.learning.vendor.utils import progress

logger = logging.getLogger(__name__)

PathLike = Union[str, Path]


class TraceLoader:
    """Loads traces from files / directories / iterables into the unified format.

    Use :meth:`load` for a one-shot, or call the more specific
    :meth:`load_file` / :meth:`load_dir` / :meth:`load_iterable` directly.
    """

    def __init__(self, *, verbose: bool = True):
        self.verbose = verbose
        self._traces: List[Dict[str, Any]] = []

    def _log(self, msg: str) -> None:
        if self.verbose:
            progress(msg)

    # ───── Public API ─────

    def load(self, source: Union[PathLike, Iterable[Any]]) -> List[Dict[str, Any]]:
        """Load traces from any source.

        Accepts: a path (file or directory), or an iterable of trace items.
        Returns a list of unified-format dicts.
        """
        self._traces = []
        if isinstance(source, (str, Path)):
            path = Path(source)
            if not path.exists():
                raise FileNotFoundError(f"No such trace source: {path}")
            if path.is_file():
                self.load_file(path)
            else:
                self.load_dir(path)
        else:
            self.load_iterable(source)
        return list(self._traces)

    def load_file(self, file_path: PathLike) -> List[Dict[str, Any]]:
        """Load traces from a single ``.json`` or ``.jsonl`` file."""
        file_path = Path(file_path)
        try:
            self._load_single_file(file_path)
        except Exception as e:  # noqa: BLE001 - we want to keep going on bad files
            self._log(f"  [!] Error loading {file_path.name}: {e}")
        return list(self._traces)

    def load_dir(self, dir_path: PathLike) -> List[Dict[str, Any]]:
        """Load traces from a directory, recursively scanning JSON and JSONL files."""
        dir_path = Path(dir_path)
        trace_files = sorted(
            list(dir_path.glob("**/*.json")) + list(dir_path.glob("**/*.jsonl"))
        )
        self._log(f"  Found {len(trace_files)} trace files in {dir_path}")
        for f in trace_files:
            try:
                self._load_single_file(f)
            except Exception as e:  # noqa: BLE001
                self._log(f"  [!] Error loading {f.name}: {e}")
        return list(self._traces)

    def load_iterable(self, items: Iterable[Any]) -> List[Dict[str, Any]]:
        """Load traces from an in-memory iterable (lists, dicts, strings)."""
        for i, item in enumerate(items):
            unified = normalize_trace(item)
            if unified is None:
                continue
            if not unified.problem_id:
                unified.problem_id = f"trace_{i}"
            self._traces.append(unified.to_dict())
        return list(self._traces)

    # ───── Internals ─────

    def _load_single_file(self, file_path: Path) -> None:
        content = file_path.read_text(encoding="utf-8")
        if not content.strip():
            return

        # Try whole-file JSON first.
        try:
            data = json.loads(content)
            self._consume_json(data, file_path)
            return
        except json.JSONDecodeError:
            pass

        # Fall back to JSONL with optional event-log / codex aggregation.
        self._consume_jsonl(content, file_path)

    def _consume_json(self, data: Any, file_path: Path) -> None:
        if isinstance(data, list):
            if data and _is_tau_bench(data[0]):
                for i, item in enumerate(data):
                    self._add(_convert_tau_bench(item, file_path.stem, i))
                return

            for i, item in enumerate(data):
                unified = self._normalize_item(item, file_path, i)
                if unified is not None:
                    if not unified.problem_id:
                        unified.problem_id = f"{file_path.stem}_{i}"
                    self._add(unified)
            return

        if isinstance(data, dict):
            if _is_tau_bench(data):
                self._add(_convert_tau_bench(data, file_path.stem, 0))
                return

            unified = self._normalize_item(data, file_path, 0)
            if unified is not None:
                if not unified.problem_id:
                    unified.problem_id = file_path.stem
                self._add(unified)
            return

    def _consume_jsonl(self, content: str, file_path: Path) -> None:
        lines = content.replace("\r\n", "\n").strip().split("\n")
        events: List[Dict[str, Any]] = []
        codex_entries: List[Dict[str, Any]] = []
        claude_entries: List[Dict[str, Any]] = []
        is_event_log = False
        is_codex = False
        is_claude = False

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            if not isinstance(obj, dict):
                continue

            if _is_codex_entry(obj):
                is_codex = True
                codex_entries.append(obj)
            elif _is_claude_stream_entry(obj):
                is_claude = True
                claude_entries.append(obj)
            elif _is_event_log_entry(obj):
                is_event_log = True
                events.append(obj)
            else:
                unified = self._normalize_item(obj, file_path, len(self._traces))
                if unified is not None:
                    if not unified.problem_id:
                        unified.problem_id = file_path.stem
                    self._add(unified)

        if is_codex and codex_entries:
            trace = _convert_codex_session(codex_entries, file_path)
            if trace is not None:
                self._add(trace)
        elif is_claude and claude_entries:
            trace = _convert_claude_stream_session(claude_entries, file_path)
            if trace is not None:
                self._add(trace)
        elif is_event_log and events:
            trace = _reconstruct_from_events(events, file_path.stem)
            if trace is not None:
                self._add(trace)

    def _normalize_item(self, item: Any, file_path: Path, index: int) -> Optional[UnifiedTrace]:
        unified = normalize_trace(item, file_path=file_path)
        if unified is None and isinstance(item, dict):
            # Last-resort attempt: shove whatever we have into the generic
            # converter. The pipeline is tolerant of partial traces.
            unified = _convert_generic_trace(item, index)
            if not unified.raw_trajectory:
                return None
        return unified

    def _add(self, unified: UnifiedTrace) -> None:
        self._traces.append(unified.to_dict())


def load_traces(
    source: Union[PathLike, Iterable[Any]],
    *,
    verbose: bool = True,
) -> List[Dict[str, Any]]:
    """Convenience function: load traces from ``source`` and return dicts.

    ``source`` may be a file path, directory path, or iterable of trace
    items. Set ``verbose=False`` to suppress progress logging.
    """
    return TraceLoader(verbose=verbose).load(source)
