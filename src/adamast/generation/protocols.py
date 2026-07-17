"""Stable contracts shared by AdaMAST taxonomy-generation strategies."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(frozen=True)
class GenerationRequest:
    """Inputs that every taxonomy-generation strategy receives."""

    traces: Path
    output: Path
    provider: str
    model: str
    open_viewer: bool = False
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerationResult:
    """Durable result returned by a taxonomy-generation strategy."""

    strategy: str
    status: str
    taxonomy_path: Path
    manifest_path: Path
    viewer_path: Path | None
    summary: Mapping[str, Any]

    @property
    def accepted(self) -> bool:
        return self.status == "accepted"


@runtime_checkable
class GenerationStrategy(Protocol):
    """Interface implemented by each named AdaMAST generation method."""

    name: str

    def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate, validate, and persist one taxonomy result."""
