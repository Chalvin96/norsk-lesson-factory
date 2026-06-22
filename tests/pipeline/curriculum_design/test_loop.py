"""Tests for the curriculum_design loop core (Phase 1).

Entry point: ``run_curriculum_loop``.

Offline: injects a fake ``research_backend`` returning canned ``ResearchNote``
lists, a fake ``fetch`` for the citation resolver (via monkeypatched
``resolve_citations``), and fake writer/reviewer agents. Asserts:

- converges when signals clear.
- escalates to ``needs_human`` when a signal persists to ``max_rounds``.
- citation resolver drops uncorroborated notes.
- hard-fails when no research backend is wired.
- ``coverage_gap`` fires for an uncovered cited goal.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from lesson_builder.pipeline.concept_requirements import Objective
from lesson_builder.pipeline.curriculum_design.loop import run_curriculum_loop
from lesson_builder.pipeline.curriculum_design.models import (
    ConceptDraft,
    CourseMap,
    ResearchNote,
)
from lesson_builder.pipeline.curriculum_design.thresholds import CurriculumThresholds

# ── fixture builders ──────────────────────────────────────────────────────


def _note(
    claim: str = "a claim",
    url: str = "https://example.com/a",
    quote: str = "quoted text",
) -> ResearchNote:
    return ResearchNote(claim=claim, url=url, quote=quote)


def _concept(
    slug: str,
    *,
    n_objectives: int = 3,
    notes: str = "teaches the concept",
    claims_covered: list[str] | None = None,
    sequence_index: int = 0,
) -> ConceptDraft:
    return ConceptDraft(
        slug=slug,
        cefr_level="A1",
        objectives=[
            Objective(id=f"o{i}", statement=f"{slug} objective {i}", bloom_targets=["understand"])
            for i in range(n_objectives)
        ],
        required_anchor_forms=[f"{slug}-anchor-{i}" for i in range(3)],
        notes=f"{notes}. Covers: {', '.join(claims_covered or [])}",
        sequence_index=sequence_index,
        source_notes=claims_covered or [],
    )


def _course_map(concepts: list[ConceptDraft]) -> CourseMap:
    return CourseMap(concepts=concepts, cefr_span="A1-A2")


def _thresholds(*, thinness_floor: int = 2, too_broad_ceiling: int = 5) -> CurriculumThresholds:
    return CurriculumThresholds(
        n_concepts=3,
        n_in_benchmark=3,
        thinness_floor=thinness_floor,
        too_broad_ceiling=too_broad_ceiling,
    )


class _FakeResearchBackend:
    """Returns canned notes per call; records queries."""

    def __init__(self, notes: Sequence[ResearchNote]) -> None:
        self._notes = list(notes)
        self.calls: list[str] = []

    def __call__(self, query: str) -> list[ResearchNote]:
        self.calls.append(query)
        return list(self._notes)


class _StatefulWriterAgent:
    """Returns a CourseMap JSON on each ``invoke`` call; can vary per round.

    The structured-output path wraps the raw invoke result, so we return JSON
    text that validates against the ``CourseMap`` schema. Call count is tracked
    so tests can make round 1 dirty and round 2 clean.
    """

    def __init__(self, maps_by_round: list[CourseMap]) -> None:
        self._maps = list(maps_by_round)
        self._index = 0
        self.calls: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.calls.append(prompt)
        current = self._maps[min(self._index, len(self._maps) - 1)]
        self._index += 1
        return current.model_dump_json()


class _FakeReviewerAgent:
    """Advisory-only agent that returns an empty list."""

    def invoke(self, prompt: str) -> str:
        return "[]"


class _StructuredAgentStub:
    """Stand-in for ``Agent.structured()`` return value.

    The loop calls ``agent.structured(CourseMap).invoke(prompt)``; this stub
    calls the writer's ``invoke`` and parses the JSON so the CourseMap schema is
    validated through the real Pydantic path.
    """

    def __init__(self, writer: _StatefulWriterAgent) -> None:
        self._writer = writer

    def invoke(self, prompt: str) -> CourseMap:
        raw = self._writer.invoke(prompt)
        return CourseMap.model_validate(json.loads(raw))


class _WriterAgentWrapper:
    """Wraps a writer so ``agent.structured(Schema)`` returns the stub."""

    def __init__(self, writer: _StatefulWriterAgent) -> None:
        self._writer = writer

    def structured(self, schema: type) -> _StructuredAgentStub:
        return _StructuredAgentStub(self._writer)


# ── tests ─────────────────────────────────────────────────────────────────


def test_run_curriculum_loop_given_clean_first_round_expect_converged(monkeypatch):
    # setup: research returns notes; resolve marks them all resolved; writer
    # produces a clean map that passes all thresholds.
    notes = [
        _note(claim="past tense", url="https://a.example", quote="past tense"),
        _note(claim="articles", url="https://b.example", quote="articles"),
    ]

    clean_map = _course_map([
        _concept("past_tense", n_objectives=3, claims_covered=["past tense"]),
        _concept("articles", n_objectives=3, claims_covered=["articles"]),
    ])
    writer = _StatefulWriterAgent([clean_map])

    # all citations corroborate
    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )

    # execute
    result = run_curriculum_loop(
        "norwegian grammar",
        research_backend=_FakeResearchBackend(notes),
        writer_agent=_WriterAgentWrapper(writer),
        reviewer_agent=_FakeReviewerAgent(),
        thresholds=_thresholds(),
        max_rounds=3,
    )

    # assert
    assert result.status == "converged"
    assert result.rounds == 1
    assert result.signals.is_clean() is True
    assert len(result.course_map.concepts) == 2


def test_run_curriculum_loop_given_persistent_signal_expect_needs_human(monkeypatch):
    # setup: the writer ALWAYS produces a thin concept (1 objective, floor=2).
    # The signal never clears -> needs_human at max_rounds.
    notes = [_note(claim="topic", url="https://a.example", quote="topic")]

    thin_map = _course_map([_concept("thin_one", n_objectives=1, claims_covered=["topic"])])
    writer = _StatefulWriterAgent([thin_map])  # same thin map every round

    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )

    # execute
    result = run_curriculum_loop(
        "topic",
        research_backend=_FakeResearchBackend(notes),
        writer_agent=_WriterAgentWrapper(writer),
        reviewer_agent=_FakeReviewerAgent(),
        thresholds=_thresholds(thinness_floor=2),
        max_rounds=3,
    )

    # assert: signal persists -> needs_human, signals carry the thin hit.
    assert result.status == "needs_human"
    assert result.rounds <= 3
    assert "thin_one" in result.signals.thinness_hits


def test_run_curriculum_loop_given_signal_then_fix_expect_converged_round_2(monkeypatch):
    # setup: round 1 produces a thin concept; round 2 produces a fixed map.
    notes = [_note(claim="topic", url="https://a.example", quote="topic")]

    thin_map = _course_map([_concept("concept_a", n_objectives=1, claims_covered=["topic"])])
    fixed_map = _course_map([_concept("concept_a", n_objectives=3, claims_covered=["topic"])])
    writer = _StatefulWriterAgent([thin_map, fixed_map])

    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )

    # execute
    result = run_curriculum_loop(
        "topic",
        research_backend=_FakeResearchBackend(notes),
        writer_agent=_WriterAgentWrapper(writer),
        reviewer_agent=_FakeReviewerAgent(),
        thresholds=_thresholds(thinness_floor=2),
        max_rounds=3,
    )

    # assert: converged on round 2 after the fix.
    assert result.status == "converged"
    assert result.rounds == 2
    assert result.signals.is_clean() is True


def test_run_curriculum_loop_given_no_backend_wired_expect_hard_fail():
    # setup + execute + assert: no research backend -> hard-fail.
    with pytest.raises(RuntimeError, match="no research backend wired"):
        run_curriculum_loop(
            "any seed",
            thresholds=_thresholds(),
            max_rounds=1,
        )


def test_run_curriculum_loop_given_uncorroborated_notes_expect_dropped(monkeypatch):
    # setup: two notes — one corroborated, one not. The writer should only
    # see the corroborated note. The coverage check should flag the dropped
    # claim as a gap (no concept covers it).
    notes = [
        _note(claim="covered_claim", url="https://ok.example", quote="ok quote"),
        _note(claim="dropped_claim", url="https://bad.example", quote="bad quote"),
    ]

    # resolve_citations marks only the first note resolved.
    def fake_resolve(notes_list, **kw):
        return [
            notes_list[0].model_copy(update={"resolved": True}),
            notes_list[1].model_copy(update={"resolved": False}),
        ]

    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        fake_resolve,
    )

    # writer produces a map covering only the corroborated claim.
    map_with_one = _course_map([
        _concept("covers_ok", n_objectives=3, claims_covered=["covered_claim"]),
    ])
    writer = _StatefulWriterAgent([map_with_one])

    # execute
    result = run_curriculum_loop(
        "seed",
        research_backend=_FakeResearchBackend(notes),
        writer_agent=_WriterAgentWrapper(writer),
        reviewer_agent=_FakeReviewerAgent(),
        thresholds=_thresholds(),
        max_rounds=1,
    )

    # assert: dropped_claim is NOT a cited goal (only resolved claims are);
    # covered_claim IS covered. So coverage is clean -> converged.
    # This proves uncorroborated notes are dropped (their claims do not gate coverage).
    assert "dropped_claim" not in result.signals.coverage_gaps
    assert result.signals.coverage_gaps == []


def test_run_curriculum_loop_given_uncovered_resolved_claim_expect_coverage_gap(monkeypatch):
    # setup: a resolved note whose claim is NOT covered by any concept.
    notes = [
        _note(claim="covered_topic", url="https://a.example", quote="a"),
        _note(claim="uncovered_topic", url="https://b.example", quote="b"),
    ]

    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes_list, **kw: [n.model_copy(update={"resolved": True}) for n in notes_list],
    )

    # writer produces a map covering only "covered_topic".
    partial_map = _course_map([
        _concept("covers_one", n_objectives=3, claims_covered=["covered_topic"]),
    ])
    writer = _StatefulWriterAgent([partial_map])

    # execute
    result = run_curriculum_loop(
        "seed",
        research_backend=_FakeResearchBackend(notes),
        writer_agent=_WriterAgentWrapper(writer),
        reviewer_agent=_FakeReviewerAgent(),
        thresholds=_thresholds(),
        max_rounds=1,
    )

    # assert: coverage_gap fires for the uncovered resolved claim -> needs_human.
    assert result.status == "needs_human"
    assert "uncovered_topic" in result.signals.coverage_gaps
    assert "covered_topic" not in result.signals.coverage_gaps


def test_run_curriculum_loop_given_too_broad_signal_expect_needs_human(monkeypatch):
    # setup: a concept with too many objectives (> ceiling).
    notes = [_note(claim="topic", url="https://a.example", quote="topic")]

    broad_map = _course_map([_concept("broad_one", n_objectives=6, claims_covered=["topic"])])
    writer = _StatefulWriterAgent([broad_map])

    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes_list, **kw: [n.model_copy(update={"resolved": True}) for n in notes_list],
    )

    # execute
    result = run_curriculum_loop(
        "topic",
        research_backend=_FakeResearchBackend(notes),
        writer_agent=_WriterAgentWrapper(writer),
        reviewer_agent=_FakeReviewerAgent(),
        thresholds=_thresholds(thinness_floor=2, too_broad_ceiling=5),
        max_rounds=3,
    )

    # assert: too_broad persists -> needs_human.
    assert result.status == "needs_human"
    assert "broad_one" in result.signals.too_broad_hits


def test_run_curriculum_loop_given_repeated_fingerprint_expect_early_escalation(monkeypatch):
    # setup: the same thin signal fires every round; the loop should detect the
    # repeated fingerprint and escalate before burning all rounds.
    notes = [_note(claim="topic", url="https://a.example", quote="topic")]

    thin_map = _course_map([_concept("thin_one", n_objectives=1, claims_covered=["topic"])])
    writer = _StatefulWriterAgent([thin_map])

    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes_list, **kw: [n.model_copy(update={"resolved": True}) for n in notes_list],
    )

    # execute
    result = run_curriculum_loop(
        "topic",
        research_backend=_FakeResearchBackend(notes),
        writer_agent=_WriterAgentWrapper(writer),
        reviewer_agent=_FakeReviewerAgent(),
        thresholds=_thresholds(thinness_floor=2),
        max_rounds=5,
    )

    # assert: repeated fingerprint detected on round 2 -> early escalation.
    assert result.status == "needs_human"
    assert result.rounds == 2  # not 5 — the no-progress guard fires early


def test_run_curriculum_loop_given_prior_signals_in_prompt_expect_revision_guidance(monkeypatch):
    # setup: verify that prior_signals from round 1 are folded into the round 2
    # prompt (the writer sees the revision guidance).
    notes = [_note(claim="topic", url="https://a.example", quote="topic")]

    thin_map = _course_map([_concept("concept_a", n_objectives=1, claims_covered=["topic"])])
    fixed_map = _course_map([_concept("concept_a", n_objectives=3, claims_covered=["topic"])])
    writer = _StatefulWriterAgent([thin_map, fixed_map])

    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes_list, **kw: [n.model_copy(update={"resolved": True}) for n in notes_list],
    )

    # execute
    run_curriculum_loop(
        "topic",
        research_backend=_FakeResearchBackend(notes),
        writer_agent=_WriterAgentWrapper(writer),
        reviewer_agent=_FakeReviewerAgent(),
        thresholds=_thresholds(thinness_floor=2),
        max_rounds=3,
    )

    # assert: the round-2 prompt contains revision guidance referencing the thin hit.
    round_2_prompt = writer.calls[1]
    assert "Revision guidance" in round_2_prompt
    assert "concept_a" in round_2_prompt


def test_run_curriculum_loop_given_invalid_max_rounds_expect_value_error():
    # setup + execute + assert
    with pytest.raises(ValueError, match="max_rounds must be >= 1"):
        run_curriculum_loop(
            "seed",
            research_backend=_FakeResearchBackend([]),
            thresholds=_thresholds(),
            max_rounds=0,
        )
