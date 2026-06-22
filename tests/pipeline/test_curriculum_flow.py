"""Tests for the curriculum increment flow (Phase 3; subsumes the retired flow.py).

Entry points: ``run_increment`` / ``commit_increment_draft``.

Offline: injects a fake ``triage_agent`` (canned routing verdict), a fake
``research_backend`` (canned ``ResearchNote`` list), a fake ``writer_agent``
(canned ``CourseMap`` JSON via the structured-output path), a fake
``reviewer_agent`` (empty advisory), and monkeypatches
``resolve_citations`` so no network is hit. Asserts:

- patch_lesson / split / new_topic routes all produce parked drafts.
- the produced card is a FULL ``ConceptRequirements`` (not the anchors-only stub).
- the commit step writes structure + requirements without mutating lesson JSON.
- the default_loader reads the same ``data/concept_requirements/<slug>.json``
  the commit step wrote (the legacy writer/reader path-split regression).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from lesson_builder.pipeline.concept_requirements import Objective
from lesson_builder.pipeline.curriculum_design.increment import (
    commit_increment_draft,
    run_increment,
)
from lesson_builder.pipeline.curriculum_design.models import (
    ConceptDraft,
    CourseMap,
    ResearchNote,
)
from lesson_builder.pipeline.lesson_qa_graph import default_loader
from lesson_builder.pipeline.nodes.triage import TriageClassification

K_TEST_ROOT = Path(__file__).resolve().parents[2]


# ── Offline fakes ─────────────────────────────────────────────────────────


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


class _FakeResearchBackend:
    """Returns canned notes per call; records queries."""

    def __init__(self, notes: Sequence[ResearchNote] | None = None) -> None:
        self._notes = list(notes or [])
        self.calls: list[str] = []

    def __call__(self, query: str) -> list[ResearchNote]:
        self.calls.append(query)
        return list(self._notes)


class _StatefulWriterAgent:
    """Returns a CourseMap JSON on each ``invoke`` call."""

    def __init__(self, maps_by_round: list[CourseMap]) -> None:
        self._maps = list(maps_by_round)
        self._index = 0
        self.calls: list[str] = []

    def invoke(self, prompt: str) -> str:
        self.calls.append(prompt)
        current = self._maps[min(self._index, len(self._maps) - 1)]
        self._index += 1
        return current.model_dump_json()


class _StructuredAgentStub:
    """Stand-in for ``Agent.structured()`` return value.

    The loop calls ``agent.structured(CourseMap).invoke(prompt)``; this stub
    calls the writer's ``invoke`` and parses the JSON through real Pydantic.
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


class _FakeReviewerAgent:
    """Advisory-only agent that returns an empty list."""

    def invoke(self, prompt: str) -> str:
        return "[]"


# ── Fixture builders ──────────────────────────────────────────────────────


def _note(claim: str = "a claim") -> ResearchNote:
    return ResearchNote(claim=claim, url="https://example.com/a", quote=claim)


def _concept_draft(
    slug: str,
    *,
    n_objectives: int = 3,
    notes: str = "teaches the concept",
    claims_covered: list[str] | None = None,
) -> ConceptDraft:
    return ConceptDraft(
        slug=slug,
        cefr_level="A1",
        objectives=[
            Objective(
                id=f"o{i}",
                statement=f"{slug} objective {i}",
                bloom_targets=["understand"],
            )
            for i in range(n_objectives)
        ],
        required_anchor_forms=[f"{slug}-anchor-{i}" for i in range(3)],
        notes=f"{notes}. Covers: {', '.join(claims_covered or [])}",
        sequence_index=0,
        source_notes=claims_covered or [],
    )


def _course_map(concepts: list[ConceptDraft]) -> CourseMap:
    return CourseMap(concepts=concepts, cefr_span="A1-A2")


def _offline_collaborators(
    course_maps: list[CourseMap],
    notes: list[ResearchNote] | None = None,
) -> dict[str, object]:
    """Build the four offline collaborators for ``run_increment``."""
    writer = _StatefulWriterAgent(course_maps)
    return {
        "triage_agent": None,  # set per-test
        "research_backend": _FakeResearchBackend(notes or [_note()]),
        "writer_agent": _WriterAgentWrapper(writer),
        "reviewer_agent": _FakeReviewerAgent(),
    }


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


