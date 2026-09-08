"""Entry points: `audit_snapshot` and `assert_semantic_projection` support eval tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from lesson_builder.application.operations.audit_source import audit_source_directory
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from lesson_builder.domain.lesson.validation.review_payloads import extract_answer_questions
from lesson_builder.domain.lesson.validation.review_payloads import extract_attempt_questions
from lesson_builder.domain.lesson.validation.review_payloads import extract_open_rubric_questions
from tests.evals.exercise_quality import review_case_lesson


def audit_snapshot(root: Path, snapshot: dict[str, str]) -> MechanicalAudit:
    """Write one fixture snapshot below ``tmp_path`` and run the production audit."""
    root.mkdir(parents=True)
    for filename, content in snapshot.items():
        (root / filename).write_text(content, encoding="utf-8")
    return audit_source_directory(root)


def assert_semantic_projection(case: dict[str, Any], state: str) -> None:
    """Assert that a semantic fixture preserves every learner-facing projection."""
    lesson = review_case_lesson(case, state)
    attempt = extract_attempt_questions(lesson)
    answers = extract_answer_questions(lesson)
    rubrics = extract_open_rubric_questions(lesson)
    assert [question["id"] for question in attempt] == [case["handle"]]
    assert [question["id"] for question in answers] == [case["handle"]]
    if case["handle"] in {
        "isolating-an-unfamiliar-word",
        "reconstruct_the_meeting_route",
        "practice_4_understand_and_accept",
    }:
        assert [question["id"] for question in rubrics] == [case["handle"]]
    rendered_attempt = json.dumps(attempt, ensure_ascii=False)
    assert "derived_from" not in rendered_attempt
    assert "criteria" not in rendered_attempt
    assert "judge_prompt" not in rendered_attempt
    assert "target" not in rendered_attempt


__all__ = ["assert_semantic_projection", "audit_snapshot"]
