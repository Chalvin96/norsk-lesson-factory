"""Tests for the curriculum_design review stage (Phase 1).

Entry point: ``review_map``.

Offline: builds ``CourseMap`` / ``CurriculumThresholds`` fixtures directly and
asserts the DETERMINISTIC signals (thinness, too_broad, coverage_gaps) are
load-bearing. The LLM advisory is only exercised through an injectable fake
agent that returns a canned JSON list.
"""

from __future__ import annotations

import json

from lesson_builder.pipeline.concept_requirements import Objective
from lesson_builder.pipeline.curriculum_design.models import (
    ConceptDraft,
    CourseMap,
)
from lesson_builder.pipeline.curriculum_design.review import review_map
from lesson_builder.pipeline.curriculum_design.thresholds import CurriculumThresholds


def _concept(
    slug: str,
    *,
    n_objectives: int,
    cefr_level: str = "A1",
    notes: str = "teaches the concept",
    anchors: list[str] | None = None,
) -> ConceptDraft:
    return ConceptDraft(
        slug=slug,
        cefr_level=cefr_level,
        objectives=[
            Objective(id=f"o{i}", statement=f"{slug} objective {i}", bloom_targets=["understand"])
            for i in range(n_objectives)
        ],
        required_anchor_forms=anchors or [f"{slug}-anchor-{i}" for i in range(3)],
        notes=notes,
        sequence_index=0,
        source_notes=[],
    )


def _thresholds(*, thinness_floor: int = 2, too_broad_ceiling: int = 5) -> CurriculumThresholds:
    return CurriculumThresholds(
        n_concepts=3,
        n_in_benchmark=3,
        thinness_floor=thinness_floor,
        too_broad_ceiling=too_broad_ceiling,
    )


def test_review_map_given_clean_map_expect_no_deterministic_signals():
    # setup: 3 objectives per concept, floors are 2-5, cited goals covered by concept text.
    course_map = CourseMap(
        concepts=[
            _concept("past_tense", n_objectives=3, notes="past tense formation"),
            _concept("articles", n_objectives=3, notes="definite articles"),
        ],
        cefr_span="A1-A2",
    )
    thresholds = _thresholds(thinness_floor=2, too_broad_ceiling=5)
    cited_goals = ["past tense", "definite articles"]

    # execute
    signals = review_map(course_map, thresholds, cited_goals)

    # assert
    assert signals.thinness_hits == []
    assert signals.too_broad_hits == []
    assert signals.coverage_gaps == []
    assert signals.advisory == []
    assert signals.is_clean() is True


def test_review_map_given_thin_concept_expect_thinness_hit():
    # setup: one concept with 1 objective, floor is 2.
    course_map = CourseMap(
        concepts=[
            _concept("thin_concept", n_objectives=1),
            _concept("ok_concept", n_objectives=3),
        ],
        cefr_span="A1-A2",
    )
    thresholds = _thresholds(thinness_floor=2, too_broad_ceiling=5)

    # execute
    signals = review_map(course_map, thresholds, cited_goals=[])

    # assert
    assert signals.thinness_hits == ["thin_concept"]
    assert "ok_concept" not in signals.thinness_hits
    assert signals.is_clean() is False


def test_review_map_given_broad_concept_expect_too_broad_hit():
    # setup: one concept with 6 objectives, ceiling is 5.
    course_map = CourseMap(
        concepts=[
            _concept("broad_concept", n_objectives=6),
            _concept("ok_concept", n_objectives=3),
        ],
        cefr_span="A1-B1",
    )
    thresholds = _thresholds(thinness_floor=2, too_broad_ceiling=5)

    # execute
    signals = review_map(course_map, thresholds, cited_goals=[])

    # assert
    assert signals.too_broad_hits == ["broad_concept"]
    assert "ok_concept" not in signals.too_broad_hits
    assert signals.is_clean() is False


