"""AdaMAST Reflection Judge — multi-stage trace-analysis judge.

The LLM transport routes through adamast's existing
``adamast.llm.learning_calls.support_model_call`` (Anthropic + OpenAI +
Gemini, env-driven). ``judge_model`` is a required parameter at construction
time; there is no hidden default model.

Public API:

    AdaMASTReflectionJudge   : the orchestration class.
    validate_output        : schema validator for the judge's output.
    derive_selection_summary: deterministic compression from failure points
                              + relations to the selection-oriented summary.

The judge identifies failure POINTS (concrete trace locations), builds a
backward-grounded causal graph between them, and only AFTER that assigns
taxonomy codes. See ``prompts.py`` for the exact LLM instructions.
"""

from .judge import AdaMASTReflectionJudge
from .schema import validate_output
from .selection import derive_selection_summary

__all__ = ["AdaMASTReflectionJudge", "validate_output", "derive_selection_summary"]
