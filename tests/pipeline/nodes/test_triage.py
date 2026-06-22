"""Tests for triage_request: LLM-grounded placement over the lesson catalog."""

from __future__ import annotations

import json
from pathlib import Path

from lesson_builder.pipeline.nodes.parse_request import ImprovementSpec
from lesson_builder.pipeline.nodes.triage import TriageClassification, triage_request
from lesson_builder.pipeline.store.index import LessonIndex


class _FakeTriageAgent:
    """Offline triage agent returning a canned routing verdict."""

    def __init__(self, route: str, matched_slug: str | None = None) -> None:
        self._classification = TriageClassification(
            route=route, matched_slug=matched_slug, rationale="test triage"
        )

    def structured(self, schema: object) -> _FakeTriageAgent:
        return self

    def invoke(self, prompt: str) -> TriageClassification:
        return self._classification


def _write_lesson(tmp_path: Path, slug: str, prompt_text: str) -> None:
    lessons = tmp_path / "lessons"
    lessons.mkdir(exist_ok=True)
    lesson = {
        "key": slug,
        "concept_slug": slug,
        "grounding_mode": "authored_golden",
        "title": slug,
        "cefr_level": "A1",
        "goal": "Learn",
        "objectives": [{"id": "o1", "statement": prompt_text, "bloom_targets": ["understand"]}],
        "elements": [
            {
                "element_kind": "exercise",
                "id": "ex1",
                "operation": "judge",
                "objective_id": "o1",
                "bloom_level": "understand",
                "derived_from": [],
                "prompt": [{"kind": "text", "value": prompt_text}],
                "explanation": "ex",
                "payload": {
                    "sentence": [{"kind": "text", "value": "Test"}],
                    "is_correct": True,
                    "feedback": "f",
                },
            }
        ],
        "review_pool": {
            "pools": [
                {
                    "key": "o1",
                    "objective_id": "o1",
                    "cards": [{"uuid": "00000000-0000-0000-0000-000000000001", "exercise_id": "ex1"}],
                }
            ]
        },
    }
    (lessons / f"{slug}.json").write_text(json.dumps(lesson))


def test_triage_given_existing_match_expect_exists(tmp_path: Path):
    _write_lesson(tmp_path, "word_order", "word order in main clauses V2 rule")
    db = tmp_path / "index.db"
    idx = LessonIndex(db)
    idx.hydrate(tmp_path / "lessons")
    spec = ImprovementSpec(
        operation="add_exercises",
        target_slug="word_order",
        target_content="word order main clauses",
    )
    result = triage_request(spec, idx, triage_agent=_FakeTriageAgent("patch_lesson", "word_order"))
    assert result.verdict == "exists"
    assert result.proposed_action == "patch_lesson"
    idx.close()


def test_triage_given_no_match_expect_new(tmp_path: Path):
    _write_lesson(tmp_path, "past_tense", "past tense verb formation")
    db = tmp_path / "index.db"
    idx = LessonIndex(db)
    idx.hydrate(tmp_path / "lessons")
    spec = ImprovementSpec(
        operation="add_exercises",
        target_slug=None,
        target_content="quantum physics phenomena",
    )
    result = triage_request(spec, idx, triage_agent=_FakeTriageAgent("new_topic"))
    assert result.verdict == "new"
    assert result.proposed_action == "new_topic"
    idx.close()


def test_triage_given_empty_index_expect_new(tmp_path: Path):
    db = tmp_path / "index.db"
    idx = LessonIndex(db)
    spec = ImprovementSpec(operation="add_exercises", target_content="anything")
    result = triage_request(spec, idx)
    assert result.verdict == "new"
    idx.close()
