"""Compatibility re-export for the shared AdaMAST reflection parser."""

from adamast.core.reflection import (
    CodeAssignment,
    ReflectionResult,
    parse_reflection,
)

__all__ = ["CodeAssignment", "ReflectionResult", "parse_reflection"]
