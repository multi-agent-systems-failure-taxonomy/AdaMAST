"""Single-model, no-harness AdaMAST integration."""

from .runtime import (
    MessageCall,
    SingleLLMConfig,
    SingleLLMResult,
    run_single_llm,
)

__all__ = [
    "MessageCall",
    "SingleLLMConfig",
    "SingleLLMResult",
    "run_single_llm",
]
