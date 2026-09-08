"""Tests for the observable deterministic lesson-gate results."""

from __future__ import annotations

import json
from typing import Any

from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from lesson_builder.domain.lesson.validation.checks.gate_manager import gate_lesson_results
from tests.paths import K_VALID_LESSON_SCHEMA_PATH


def _load_lesson() -> dict[str, Any]:
    return json.loads(K_VALID_LESSON_SCHEMA_PATH.read_text())


def test_gate_lesson_results_given_valid_fixture_expect_bloom_warning_for_apply_gap():
    lesson = _load_lesson()
    results = gate_lesson_results(lesson, terminology_bans=TerminologyBans())
    bloom_results = [
        (r.check_id, r.unit_id, r.severity, r.advisory) for r in results if r.check_id == "bloom_alignment"
    ]
    assert bloom_results == [("bloom_alignment", "o1", "warning", False)]


def test_gate_lesson_results_given_terminology_ban_expect_blocker():
    lesson = _load_lesson()
    lesson["elements"][0]["blocks"][0]["spans"][0]["value"] = "Use the tense-carrying verb."

    results = gate_lesson_results(
        lesson,
        terminology_bans=TerminologyBans(banned_phrases=["tense-carrying verb"]),
    )

    terminology_results = [result for result in results if result.check_id == "terminology_banned"]
    assert len(terminology_results) == 1
    assert terminology_results[0].is_blocking is True
