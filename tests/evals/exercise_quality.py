"""Entry points: `load_consensus_cases` and `review_case_lesson` support eval tests."""

from __future__ import annotations

from typing import Any

from scripts.eval_corpus import K_EXERCISE_QUALITY_FAMILIES
from scripts.eval_corpus import K_EXERCISE_QUALITY_FIXTURE
from scripts.eval_corpus import K_EXERCISE_QUALITY_REQUIRED_METADATA
from scripts.eval_corpus import K_EXERCISE_QUALITY_SCHEMA_VERSION
from scripts.eval_corpus import load_consensus_cases
from scripts.eval_corpus import project_semantic_case

K_EXPECTED_MECHANICAL_CODES = {
    "broad_speak_hidden_exact_target": "speak-target-cue-broad",
    "find_fix_attempt_capability": "find-fix-attempt-capability",
    "response_language_english_conflict": "write-response-language-conflict",
    "recall_segments_missing_separator": "recall-rendered-boundary-missing",
    "sentence_initial_keyed_lowercase": "recall-sentence-start-lowercase",
}


def review_case_lesson(case: dict[str, Any], state: str) -> dict[str, Any]:
    """Return the compiled review snapshot, including mechanical cases."""
    snapshot = case.get(f"review_{state}")
    return snapshot if isinstance(snapshot, dict) else project_semantic_case(case, state)


__all__ = [
    "K_EXERCISE_QUALITY_FAMILIES",
    "K_EXERCISE_QUALITY_FIXTURE",
    "K_EXERCISE_QUALITY_REQUIRED_METADATA",
    "K_EXERCISE_QUALITY_SCHEMA_VERSION",
    "K_EXPECTED_MECHANICAL_CODES",
    "load_consensus_cases",
    "review_case_lesson",
    "project_semantic_case",
]
