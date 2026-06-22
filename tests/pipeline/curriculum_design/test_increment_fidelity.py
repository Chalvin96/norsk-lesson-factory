"""Loop-fidelity test for the curriculum increment (Phase 3 de-risk).

Entry points: ``run_increment`` (increment) + ``author_metadata_objectives``
(cold-author Stage 1 consumer).

This is the de-risk both reviewers required: run increment-mode for a concept
and confirm the produced ``ConceptRequirements`` is gate-clean-shaped AND
CONSUMABLE by cold-author with NO shape adapter (``author_metadata_objectives``
accepts it directly, returns ``StageOK``, and the resulting ``ColdMetadata``
carries the card's CEFR + objectives verbatim).

Offline: injects fakes for every collaborator (triage, research backend,
writer/reviewer agents) and monkeypatches ``resolve_citations`` so no network
is hit. Exercises the full ``run_increment -> run_curriculum_loop`` path
end-to-end.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from lesson_builder.pipeline.cold_author.metadata_objectives import (
    author_metadata_objectives,
)
from lesson_builder.pipeline.cold_author.models import StageOK
from lesson_builder.pipeline.concept_requirements import Objective
from lesson_builder.pipeline.curriculum_design.increment import run_increment
from lesson_builder.pipeline.curriculum_design.models import (
    ConceptDraft,
    CourseMap,
    ResearchNote,
)
from lesson_builder.pipeline.nodes.triage import TriageClassification

K_TEST_ROOT = Path(__file__).resolve().parents[3]


# ── Offline fakes ─────────────────────────────────────────────────────────


class _FakeTriageAgent:
    def __init__(self, route: str, matched_slug: str | None = None) -> None:
        self._classification = TriageClassification(
            route=route, matched_slug=matched_slug, rationale="test triage"
        )

    def structured(self, schema: object) -> _FakeTriageAgent:
        return self

    def invoke(self, prompt: str) -> TriageClassification:
        return self._classification


class _FakeResearchBackend:
    def __init__(self, notes: Sequence[ResearchNote]) -> None:
        self._notes = list(notes)

    def __call__(self, query: str) -> list[ResearchNote]:
        return list(self._notes)


class _StatefulWriterAgent:
    def __init__(self, maps_by_round: list[CourseMap]) -> None:
        self._maps = list(maps_by_round)
        self._index = 0

    def invoke(self, prompt: str) -> str:
        current = self._maps[min(self._index, len(self._maps) - 1)]
        self._index += 1
        return current.model_dump_json()


class _StructuredAgentStub:
    def __init__(self, writer: _StatefulWriterAgent) -> None:
        self._writer = writer

    def invoke(self, prompt: str) -> CourseMap:
        raw = self._writer.invoke(prompt)
        return CourseMap.model_validate(json.loads(raw))


class _WriterAgentWrapper:
    def __init__(self, writer: _StatefulWriterAgent) -> None:
        self._writer = writer

    def structured(self, schema: type) -> _StructuredAgentStub:
        return _StructuredAgentStub(self._writer)


class _FakeReviewerAgent:
    def invoke(self, prompt: str) -> str:
        return "[]"


class _FakeColdAuthorAgent:
    """Returns valid title+goal JSON for cold-author Stage 1."""

    def invoke(self, prompt: str) -> str:
        return json.dumps({"title": "Test Lesson", "goal": "Learn the concept."})


# ── Fixture builders ──────────────────────────────────────────────────────


def _gate_clean_concept(
    slug: str,
    *,
    claims_covered: list[str],
) -> ConceptDraft:
    """A concept draft that passes all deterministic review signals.

    - >= 1 objective (meets thinness_floor=1 from the 104-derived thresholds).
    - <= 4 objectives (under too_broad_ceiling=4).
    - bloom_targets yield >= 2 eligible operations per objective.
    - notes cover all cited claims (no coverage gaps).
    """
    return ConceptDraft(
        slug=slug,
        cefr_level="A2",
        objectives=[
            Objective(
                id="o1",
                statement=f"{slug} core usage with {', '.join(claims_covered)}",
                bloom_targets=["understand", "apply"],
            ),
            Objective(
                id="o2",
                statement=f"{slug} recognition in context",
                bloom_targets=["remember"],
            ),
        ],
        required_anchor_forms=[f"{slug}_form_one", f"{slug}_form_two"],
        notes=f"Teaches {slug}. Covers: {', '.join(claims_covered)}.",
        sequence_index=0,
        source_notes=claims_covered,
    )


def _course_map(concepts: list[ConceptDraft]) -> CourseMap:
    return CourseMap(concepts=concepts, cefr_span="A1-A2")


def _write_repo_lesson(tmp_path: Path, slug: str) -> Path:
    repo_root = tmp_path
    lesson_payload = json.loads(
        (K_TEST_ROOT / "data" / "lessons" / f"{slug}.json").read_text(encoding="utf-8")
    )
    requirements_payload = json.loads(
        (K_TEST_ROOT / "data" / "concept_requirements" / f"{slug}.json").read_text(encoding="utf-8")
    )
    (repo_root / "data" / "lessons").mkdir(parents=True, exist_ok=True)
    (repo_root / "data" / "concept_requirements").mkdir(parents=True, exist_ok=True)
    (repo_root / "data" / "lessons" / f"{slug}.json").write_text(
        json.dumps(lesson_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (repo_root / "data" / "concept_requirements" / f"{slug}.json").write_text(
        json.dumps(requirements_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (repo_root / "store").mkdir(parents=True, exist_ok=True)
    return repo_root


# ── Tests ─────────────────────────────────────────────────────────────────


def test_run_increment_given_converged_loop_expect_card_consumable_by_cold_author_no_adapter(
    tmp_path: Path, monkeypatch
):
    """The de-risk: a converged increment produces a ``ConceptRequirements`` that
    cold-author Stage 1 accepts DIRECTLY (no shape adapter) and returns ``StageOK``.

    This proves the loop before bootstrap: a concept card out of the Phase-1 loop
    is structurally authorable end-to-end.
    """
    # setup: a repo with one existing lesson, offline collaborators.
    repo_root = _write_repo_lesson(tmp_path, "word_order_main_clauses")
    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )

    cited_claim = "past tense V2 inversion"
    note = ResearchNote(
        claim=cited_claim,
        url="https://example.com/past-v2",
        quote=cited_claim,
    )
    course_map = _course_map([
        _gate_clean_concept("word_order_main_clauses", claims_covered=[cited_claim]),
    ])

    # execute: run the increment to convergence.
    result = run_increment(
        "update the topic about word order main clauses",
        repo_root=repo_root,
        slug="word_order_main_clauses",
        run_id="fidelity1",
        triage_agent=_FakeTriageAgent("patch_lesson", "word_order_main_clauses"),
        research_backend=_FakeResearchBackend([note]),
        writer_agent=_WriterAgentWrapper(_StatefulWriterAgent([course_map])),
        reviewer_agent=_FakeReviewerAgent(),
    )

    # assert 1: the increment converged and produced a ConceptRequirements card.
    assert result.status == "parked_for_review"
    assert result.concept is not None
    concept = result.concept

    # assert 2: the card is gate-clean-shaped (full ConceptRequirements, not stub).
    assert concept.slug == "word_order_main_clauses"
    assert concept.cefr_level in {"A1", "A2", "B1", "B2", "C1", "C2"}
    assert len(concept.objectives) >= 1
    for obj in concept.objectives:
        assert obj.id
        assert obj.statement
        assert len(obj.bloom_targets) >= 1
        # Every bloom target yields >= MIN_EXERCISES_PER_OBJECTIVE eligible ops.
        for bloom in obj.bloom_targets:
            assert bloom in {"remember", "understand", "apply", "analyze"}
    assert len(concept.required_anchor_forms) >= 1
    assert concept.notes

    # assert 3: CONSUMABLE by cold-author with NO shape adapter.
    # ``author_metadata_objectives`` takes ``ConceptRequirements`` directly and
    # returns ``StageOK`` (not ``StageFailure``) when the card is authorable.
    stage_result = author_metadata_objectives(
        concept.slug,
        concept,
        author_agent=_FakeColdAuthorAgent(),
    )
    assert isinstance(stage_result, StageOK)
    metadata = stage_result.payload
    # CEFR + objectives are carried verbatim from the card (the card is the authority).
    assert metadata.cefr_level == concept.cefr_level
    assert len(metadata.objectives) == len(concept.objectives)
    for card_obj, metadata_obj in zip(concept.objectives, metadata.objectives, strict=True):
        assert metadata_obj.id == card_obj.id
        assert metadata_obj.statement == card_obj.statement
        assert list(metadata_obj.bloom_targets) == list(card_obj.bloom_targets)


def test_run_increment_given_new_topic_loop_expect_card_consumable_by_cold_author_no_adapter(
    tmp_path: Path, monkeypatch
):
    """Same de-risk for the new_topic route (not just patch_lesson).

    A brand-new concept card out of the loop is also structurally authorable.
    """
    # setup: repo with one lesson, but the increment targets a new slug.
    repo_root = _write_repo_lesson(tmp_path, "word_order_main_clauses")
    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )

    cited_claim = "Norwegian past tense conjugation"
    note = ResearchNote(
        claim=cited_claim,
        url="https://example.com/past-tense",
        quote=cited_claim,
    )
    new_slug = "past_tense_conjugation"
    course_map = _course_map([
        _gate_clean_concept(new_slug, claims_covered=[cited_claim]),
    ])

    # execute
    result = run_increment(
        "new topic about past tense conjugation",
        repo_root=repo_root,
        run_id="fidelity2",
        triage_agent=_FakeTriageAgent("new_topic"),
        research_backend=_FakeResearchBackend([note]),
        writer_agent=_WriterAgentWrapper(_StatefulWriterAgent([course_map])),
        reviewer_agent=_FakeReviewerAgent(),
    )

    # assert: converged, card is full + consumable.
    assert result.status == "parked_for_review"
    assert result.concept is not None
    concept = result.concept
    assert concept.slug == new_slug

    stage_result = author_metadata_objectives(
        concept.slug,
        concept,
        author_agent=_FakeColdAuthorAgent(),
    )
    assert isinstance(stage_result, StageOK)
    assert stage_result.payload.cefr_level == concept.cefr_level
    assert len(stage_result.payload.objectives) == len(concept.objectives)


def test_run_increment_produced_card_round_trips_through_concept_requirements_schema(
    tmp_path: Path, monkeypatch
):
    """The produced card survives a JSON round-trip through the strict
    ``ConceptRequirements`` validator (``extra="forbid"``). This catches any
    loop-internal fields (``sequence_index``, ``source_notes``) leaking onto the
    committed card — they must NOT appear in the serialized requirements.
    """
    from lesson_builder.pipeline.concept_requirements import ConceptRequirements

    repo_root = _write_repo_lesson(tmp_path, "word_order_main_clauses")
    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )

    cited_claim = "definite article inflection"
    note = ResearchNote(claim=cited_claim, url="https://example.com/a", quote=cited_claim)
    course_map = _course_map([
        _gate_clean_concept("word_order_main_clauses", claims_covered=[cited_claim]),
    ])

    result = run_increment(
        "update the topic about word order main clauses",
        repo_root=repo_root,
        slug="word_order_main_clauses",
        run_id="fidelity3",
        triage_agent=_FakeTriageAgent("patch_lesson", "word_order_main_clauses"),
        research_backend=_FakeResearchBackend([note]),
        writer_agent=_WriterAgentWrapper(_StatefulWriterAgent([course_map])),
        reviewer_agent=_FakeReviewerAgent(),
    )

    # The committed card must NOT carry sequence_index / source_notes
    # (those are ConceptDraft-internal; the card is strict ConceptRequirements).
    card_json = result.concept.model_dump(mode="json")
    assert "sequence_index" not in card_json
    assert "source_notes" not in card_json

    # Round-trip through the strict validator.
    re_validated = ConceptRequirements.model_validate(card_json)
    assert re_validated.slug == result.concept.slug
    assert len(re_validated.objectives) == len(result.concept.objectives)