def test_run_increment_given_existing_slug_expect_patch_route_and_review_draft(
    tmp_path: Path, monkeypatch
):
    # setup
    repo_root = _write_repo_lesson(tmp_path, "word_order_main_clauses")
    # All citations corroborate (no network).
    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )
    course_map = _course_map([
        _concept_draft("word_order_main_clauses", claims_covered=["a claim"]),
    ])
    collaborators = _offline_collaborators([course_map])

    # execute
    result = run_increment(
        "update the topic about word order main clauses",
        repo_root=repo_root,
        slug="word_order_main_clauses",
        run_id="cur1",
        triage_agent=_FakeTriageAgent("patch_lesson", "word_order_main_clauses"),
        research_backend=collaborators["research_backend"],
        writer_agent=collaborators["writer_agent"],
        reviewer_agent=collaborators["reviewer_agent"],
    )

    # assert: parked_for_review with a FULL ConceptRequirements card.
    draft_path = repo_root / result.draft_path
    requirements_path = draft_path / "requirements" / "word_order_main_clauses.json"

    assert result.status == "parked_for_review"
    assert result.route == "patch_lesson"
    assert result.concept is not None
    assert result.concept.slug == "word_order_main_clauses"
    assert len(result.concept.objectives) >= 1
    assert result.concept.cefr_level == "A1"
    assert (draft_path / "structure.json").exists()
    assert requirements_path.exists()
    # The card has objectives + bloom + cefr + anchors (NOT the anchors-only stub).
    card = json.loads(requirements_path.read_text(encoding="utf-8"))
    assert "objectives" in card and len(card["objectives"]) >= 1
    assert "cefr_level" in card
    assert "required_anchor_forms" in card
    assert result.stale_impacts[0].slug == "word_order_main_clauses"


def test_run_increment_given_partial_match_expect_split_route(tmp_path: Path, monkeypatch):
    # setup
    repo_root = _write_repo_lesson(tmp_path, "word_order_main_clauses")
    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )
    course_map = _course_map([
        _concept_draft("word_order_main_clauses", claims_covered=["a claim"]),
    ])
    collaborators = _offline_collaborators([course_map])

    # execute
    result = run_increment(
        "new topic about word order in main clauses",
        repo_root=repo_root,
        run_id="cur2",
        triage_agent=_FakeTriageAgent("split", "word_order_main_clauses"),
        research_backend=collaborators["research_backend"],
        writer_agent=collaborators["writer_agent"],
        reviewer_agent=collaborators["reviewer_agent"],
    )

    # assert
    assert result.route == "split"
    assert result.status == "parked_for_review"


def test_run_increment_given_new_topic_expect_full_concept_card(tmp_path: Path, monkeypatch):
    # setup
    repo_root = _write_repo_lesson(tmp_path, "word_order_main_clauses")
    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )
    course_map = _course_map([
        _concept_draft("quantum_physics_phenomena", claims_covered=["a claim"]),
    ])
    collaborators = _offline_collaborators([course_map])

    # execute
    result = run_increment(
        "new topic about quantum physics phenomena",
        repo_root=repo_root,
        run_id="cur3",
        triage_agent=_FakeTriageAgent("new_topic"),
        research_backend=collaborators["research_backend"],
        writer_agent=collaborators["writer_agent"],
        reviewer_agent=collaborators["reviewer_agent"],
    )

    # assert: new_topic route produces a parked draft with a FULL card.
    draft_path = repo_root / result.draft_path
    requirements_path = draft_path / "requirements" / "quantum_physics_phenomena.json"

    assert result.route == "new_topic"
    assert result.status == "parked_for_review"
    assert result.concept is not None
    assert result.concept.slug == "quantum_physics_phenomena"
    assert len(result.concept.objectives) >= 1
    assert requirements_path.exists()
    assert not (repo_root / "data" / "lessons" / "quantum_physics_phenomena.json").exists()
    # The card is a FULL ConceptRequirements (objectives + bloom + cefr + notes).
    card = json.loads(requirements_path.read_text(encoding="utf-8"))
    assert card["slug"] == "quantum_physics_phenomena"
    assert len(card["objectives"]) >= 1
    assert "bloom_targets" in card["objectives"][0]
    assert "cefr_level" in card


