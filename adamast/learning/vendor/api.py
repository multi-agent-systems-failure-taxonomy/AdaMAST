"""High-level public API.

Two top-level entry points for the common workflows:

- :func:`generate_taxonomy` — give it traces (a path, a directory, or a
  list of dicts) and it returns a taxonomy dict.
- :func:`classify_trace` — give it a taxonomy + a trace and it returns
  a :class:`Diagnosis`.

For finer-grained control, instantiate
:class:`adamast.pipeline.TaxonomyPipeline` and
:class:`adamast.classifier.TaxonomyClassifier` directly.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Union

from adamast.learning.vendor.classifier import Diagnosis, TaxonomyClassifier
from adamast.learning.vendor.config import PipelineConfig
from adamast.learning.vendor.pipeline.pipeline import TaxonomyPipeline
from adamast.learning.vendor.traces.loader import load_traces

PathLike = Union[str, Path]
TraceInput = Union[PathLike, Iterable[Any]]


def generate_taxonomy(
    traces: TraceInput,
    output_dir: Optional[PathLike] = None,
    *,
    model: Optional[str] = None,
    max_codes: int = 0,
    config: Optional[PipelineConfig] = None,
    save_intermediate: bool = True,
    verbose: bool = True,
) -> Dict[str, Any]:
    """Generate a failure taxonomy from a set of traces.

    Parameters
    ----------
    traces
        Path to a file, directory, or an iterable of trace dicts. Files are
        auto-detected — JSON, JSONL, tau-bench, codex CLI, event-log, KIRA,
        Forgecode, and the already-unified AdaMAST format are all supported.
    output_dir
        Where to write ``taxonomy.json`` and intermediate step files.
        Defaults to ``./adamast_output``.
    model
        Override the LLM model. If not given, picks based on environment
        (``ADAMAST_MODEL``, ``OPENAI_MODEL``, ``OPENAI_BASE_URL``).
    max_codes
        Optional cap on the final number of codes. ``0`` means no cap.
    config
        For full control — pass a :class:`PipelineConfig` directly. ``model``
        and ``max_codes`` arguments override fields on this config.
    save_intermediate
        Whether to save per-step JSON files (default: True). Disabling
        leaves only ``taxonomy.json`` in the output directory.
    verbose
        Print progress to stdout.

    Returns
    -------
    dict
        The generated taxonomy. Has two layers — ``annotation_layer``
        (compact, judge-friendly) and ``full_layer`` (everything,
        including heuristics and discovered system metadata).
    """
    if config is None:
        config = PipelineConfig()
    if model:
        config.model = model
    if max_codes:
        config.max_codes = max_codes
    config.save_intermediate_steps = save_intermediate

    loaded = load_traces(traces, verbose=verbose)
    if not loaded:
        raise ValueError(
            "No traces could be loaded from the provided source. "
            "Check that the path/file/iterable contains supported trace data."
        )

    pipeline = TaxonomyPipeline(config=config, output_dir=output_dir)
    return pipeline.run(loaded)


def classify_trace(
    taxonomy: Union[Dict[str, Any], PathLike],
    trace: Dict[str, Any],
    *,
    model: Optional[str] = None,
    config: Optional[PipelineConfig] = None,
) -> Optional[Diagnosis]:
    """Classify a single trace against an existing taxonomy.

    Parameters
    ----------
    taxonomy
        The taxonomy dict (as returned by :func:`generate_taxonomy`) or a
        path to a ``taxonomy.json`` file.
    trace
        A single trace dict in the unified AdaMAST format. If you have raw
        trace data, run it through
        :func:`adamast.traces.normalize_trace` first.
    model, config
        Override the classifier's LLM settings.

    Returns
    -------
    Diagnosis or None
        The best-matching diagnosis, or ``None`` if classification fails.
    """
    if config is None:
        config = PipelineConfig()
    if model:
        config.model = model

    classifier = TaxonomyClassifier(taxonomy, config=config)
    return classifier.classify(trace)


def classify_traces(
    taxonomy: Union[Dict[str, Any], PathLike],
    traces: List[Dict[str, Any]],
    *,
    model: Optional[str] = None,
    config: Optional[PipelineConfig] = None,
) -> List[Optional[Diagnosis]]:
    """Classify many traces in one call. Order is preserved."""
    if config is None:
        config = PipelineConfig()
    if model:
        config.model = model

    classifier = TaxonomyClassifier(taxonomy, config=config)
    return classifier.classify_batch(traces)
