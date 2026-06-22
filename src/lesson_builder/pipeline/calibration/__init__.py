"""Reviewer calibration package.

A single, corpus-free approach: derive per-axis quality FLOORS from the reviewer's
scores on the golden lessons (``rubric_floors``) and gate any lesson scoring below
a floor. The earlier f1/seeded-defect harness was removed — measuring the live
judges' issue-detection precision/recall proved the issue lists over-flag (recall
1.0, precision ~0.25), so issue findings stay advisory and the SCORES carry the
load-bearing gate instead. See the ``project_calibration_corpus_mismatch`` memory.
"""

from lesson_builder.pipeline.calibration.rubric_floors import (
    RubricFloors,
    compute_rubric_floors,
    load_rubric_floors,
    rubric_floor_check,
    save_rubric_floors,
)
from lesson_builder.pipeline.calibration.rubric_run import (
    K_RUBRIC_GOLDEN_SLUGS,
    run_rubric_calibration,
)

__all__ = [
    "K_RUBRIC_GOLDEN_SLUGS",
    "RubricFloors",
    "compute_rubric_floors",
    "load_rubric_floors",
    "rubric_floor_check",
    "run_rubric_calibration",
    "save_rubric_floors",
]
