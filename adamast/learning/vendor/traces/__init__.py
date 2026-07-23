"""Trace loading, normalization, and signal extraction."""

from adamast.learning.vendor.traces.loader import TraceLoader, load_traces
from adamast.learning.vendor.traces.normalizer import (
    UnifiedTrace,
    normalize_trace,
    normalize_traces,
)
from adamast.learning.vendor.traces.signals import SignalExtractor

__all__ = [
    "TraceLoader",
    "load_traces",
    "UnifiedTrace",
    "normalize_trace",
    "normalize_traces",
    "SignalExtractor",
]
