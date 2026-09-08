"""Entry points: `load_consensus_cases` and `project_semantic_case` support the evaluator.

`load_consensus_cases` validates the committed corpus for `eval_exercise_quality`,
and `project_semantic_case` projects its model-evaluation snapshots for that
same evaluator and its focused tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from lesson_builder.workspace.paths import get_workspace_root

K_EXERCISE_QUALITY_FIXTURE = get_workspace_root() / "tests" / "fixtures" / "exercise_quality" / "consensus_cases.yaml"
K_EXERCISE_QUALITY_SCHEMA_VERSION = 1
K_EXERCISE_QUALITY_FAMILIES = frozenset(
    {
        "missing_attempt_context",
        "broad_speak_hidden_exact_target",
        "malformed_keyed_norwegian",
        "feminine_en_alternative",
        "find_fix_attempt_capability",
        "response_language_english_conflict",
        "open_criteria_omits_required_work",
        "recall_segments_missing_separator",
        "sentence_initial_keyed_lowercase",
    }
)
K_EXERCISE_QUALITY_REQUIRED_METADATA = frozenset(
    {
        "family",
        "lesson",
        "handle",
        "mode",
        "surfaces",
        "evidence",
        "label_source",
        "captured_at",
        "adversarial_category",
    }
)


def load_consensus_cases(path: Path = K_EXERCISE_QUALITY_FIXTURE) -> list[dict[str, Any]]:
    """Load and validate the committed exercise-quality corpus and provenance."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != K_EXERCISE_QUALITY_SCHEMA_VERSION:
        raise TypeError("exercise-quality fixture must declare the supported schema_version")
    raw_cases = document.get("cases")
    if not isinstance(raw_cases, list):
        raise TypeError("exercise-quality fixture must contain a cases list")
    if not all(isinstance(case, dict) for case in raw_cases):
        raise TypeError("exercise-quality cases must all be mappings")
    cases = list(raw_cases)
    for case in cases:
        _validate_case_metadata(case)
    return cases


def project_semantic_case(case: dict[str, Any], state: str = "bad") -> dict[str, Any]:
    """Return one compiled-internal lesson snapshot for a semantic case."""
    if case.get("mode") != "semantic":
        raise ValueError(f"case {case.get('family')!r} is not semantic")
    lesson = case.get(state)
    if not isinstance(lesson, dict) or not isinstance(lesson.get("elements"), list):
        raise TypeError(f"case {case.get('family')!r} has no internal {state} snapshot")
    return lesson


def _validate_case_metadata(case: dict[str, Any]) -> None:
    """Validate provenance and labeled-state fields for one corpus case."""
    missing = K_EXERCISE_QUALITY_REQUIRED_METADATA - case.keys()
    if missing:
        raise ValueError(f"exercise-quality case is missing metadata: {', '.join(sorted(missing))}")
    _require_non_empty_text(case, "captured_at")
    _require_non_empty_text(case, "label_source")
    _require_non_empty_text(case, "adversarial_category")
    if case["mode"] not in {"mechanical", "semantic"}:
        raise ValueError(f"unsupported exercise-quality case mode: {case['mode']!r}")
    if not isinstance(case["surfaces"], list) or not case["surfaces"]:
        raise TypeError("exercise-quality case surfaces must be a non-empty list")
    _require_non_empty_text(case, "evidence")
    if "bad" not in case or "good" not in case:
        raise ValueError("exercise-quality case must include bad and good states")


def _require_non_empty_text(case: dict[str, Any], field: str) -> None:
    """Require one named corpus metadata field to be non-empty text."""
    value = case[field]
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"exercise-quality case {field} must be non-empty text")


__all__ = [
    "K_EXERCISE_QUALITY_FAMILIES",
    "K_EXERCISE_QUALITY_FIXTURE",
    "K_EXERCISE_QUALITY_REQUIRED_METADATA",
    "K_EXERCISE_QUALITY_SCHEMA_VERSION",
    "load_consensus_cases",
    "project_semantic_case",
]