def test_review_map_given_uncovered_cited_goal_expect_coverage_gap():
    # setup: cited goal "future tense" is NOT covered by any concept.
    course_map = CourseMap(
        concepts=[
            _concept("past_tense", n_objectives=3, notes="past tense formation"),
            _concept("present_tense", n_objectives=3, notes="present tense"),
        ],
        cefr_span="A1-A2",
    )
    thresholds = _thresholds(thinness_floor=2, too_broad_ceiling=5)
    cited_goals = ["past tense", "future tense"]

    # execute
    signals = review_map(course_map, thresholds, cited_goals)

    # assert: "future tense" is a gap; "past tense" is covered.
    assert signals.coverage_gaps == ["future tense"]
    assert signals.is_clean() is False


def test_review_map_given_covered_goal_via_objective_statement_expect_no_gap():
    # setup: goal text appears in an objective statement (not just notes).
    course_map = CourseMap(
        concepts=[
            _concept(
                "modal_verbs",
                n_objectives=2,
                notes="unrelated text",
                anchors=["modal"],
            ),
        ],
        cefr_span="A1-A2",
    )
    # Override objectives to include the goal text in a statement.
    course_map.concepts[0] = course_map.concepts[0].model_copy(
        update={
            "objectives": [
                Objective(id="o0", statement="use modal verbs for possibility", bloom_targets=["apply"]),
                Objective(id="o1", statement="another objective", bloom_targets=["understand"]),
            ]
        }
    )
    thresholds = _thresholds(thinness_floor=2, too_broad_ceiling=5)
    cited_goals = ["modal verbs possibility"]

    # execute
    signals = review_map(course_map, thresholds, cited_goals)

    # assert: goal tokens "modal", "verbs", "possibility" all appear in the objective.
    assert signals.coverage_gaps == []


def test_review_map_given_advisory_agent_expect_advisory_populated():
    # setup: a fake advisory agent returning canned findings.
    course_map = CourseMap(
        concepts=[_concept("ok", n_objectives=3)],
        cefr_span="A1-A2",
    )
    thresholds = _thresholds(thinness_floor=2, too_broad_ceiling=5)

    class _FakeAgent:
        def invoke(self, prompt: str) -> str:
            return json.dumps(["consider reordering concepts", "A1 before A2 is fine"])

    # execute
    signals = review_map(course_map, thresholds, cited_goals=[], agent=_FakeAgent())

    # assert: advisory is populated but does NOT affect is_clean.
    assert len(signals.advisory) == 2
    assert "consider reordering concepts" in signals.advisory
    assert signals.is_clean() is True


def test_review_map_given_advisory_agent_raises_expect_swallowed_not_blocking():
    # setup: advisory agent raises; review must not break.
    course_map = CourseMap(
        concepts=[_concept("ok", n_objectives=3)],
        cefr_span="A1-A2",
    )
    thresholds = _thresholds(thinness_floor=2, too_broad_ceiling=5)

    class _ExplodingAgent:
        def invoke(self, prompt: str) -> str:
            raise RuntimeError("backend down")

    # execute
    signals = review_map(course_map, thresholds, cited_goals=[], agent=_ExplodingAgent())

    # assert: advisory records the failure but deterministic signals are unaffected.
    assert any("advisory agent unavailable" in a for a in signals.advisory)
    assert signals.is_clean() is True


def test_review_map_given_zero_floors_expect_no_deterministic_hits():
    # setup: empty CurriculumThresholds (all zeros) -> no floors -> no hits.
    course_map = CourseMap(
        concepts=[_concept("any", n_objectives=0 + 1)],  # min_length=1 on objectives
        cefr_span="A1-A2",
    )
    thresholds = CurriculumThresholds()

    # execute
    signals = review_map(course_map, thresholds, cited_goals=[])

    # assert: no floors loaded -> no deterministic signals fire.
    assert signals.thinness_hits == []
    assert signals.too_broad_hits == []
    assert signals.is_clean() is True