def test_run_increment_given_non_converging_loop_expect_needs_human(tmp_path: Path, monkeypatch):
    # setup: the writer always produces a concept that does NOT cover the cited
    # research claim, so ``coverage_gaps`` fires every round -> the loop never
    # converges -> the increment surfaces needs_human (does NOT commit).
    repo_root = _write_repo_lesson(tmp_path, "word_order_main_clauses")
    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )
    # The research claim is "zorkness_feature" (a unique token); the concept
    # never mentions it -> coverage_gap fires persistently -> no convergence.
    uncovered_note = ResearchNote(
        claim="zorkness_feature",
        url="https://example.com/z",
        quote="zorkness_feature",
    )
    uncovered_map = _course_map([
        _concept_draft(
            "word_order_main_clauses",
            claims_covered=["unrelated_claim"],
        ),
    ])
    writer = _StatefulWriterAgent([uncovered_map])
    research_backend = _FakeResearchBackend([uncovered_note])

    # execute
    result = run_increment(
        "update the topic about word order main clauses",
        repo_root=repo_root,
        slug="word_order_main_clauses",
        run_id="cur_nh",
        triage_agent=_FakeTriageAgent("patch_lesson", "word_order_main_clauses"),
        research_backend=research_backend,
        writer_agent=_WriterAgentWrapper(writer),
        reviewer_agent=_FakeReviewerAgent(),
    )

    # assert: needs_human; no draft written.
    assert result.status == "needs_human"
    assert result.draft_path is None
    assert "did not converge" in result.message


def test_commit_increment_draft_given_approved_draft_expect_curriculum_written_without_lesson_mutation(
    tmp_path: Path, monkeypatch
):
    # setup
    repo_root = _write_repo_lesson(tmp_path, "word_order_main_clauses")
    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )
    course_map = _course_map([
        _concept_draft("quantum_physics_phenomena", claims_covered=["a claim"]),
    ])
    collaborators = _offline_collaborators([course_map])
    result = run_increment(
        "new topic about quantum physics phenomena",
        repo_root=repo_root,
        run_id="cur4",
        triage_agent=_FakeTriageAgent("new_topic"),
        research_backend=collaborators["research_backend"],
        writer_agent=collaborators["writer_agent"],
        reviewer_agent=collaborators["reviewer_agent"],
    )

    # execute
    commit_result = commit_increment_draft(
        repo_root=repo_root,
        draft_path=Path(result.draft_path),
    )

    # assert
    assert "data/curriculum/structure.json" in commit_result.committed_paths
    assert (
        "data/concept_requirements/quantum_physics_phenomena.json"
        in commit_result.committed_paths
    )
    assert commit_result.lesson_paths_touched == []
    assert (
        repo_root / "data" / "concept_requirements" / "quantum_physics_phenomena.json"
    ).exists()
    assert not (
        repo_root / "data" / "lessons" / "quantum_physics_phenomena.json"
    ).exists()


def test_commit_increment_draft_given_patched_requirements_expect_default_loader_reads_same_dir(
    tmp_path: Path, monkeypatch
):
    """Regression for the writer/reader path split: the increment writes
    requirements and ``lesson_qa_graph.default_loader`` (the gate/stale-check
    reader) must resolve the exact same committed file -- otherwise a committed
    curriculum requirement is silently invisible to the QA graph and the
    stale-check never fires.
    """
    slug = "word_order_main_clauses"
    repo_root = _write_repo_lesson(tmp_path, slug)
    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )
    course_map = _course_map([
        _concept_draft(slug, claims_covered=["a claim"]),
    ])
    collaborators = _offline_collaborators([course_map])

    result = run_increment(
        "update the topic about word order main clauses",
        repo_root=repo_root,
        slug=slug,
        run_id="cur5",
        triage_agent=_FakeTriageAgent("patch_lesson", slug),
        research_backend=collaborators["research_backend"],
        writer_agent=collaborators["writer_agent"],
        reviewer_agent=collaborators["reviewer_agent"],
    )
    commit_result = commit_increment_draft(
        repo_root=repo_root,
        draft_path=Path(result.draft_path),
    )

    committed_requirements_path = repo_root / "data" / "concept_requirements" / f"{slug}.json"
    assert f"data/concept_requirements/{slug}.json" in commit_result.committed_paths
    assert committed_requirements_path.exists()

    committed_requirements = json.loads(committed_requirements_path.read_text(encoding="utf-8"))
    loaded = default_loader(slug, repo_root=repo_root)

    # default_loader reads from the same data/concept_requirements/<slug>.json
    # the increment just wrote -- the committed card must be visible.
    assert loaded.requirements == committed_requirements
    assert "Curriculum increment request: update the topic about word order main clauses" in (
        loaded.requirements["notes"]
    )
    # the legacy split target must stay untouched/unused.
    assert not (repo_root / "data" / "curriculum" / "requirements").exists()
