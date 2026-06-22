"""Task E Step 5: ``cold_author_flow`` orchestrates the cold-author stages, parks
the assembled draft at the human gate via the shared back-half, refuses when the
lesson already exists (overwrite guard, R3), and short-circuits to needs_human on
a mid-stage failure (R4) while preserving earlier output as a draft.

Entry point: ``cold_author_flow``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import lesson_builder.pipeline.graph_runner as graph_runner
from lesson_builder.pipeline.cold_author.flow import cold_author_flow
from lesson_builder.pipeline.judges import noop_judge
from lesson_builder.pipeline.lesson_qa_graph import noop_fixer

K_TEST_ROOT = Path(__file__).resolve().parents[3]


def _requirements_payload() -> dict[str, Any]:
    return {
        "slug": "adjective_agreement",
        "cefr_level": "A1",
        "objectives": [
            {
                "id": "obj_indefinite_forms",
                "statement": "Recognize forms.",
                "bloom_targets": ["understand"],
            },
            {
                "id": "obj_production",
                "statement": "Produce forms.",
                "bloom_targets": ["apply"],
            },
        ],
        "required_anchor_forms": ["en fin bil", "et fint hus", "fine biler", "bilen er fin"],
        "min_clean_examples": 5,
        "notes": "teach agreement: base, -t, -e, predicative.",
    }


def _write_requirements(tmp_path: Path, slug: str = "adjective_agreement") -> Path:
    req_dir = tmp_path / "data" / "concept_requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    (req_dir / f"{slug}.json").write_text(
        json.dumps(_requirements_payload(), ensure_ascii=False, indent=2) + "\n"
    )
    (tmp_path / "store").mkdir(parents=True, exist_ok=True)
    return tmp_path


class _FakeAuthor:
    """Returns well-formed responses for each cold-author stage.

    The flow calls metadata_objectives, sections, and exercises in sequence.
    All three responses are valid and satisfy the coverage invariants, so the
    assembled draft reaches the human gate.
    """

    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, prompt: str, **kw: Any) -> str:
        self.calls += 1
        # The prompt text identifies the stage; return the matching well-formed body.
        if "metadata" in prompt.lower() or "cefr_level" in prompt.lower():
            return json.dumps(
                {
                    "title": "Adjective agreement",
                    "goal": "Learn indefinite adjective agreement.",
                },
                ensure_ascii=False,
            )
        if "teaching sections" in prompt.lower():
            return json.dumps(
                {
                    "sections": [
                        {
                            "role": "orient",
                            "objective_ids": [],
                            "title": "Overview",
                            "blocks": [
                                {"kind": "paragraph", "spans": [{"kind": "text", "value": "en fin bil"}]}
                            ],
                        },
                        {
                            "role": "model",
                            "objective_ids": ["obj_indefinite_forms"],
                            "title": "Forms",
                            "blocks": [
                                {"kind": "paragraph", "spans": [{"kind": "text", "value": "et fint hus"}]}
                            ],
                        },
                        {
                            "role": "model",
                            "objective_ids": ["obj_production"],
                            "title": "Production",
                            "blocks": [
                                {"kind": "paragraph", "spans": [{"kind": "text", "value": "fine biler"}]}
                            ],
                        },
                        {
                            "role": "recap",
                            "objective_ids": [],
                            "title": "Recap",
                            "blocks": [
                                {"kind": "paragraph", "spans": [{"kind": "text", "value": "bilen er fin"}]}
                            ],
                        },
                    ]
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {
                "exercises": [
                    {
                        "objective_id": "obj_indefinite_forms",
                        "operation": "judge",
                        "bloom_level": "understand",
                        "prompt_text": "Riktig?",
                        "explanation_text": "Ja.",
                        "flat": {"sentence_no": "en fin bil", "is_correct": True, "feedback": "ok"},
                    },
                    {
                        "objective_id": "obj_indefinite_forms",
                        "operation": "choose",
                        "bloom_level": "understand",
                        "prompt_text": "Velg.",
                        "explanation_text": None,
                        "flat": {"options": ["et fint hus", "et fin hus"], "correct_index": 0},
                    },
                    {
                        "objective_id": "obj_production",
                        "operation": "build",
                        "bloom_level": "apply",
                        "prompt_text": "Bygg.",
                        "explanation_text": None,
                        "flat": {"sentence": "fine biler går"},
                    },
                    {
                        "objective_id": "obj_production",
                        "operation": "recall_fill",
                        "bloom_level": "apply",
                        "prompt_text": "Fyll.",
                        "explanation_text": None,
                        "flat": {
                            "sentence": "bilen er ___",
                            "blanks": [{"options": ["fin", "fint"], "answer": "fin"}],
                        },
                    },
                ]
            },
            ensure_ascii=False,
        )


def _patch_checkpoints(monkeypatch, repo_root: Path) -> None:
    monkeypatch.setattr(graph_runner, "K_CHECKPOINTS_PATH", repo_root / "store" / "checkpoints.db")


def test_cold_author_flow_given_cold_slug_expect_parked_for_review(tmp_path: Path, monkeypatch):
    repo_root = _write_requirements(tmp_path)
    _patch_checkpoints(monkeypatch, repo_root)

    result = cold_author_flow(
        "adjective_agreement",
        repo_root=repo_root,
        run_id="cold1",
        author_agent=_FakeAuthor(),
        fixer=noop_fixer,
        judge=noop_judge,
    )

    assert result.status == "parked_for_review"
    assert result.graph is not None
    assert result.graph["next"] == ["human_gate"]
    assert result.draft_path is not None
    assert (repo_root / result.draft_path).exists()


def test_cold_author_flow_given_existing_lesson_without_force_expect_refused(
    tmp_path: Path, monkeypatch
):
    repo_root = _write_requirements(tmp_path)
    # Simulate an existing lesson file (every real slug has one).
    lessons_dir = repo_root / "data" / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    (lessons_dir / "adjective_agreement.json").write_text(
        json.dumps({"key": "adjective_agreement"}), encoding="utf-8"
    )
    _patch_checkpoints(monkeypatch, repo_root)

    result = cold_author_flow(
        "adjective_agreement",
        repo_root=repo_root,
        author_agent=_FakeAuthor(),
        fixer=noop_fixer,
        judge=noop_judge,
    )

    assert result.status == "refused"
    assert "improve" in result.message.lower()


def test_cold_author_flow_given_existing_lesson_with_force_expect_parked(
    tmp_path: Path, monkeypatch
):
    repo_root = _write_requirements(tmp_path)
    lessons_dir = repo_root / "data" / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    (lessons_dir / "adjective_agreement.json").write_text(
        json.dumps({"key": "adjective_agreement"}), encoding="utf-8"
    )
    _patch_checkpoints(monkeypatch, repo_root)

    result = cold_author_flow(
        "adjective_agreement",
        repo_root=repo_root,
        run_id="cold_force",
        author_agent=_FakeAuthor(),
        fixer=noop_fixer,
        judge=noop_judge,
        force=True,
    )

    assert result.status == "parked_for_review"


def test_cold_author_flow_given_cold_slug_does_not_raise_filenotfound(tmp_path: Path, monkeypatch):
    # C1: a cold slug has no data/lessons/<slug>.json; the standalone loader must
    # not trip FileNotFoundError.
    repo_root = _write_requirements(tmp_path)
    _patch_checkpoints(monkeypatch, repo_root)

    result = cold_author_flow(
        "adjective_agreement",
        repo_root=repo_root,
        run_id="cold_nofile",
        author_agent=_FakeAuthor(),
        fixer=noop_fixer,
        judge=noop_judge,
    )

    assert result.status == "parked_for_review"
    assert not (repo_root / "data" / "lessons" / "adjective_agreement.json").exists()


class _Stage2FailingAuthor(_FakeAuthor):
    """Stage 2 (sections) raises BackendDownException after Stage 1 succeeded."""

    def invoke(self, prompt: str, **kw: Any) -> str:
        if "teaching sections" in prompt.lower():
            from lesson_builder.pipeline.llm.exceptions import BackendDownException

            raise BackendDownException("backend down for sections")
        return super().invoke(prompt, **kw)


def test_cold_author_flow_given_stage2_backend_down_expect_needs_human_with_draft(
    tmp_path: Path, monkeypatch
):
    repo_root = _write_requirements(tmp_path)
    _patch_checkpoints(monkeypatch, repo_root)

    result = cold_author_flow(
        "adjective_agreement",
        repo_root=repo_root,
        run_id="cold_fail",
        author_agent=_Stage2FailingAuthor(),
        fixer=noop_fixer,
        judge=noop_judge,
    )

    # R4: the flow short-circuits to needs_human instead of crashing, and the
    # Stage 1 output (metadata) is preserved as a draft on disk.
    assert result.status == "needs_human"
    assert result.failed_stage == "sections"
    assert result.draft_path is not None
    draft = json.loads((repo_root / result.draft_path).read_text(encoding="utf-8"))
    assert "metadata" in draft
    assert draft["metadata"]["objectives"]  # Stage 1 output preserved.
