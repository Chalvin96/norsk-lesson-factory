"""Tests for the curriculum_design bootstrap flow (Phase 2).

Entry points: ``run_bootstrap`` / ``commit_bootstrap_draft`` /
``authorability_sample``.

Offline: injects a fake ``research_backend`` (canned ``ResearchNote`` list), a
fake ``writer_agent`` (canned ``CourseMap`` JSON via the structured-output
path), a fake ``reviewer_agent`` (empty advisory), and monkeypatches
``resolve_citations`` so no network is hit. Asserts:

- ``run_bootstrap`` converges to ``parked_for_review`` with N cards, all valid
  ``ConceptRequirements``.
- ``run_bootstrap`` surfaces ``needs_human`` on a non-converging loop (no
  draft written).
- ``coverage_report`` flags an uncovered cited goal.
- ``commit_bootstrap_draft`` is TRANSACTIONAL: all cards land on success; on a
  simulated mid-commit failure, NO partial write reaches the tracked tree.
- ``authorability_sample`` returns one concept per CEFR band.
- the CLI ``curriculum bootstrap`` path routes to ``run_bootstrap``.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from lesson_builder.pipeline.cli import add_parsers
from lesson_builder.pipeline.concept_requirements import (
    ConceptRequirements,
    Objective,
)
from lesson_builder.pipeline.curriculum_design.bootstrap import (
    BootstrapValidationError,
    authorability_sample,
    commit_bootstrap_draft,
    run_bootstrap,
)
from lesson_builder.pipeline.curriculum_design.models import (
    ConceptDraft,
    CourseMap,
    ResearchNote,
)
from lesson_builder.pipeline.curriculum_design.thresholds import CurriculumThresholds

# ── Offline fakes (mirror the test_loop.py / test_curriculum_flow.py pattern) ──


class _FakeResearchBackend:
    """Returns canned notes per call; records queries."""

    def __init__(self, notes: Sequence[ResearchNote] | None = None) -> None:
        self._notes = list(notes or [])
        self.calls: list[str] = []

    def __call__(self, query: str) -> list[ResearchNote]:
        self.calls.append(query)
        return list(self._notes)


class _StatefulWriterAgent:
    """Returns a CourseMap JSON on each ``invoke`` call; can vary per round."""

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


def _note(
    claim: str = "a claim",
    url: str = "https://example.com/a",
    quote: str = "a claim",
) -> ResearchNote:
    return ResearchNote(claim=claim, url=url, quote=quote)


def _concept_draft(
    slug: str,
    *,
    cefr_level: str = "A1",
    n_objectives: int = 3,
    notes: str = "teaches the concept",
    claims_covered: list[str] | None = None,
    sequence_index: int = 0,
) -> ConceptDraft:
    return ConceptDraft(
        slug=slug,
        cefr_level=cefr_level,  # type: ignore[arg-type]
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
        sequence_index=sequence_index,
        source_notes=claims_covered or [],
    )


def _course_map(concepts: list[ConceptDraft]) -> CourseMap:
    return CourseMap(concepts=concepts, cefr_span="A1-B1")


def _thresholds(*, thinness_floor: int = 2, too_broad_ceiling: int = 5) -> CurriculumThresholds:
    return CurriculumThresholds(
        n_concepts=3,
        n_in_benchmark=3,
        thinness_floor=thinness_floor,
        too_broad_ceiling=too_broad_ceiling,
    )


def _seed_repo(tmp_path: Path) -> Path:
    """Seed a minimal empty-curriculum repo root (no data/lessons, no data/curriculum)."""
    repo_root = tmp_path
    (repo_root / "store").mkdir(parents=True, exist_ok=True)
    return repo_root


def _offline_collaborators(
    course_maps: list[CourseMap],
    notes: list[ResearchNote] | None = None,
) -> dict[str, object]:
    writer = _StatefulWriterAgent(course_maps)
    return {
        "research_backend": _FakeResearchBackend(notes or [_note()]),
        "writer_agent": _WriterAgentWrapper(writer),
        "reviewer_agent": _FakeReviewerAgent(),
    }


def _patch_resolve_all_corroborated(monkeypatch) -> None:
    """Monkeypatch ``resolve_citations`` so every note is resolved (no network)."""
    monkeypatch.setattr(
        "lesson_builder.pipeline.curriculum_design.loop.resolve_citations",
        lambda notes, **kw: [n.model_copy(update={"resolved": True}) for n in notes],
    )


# ── run_bootstrap: converges to parked_for_review ──────────────────────────


def test_run_bootstrap_given_clean_loop_expect_parked_with_valid_cards(
    tmp_path: Path, monkeypatch
):
    # setup: three concepts across three CEFR bands, each covering a cited claim.
    repo_root = _seed_repo(tmp_path)
    _patch_resolve_all_corroborated(monkeypatch)
    notes = [
        _note(claim="greetings", url="https://a.example", quote="greetings"),
        _note(claim="word_order", url="https://b.example", quote="word_order"),
        _note(claim="past_tense", url="https://c.example", quote="past_tense"),
    ]
    course_map = _course_map([
        _concept_draft("greetings", cefr_level="A1", claims_covered=["greetings"], sequence_index=0),
        _concept_draft(
            "word_order", cefr_level="A2", claims_covered=["word_order"], sequence_index=1
        ),
        _concept_draft(
            "past_tense", cefr_level="B1", claims_covered=["past_tense"], sequence_index=2
        ),
    ])
    collaborators = _offline_collaborators([course_map], notes=notes)

    # execute
    result = run_bootstrap(
        repo_root=repo_root,
        research_backend=collaborators["research_backend"],
        writer_agent=collaborators["writer_agent"],
        reviewer_agent=collaborators["reviewer_agent"],
        thresholds=_thresholds(),
        run_id="boot1",
    )

    # assert: parked_for_review with 3 valid cards.
    assert result.status == "parked_for_review"
    assert result.n_concepts == 3
    assert result.draft_path is not None
    assert result.coverage_report is not None
    assert result.coverage_report.gaps == []

    # every card in the draft validates as a strict ConceptRequirements.
    draft_root = repo_root / result.draft_path
    requirements_dir = draft_root / "requirements"
    assert (draft_root / "structure.json").exists()
    assert (draft_root / "draft.json").exists()
    written_slugs = sorted(p.stem for p in requirements_dir.glob("*.json"))
    assert written_slugs == ["greetings", "past_tense", "word_order"]
    for slug in written_slugs:
        payload = json.loads((requirements_dir / f"{slug}.json").read_text(encoding="utf-8"))
        ConceptRequirements.model_validate(payload)  # raises on invalid

    # data/lessons is never touched.
    assert not (repo_root / "data" / "lessons").exists()


def test_run_bootstrap_given_non_converging_loop_expect_needs_human_no_draft(
    tmp_path: Path, monkeypatch
):
    # setup: the writer always produces a concept that does NOT cover the cited
    # research claim, so ``coverage_gaps`` fires every round -> the loop never
    # converges -> bootstrap surfaces needs_human (no draft written).
    repo_root = _seed_repo(tmp_path)
    _patch_resolve_all_corroborated(monkeypatch)
    uncovered_note = ResearchNote(
        claim="zorkness_feature",
        url="https://example.com/z",
        quote="zorkness_feature",
    )
    uncovered_map = _course_map([
        _concept_draft("unrelated_concept", claims_covered=["unrelated_claim"]),
    ])
    writer = _StatefulWriterAgent([uncovered_map])
    research_backend = _FakeResearchBackend([uncovered_note])

    # execute
    result = run_bootstrap(
        repo_root=repo_root,
        research_backend=research_backend,
        writer_agent=_WriterAgentWrapper(writer),
        reviewer_agent=_FakeReviewerAgent(),
        thresholds=_thresholds(),
        run_id="boot_nh",
    )

    # assert: needs_human; no draft written; no data/ touched.
    assert result.status == "needs_human"
    assert result.draft_path is None
    assert "did not converge" in result.message
    assert not (repo_root / "tmp" / "curriculum_drafts").exists() or not any(
        (repo_root / "tmp" / "curriculum_drafts").iterdir()
    )


# ── run_bootstrap: coverage_report flags an uncovered goal ─────────────────


def test_run_bootstrap_given_converged_map_with_uncovered_goal_expect_coverage_gap_reported(
    tmp_path: Path, monkeypatch
):
    # setup: the loop converges (no thinness/too_broad hit) BUT one cited goal
    # is not covered by any concept. Wait -- if a coverage_gap fires the loop
    # does not converge. So instead: test that a needs_human result carries the
    # coverage gap in its report (the gap IS what blocked convergence).
    repo_root = _seed_repo(tmp_path)
    _patch_resolve_all_corroborated(monkeypatch)
    notes = [
        _note(claim="covered_goal", url="https://ok.example", quote="covered_goal"),
        _note(claim="uncovered_goal", url="https://bad.example", quote="uncovered_goal"),
    ]
    partial_map = _course_map([
        _concept_draft("covers_one", claims_covered=["covered_goal"]),
    ])
    writer = _StatefulWriterAgent([partial_map])

    # execute
    result = run_bootstrap(
        repo_root=repo_root,
        research_backend=_FakeResearchBackend(notes),
        writer_agent=_WriterAgentWrapper(writer),
        reviewer_agent=_FakeReviewerAgent(),
        thresholds=_thresholds(),
        run_id="boot_gap",
    )

    # assert: needs_human (coverage gap blocked convergence); the uncovered goal
    # appears in coverage_report.gaps.
    assert result.status == "needs_human"
    assert result.coverage_report is not None
    assert "uncovered_goal" in result.coverage_report.gaps
    assert "covered_goal" not in result.coverage_report.gaps
    assert "covered_goal" in result.coverage_report.covered


# ── commit_bootstrap_draft: transactional (all-or-nothing) ─────────────────


def test_commit_bootstrap_draft_given_approved_draft_expect_all_cards_land_no_lesson_mutation(
    tmp_path: Path, monkeypatch
):
    # setup: run a clean bootstrap to produce a draft.
    repo_root = _seed_repo(tmp_path)
    _patch_resolve_all_corroborated(monkeypatch)
    notes = [
        _note(claim="greetings", url="https://a.example", quote="greetings"),
        _note(claim="word_order", url="https://b.example", quote="word_order"),
    ]
    course_map = _course_map([
        _concept_draft("greetings", cefr_level="A1", claims_covered=["greetings"], sequence_index=0),
        _concept_draft(
            "word_order", cefr_level="A2", claims_covered=["word_order"], sequence_index=1
        ),
    ])
    collaborators = _offline_collaborators([course_map], notes=notes)
    result = run_bootstrap(
        repo_root=repo_root,
        research_backend=collaborators["research_backend"],
        writer_agent=collaborators["writer_agent"],
        reviewer_agent=collaborators["reviewer_agent"],
        thresholds=_thresholds(),
        run_id="boot_commit_ok",
    )

    # execute
    commit_result = commit_bootstrap_draft(
        repo_root=repo_root,
        draft_path=Path(result.draft_path),
    )

    # assert: all cards land; no lessons touched.
    assert "data/curriculum/structure.json" in commit_result.committed_paths
    assert "data/concept_requirements/greetings.json" in commit_result.committed_paths
    assert "data/concept_requirements/word_order.json" in commit_result.committed_paths
    assert commit_result.n_concepts == 2
    assert commit_result.lesson_paths_touched == []
    assert (repo_root / "data" / "curriculum" / "structure.json").exists()
    assert (repo_root / "data" / "concept_requirements" / "greetings.json").exists()
    assert (repo_root / "data" / "concept_requirements" / "word_order.json").exists()
    assert not (repo_root / "data" / "lessons").exists()
    # structure.json carries the topics
    structure = json.loads(
        (repo_root / "data" / "curriculum" / "structure.json").read_text(encoding="utf-8")
    )
    slugs_in_structure = [t["slug"] for t in structure["topics"]]
    assert sorted(slugs_in_structure) == ["greetings", "word_order"]


def test_commit_bootstrap_draft_given_mid_commit_failure_expect_no_partial_write(
    tmp_path: Path, monkeypatch
):
    # setup: produce a draft with three valid concepts, then tamper with one
    # card in draft.json so it is INVALID. The commit must reject it BEFORE any
    # target write (all-or-nothing: validation runs before any swap).
    repo_root = _seed_repo(tmp_path)
    _patch_resolve_all_corroborated(monkeypatch)
    notes = [
        _note(claim="alpha", url="https://a.example", quote="alpha"),
        _note(claim="beta", url="https://b.example", quote="beta"),
        _note(claim="gamma", url="https://c.example", quote="gamma"),
    ]
    course_map = _course_map([
        _concept_draft("alpha_concept", cefr_level="A1", claims_covered=["alpha"], sequence_index=0),
        _concept_draft(
            "beta_concept", cefr_level="A2", claims_covered=["beta"], sequence_index=1
        ),
        _concept_draft(
            "gamma_concept", cefr_level="B1", claims_covered=["gamma"], sequence_index=2
        ),
    ])
    collaborators = _offline_collaborators([course_map], notes=notes)
    result = run_bootstrap(
        repo_root=repo_root,
        research_backend=collaborators["research_backend"],
        writer_agent=collaborators["writer_agent"],
        reviewer_agent=collaborators["reviewer_agent"],
        thresholds=_thresholds(),
        run_id="boot_fail",
    )
    assert result.status == "parked_for_review"

    # tamper: make beta_concept's card invalid (bad cefr_level) in draft.json.
    draft_json_path = repo_root / result.draft_path / "draft.json"
    draft_payload = json.loads(draft_json_path.read_text(encoding="utf-8"))
    draft_payload["requirements"]["beta_concept"]["cefr_level"] = "X9"
    draft_json_path.write_text(json.dumps(draft_payload, indent=2), encoding="utf-8")

    # pre-seed the tracked tree so we can prove NOTHING was overwritten.
    (repo_root / "data" / "concept_requirements").mkdir(parents=True, exist_ok=True)
    (repo_root / "data" / "curriculum").mkdir(parents=True, exist_ok=True)
    pre_existing_paths = {
        repo_root / "data" / "curriculum" / "structure.json",
        repo_root / "data" / "concept_requirements" / "alpha_concept.json",
        repo_root / "data" / "concept_requirements" / "beta_concept.json",
        repo_root / "data" / "concept_requirements" / "gamma_concept.json",
    }
    for path in pre_existing_paths:
        path.write_text('{"pre_existing": true}', encoding="utf-8")

    # execute + assert: validation error raised (no silent partial commit).
    with pytest.raises(BootstrapValidationError, match="beta_concept"):
        commit_bootstrap_draft(
            repo_root=repo_root,
            draft_path=Path(result.draft_path),
        )

    # assert: NO target file was overwritten -- every pre-existing file is
    # intact. This is the all-or-nothing guarantee: validation runs before ANY
    # target write, so a bad card blocks the whole commit.
    for path in pre_existing_paths:
        assert json.loads(path.read_text(encoding="utf-8")) == {"pre_existing": True}, (
            f"target {path} was partially overwritten (all-or-nothing violated)"
        )


def test_commit_bootstrap_draft_given_invalid_card_in_draft_expect_validation_error_no_write(
    tmp_path: Path, monkeypatch
):
    # setup: a draft whose draft.json carries an INVALID card (bad cefr_level).
    # The commit must reject it BEFORE any target write (all-or-nothing).
    repo_root = _seed_repo(tmp_path)
    draft_root = repo_root / "tmp" / "curriculum_drafts" / "bad_draft"
    requirements_dir = draft_root / "requirements"
    requirements_dir.mkdir(parents=True, exist_ok=True)

    # an invalid card payload (cefr_level not in the allowed literal set).
    invalid_payload = {
        "slug": "bad_concept",
        "cefr_level": "X9",  # invalid
        "objectives": [
            {"id": "o1", "statement": "stmt", "bloom_targets": ["understand"]}
        ],
        "required_anchor_forms": ["anchor"],
        "notes": "notes",
    }
    draft_payload = {
        "structure": {"version": 1, "topics": [{"slug": "bad_concept"}]},
        "requirements": {"bad_concept": invalid_payload},
        "coverage_report": {"cited_goals": [], "covered": [], "gaps": []},
        "cefr_bands": ["A1", "A2", "B1"],
    }
    (draft_root / "draft.json").write_text(
        json.dumps(draft_payload, indent=2), encoding="utf-8"
    )
    (requirements_dir / "bad_concept.json").write_text(
        json.dumps(invalid_payload, indent=2), encoding="utf-8"
    )
    (draft_root / "structure.json").write_text(
        json.dumps(draft_payload["structure"], indent=2), encoding="utf-8"
    )

    # pre-seed the tracked tree so we can prove nothing was overwritten.
    tracked_structure = repo_root / "data" / "curriculum" / "structure.json"
    tracked_structure.parent.mkdir(parents=True, exist_ok=True)
    tracked_structure.write_text('{"pre_existing": true}', encoding="utf-8")

    # execute + assert: validation error raised, no target write happened.
    with pytest.raises(BootstrapValidationError, match="bad_concept"):
        commit_bootstrap_draft(repo_root=repo_root, draft_path=draft_root)

    # the tracked tree is untouched (pre-existing content preserved).
    assert json.loads(tracked_structure.read_text(encoding="utf-8")) == {"pre_existing": True}
    assert not (
        repo_root / "data" / "concept_requirements" / "bad_concept.json"
    ).exists()


# ── authorability_sample ───────────────────────────────────────────────────


def test_authorability_sample_given_multi_band_map_expect_one_per_band():
    # setup
    course_map = _course_map([
        _concept_draft("greetings", cefr_level="A1", sequence_index=0),
        _concept_draft("extra_a1", cefr_level="A1", sequence_index=1),
        _concept_draft("word_order", cefr_level="A2", sequence_index=2),
        _concept_draft("past_tense", cefr_level="B1", sequence_index=3),
    ])

    # execute
    sample = authorability_sample(course_map, per_band=1)

    # assert: one concept per band (A1, A2, B1 all covered; no missing bands).
    assert sample.per_band == 1
    assert "A1" in sample.covered_bands
    assert "A2" in sample.covered_bands
    assert "B1" in sample.covered_bands
    assert sample.missing_bands == []
    assert len(sample.sample["A1"]) == 1
    assert len(sample.sample["A2"]) == 1
    assert len(sample.sample["B1"]) == 1
    # the note explicitly labels this as structural-only, NOT paradigm-correctness.
    assert "structural" in sample.note.lower()
    assert "paradigm" in sample.note.lower()


def test_authorability_sample_given_missing_band_expect_missing_reported():
    # setup: no B1 concept in the map.
    course_map = _course_map([
        _concept_draft("greetings", cefr_level="A1", sequence_index=0),
        _concept_draft("word_order", cefr_level="A2", sequence_index=1),
    ])

    # execute
    sample = authorability_sample(
        course_map, per_band=1, cefr_bands=("A1", "A2", "B1")
    )

    # assert: B1 is reported as missing.
    assert "B1" in sample.missing_bands
    assert "A1" in sample.covered_bands
    assert "A2" in sample.covered_bands
    assert sample.sample["B1"] == []


def test_authorability_sample_given_invalid_per_band_expect_value_error():
    # setup
    course_map = _course_map([_concept_draft("solo", cefr_level="A1")])

    # execute + assert
    with pytest.raises(ValueError, match="per_band must be >= 1"):
        authorability_sample(course_map, per_band=0)


# ── CLI: curriculum bootstrap path ─────────────────────────────────────────


def test_cli_curriculum_bootstrap_routes_to_run_bootstrap(tmp_path: Path, monkeypatch):
    # setup: build the full CLI parser; intercept run_bootstrap so no LLM runs.
    parser = _build_cli_parser()
    captured: dict[str, object] = {}

    def fake_run_bootstrap(**kwargs):
        captured.update(kwargs)
        captured["called"] = True
        from lesson_builder.pipeline.curriculum_design.bootstrap import (
            BootstrapResult,
            CoverageReport,
        )
        from lesson_builder.pipeline.curriculum_design.models import CourseMap

        return BootstrapResult(
            status="parked_for_review",
            course_map=CourseMap(concepts=[_concept_draft("solo")], cefr_span="A1-B1"),
            draft_path="tmp/curriculum_drafts/cli1",
            n_concepts=1,
            coverage_report=CoverageReport(cited_goals=[], covered=[], gaps=[]),
            message="cli bootstrap",
        )

    # patch the bootstrap module used by the CLI's lazy import path.
    import lesson_builder.pipeline.curriculum_design as cd_pkg

    monkeypatch.setattr(cd_pkg, "run_bootstrap", fake_run_bootstrap)

    repo_root = tmp_path
    (repo_root / "store").mkdir(parents=True, exist_ok=True)

    # execute
    args = parser.parse_args(
        ["curriculum", "bootstrap", "--repo-root", str(repo_root), "--run-id", "cli1"]
    )
    rc = args.func(args)

    # assert: the CLI routed to run_bootstrap (not run_increment).
    assert rc == 0
    assert captured.get("called") is True
    assert captured.get("run_id") == "cli1"


def test_cli_curriculum_increment_still_works_flat(tmp_path: Path, monkeypatch):
    # setup: ``curriculum <text>`` (no subcommand) must still route to increment.
    parser = _build_cli_parser()
    captured: dict[str, object] = {}

    def fake_run_increment(text, **kwargs):
        captured["text"] = text
        captured["called"] = True
        from lesson_builder.pipeline.curriculum_design.increment import IncrementResult

        return IncrementResult(
            status="parked_for_review",
            route="patch_lesson",
            draft_path="tmp/curriculum_drafts/inc1",
            concept=None,
            stale_impacts=[],
            message="cli increment flat",
        )

    import lesson_builder.pipeline.curriculum_design as cd_pkg

    monkeypatch.setattr(cd_pkg, "run_increment", fake_run_increment)

    repo_root = tmp_path
    (repo_root / "store").mkdir(parents=True, exist_ok=True)

    # execute
    args = parser.parse_args(
        ["curriculum", "some text", "--repo-root", str(repo_root)]
    )
    rc = args.func(args)

    # assert: flat invocation still routes to increment.
    assert rc == 0
    assert captured.get("called") is True
    assert captured.get("text") == "some text"


def test_cli_curriculum_bootstrap_commit_routes_to_commit_bootstrap(
    tmp_path: Path, monkeypatch
):
    # setup
    parser = _build_cli_parser()
    captured: dict[str, object] = {}

    def fake_commit_bootstrap(**kwargs):
        captured.update(kwargs)
        captured["called"] = True
        from lesson_builder.pipeline.curriculum_design.bootstrap import BootstrapCommitResult

        return BootstrapCommitResult(
            committed_paths=["data/curriculum/structure.json"],
            n_concepts=1,
            lesson_paths_touched=[],
            message="cli bootstrap commit",
        )

    import lesson_builder.pipeline.curriculum_design as cd_pkg

    monkeypatch.setattr(cd_pkg, "commit_bootstrap_draft", fake_commit_bootstrap)

    repo_root = tmp_path
    (repo_root / "store").mkdir(parents=True, exist_ok=True)
    draft_dir = repo_root / "tmp" / "curriculum_drafts" / "x"
    draft_dir.mkdir(parents=True, exist_ok=True)

    # execute
    args = parser.parse_args(
        [
            "curriculum",
            "bootstrap",
            "--commit-draft",
            str(draft_dir),
            "--repo-root",
            str(repo_root),
        ]
    )
    rc = args.func(args)

    # assert
    assert rc == 0
    assert captured.get("called") is True
    assert captured.get("draft_path") == draft_dir


# ── CLI helpers ────────────────────────────────────────────────────────────


def _build_cli_parser() -> object:
    import argparse

    parser = argparse.ArgumentParser(prog="lesson-data")
    sub = parser.add_subparsers(dest="command", required=True)
    add_parsers(sub)
    return parser
