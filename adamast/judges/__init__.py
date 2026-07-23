"""AdaMAST skill judge types.

The simple LLM judges are natural-language assets under ``adamast/judges/assets``
run by ``JudgeController``. Reflection remains a deeper orchestrated judge, and
selection-summary remains deterministic Python.

:mod:`adamast.judges.contract` carries the provider-neutral JUDGES layer for
applying a finished taxonomy to new traces (one shared judge base plus the
single alien-code filter policy); its surface is re-exported here.
"""

from __future__ import annotations

from .contract import (
    Diagnosis,
    JudgeResponseError,
    TaxonomyJudge,
    create_judge,
    judge_trace,
    judge_traces,
)
from .simple import (
    CalibrationJudge,
    CalibrationJudgeResult,
    CoverageJudge,
    CoverageJudgeResult,
    JudgeController,
    MappingJudge,
    MappingJudgeResult,
    QualityJudge,
    QualityJudgeResult,
    SelectionJudge,
    SelectionJudgeResult,
    SIMPLE_JUDGE_TYPES,
    load_judge_definition,
    render_judge_prompt,
    run_calibration,
    run_coverage,
    run_mapping,
    run_quality,
    run_selection,
)

REAL = (
    "selection",
    "reflection_judge",
    "mapping",
    "coverage",
    "quality",
    "calibration",
    "selection_summary_judge",
)
PLACEHOLDER: tuple[str, ...] = ()
ALL = REAL + PLACEHOLDER

__all__ = [
    "REAL",
    "PLACEHOLDER",
    "ALL",
    "Diagnosis",
    "JudgeResponseError",
    "TaxonomyJudge",
    "create_judge",
    "judge_trace",
    "judge_traces",
    "SIMPLE_JUDGE_TYPES",
    "JudgeController",
    "load_judge_definition",
    "render_judge_prompt",
    "SelectionJudge",
    "SelectionJudgeResult",
    "MappingJudge",
    "MappingJudgeResult",
    "CoverageJudge",
    "CoverageJudgeResult",
    "QualityJudge",
    "QualityJudgeResult",
    "CalibrationJudge",
    "CalibrationJudgeResult",
    "run_selection",
    "run_mapping",
    "run_coverage",
    "run_quality",
    "run_calibration",
]
