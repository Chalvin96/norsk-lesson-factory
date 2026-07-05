"""Entry point: ``cold_author_flow`` -> ``run_improvement_flow`` -> ``regenerate_dist``.

End-to-end lifecycle regression: empty dist can be populated from scratch,
populated lessons can be improved, and dist is regenerated as a pure projection
from ``data/lessons`` rather than round-tripped back into authoring state.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import lesson_builder.pipeline.graph_runner as graph_runner
from lesson_builder.pipeline.cold_author.flow import cold_author_flow
from lesson_builder.pipeline.graph_runner import resume_thread
from lesson_builder.pipeline.improvement_flow import run_improvement_flow
from lesson_builder.pipeline.judges import noop_judge
from lesson_builder.pipeline.lesson_export import lesson_to_export
from lesson_builder.pipeline.lesson_import import regenerate_dist
from lesson_builder.pipeline.lesson_qa_graph import noop_fixer
from lesson_builder.schema import Lesson

K_TEST_ROOT = Path(__file__).resolve().parents[2]
K_DIST_LIFECYCLE_SLUG = "adjective_agreement"


class FakeColdAuthor:
    """Offline cold author that returns one complete valid lesson draft."""

    def invoke(self, prompt: str, **kw: Any) -> str:
        prompt_lower = prompt.lower()
        if "metadata" in prompt_lower:
            return json.dumps(
                {
                    "title": "Adjective agreement",
                    "goal": "Learn indefinite adjective agreement.",
                },
                ensure_ascii=False,
            )
        if "teaching sections" in prompt_lower:
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
                            "objective_ids": ["obj_indefinite_forms"],
                            "title": "Production",
                            "blocks": [
                                {"kind": "paragraph", "spans": [{"kind": "text", "value": "fine biler"}]}
                            ],
                        },
                        {
                            "role": "contrast",
                            "objective_ids": ["obj_predicative_agreement"],
                            "title": "Predicative agreement",
                            "blocks": [
                                {
                                    "kind": "paragraph",
                                    "spans": [{"kind": "text", "value": "bilen er fin"}],
                                }
                            ],
                        },
                        {
                            "role": "contrast",
                            "objective_ids": ["obj_diagnose_agreement"],
                            "title": "Find the error",
                            "blocks": [
                                {
                                    "kind": "paragraph",
                                    "spans": [{"kind": "text", "value": "et fin hus"}],
                                }
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
                        "objective_id": "obj_indefinite_forms",
                        "operation": "build",
                        "bloom_level": "apply",
                        "prompt_text": "Bygg.",
                        "explanation_text": None,
                        "flat": {"sentence": "fine biler går"},
                    },
                    {
                        "objective_id": "obj_indefinite_forms",
                        "operation": "recall_fill",
                        "bloom_level": "apply",
                        "prompt_text": "Fyll.",
                        "explanation_text": None,
                        "flat": {
                            "sentence": "bilen er ___",
                            "blanks": [{"options": ["fin", "fint"], "answer": "fin"}],
                        },
                    },
                    {
                        "objective_id": "obj_predicative_agreement",
                        "operation": "judge",
                        "bloom_level": "understand",
                        "prompt_text": "Riktig predikativ form?",
                        "explanation_text": "Ja.",
                        "flat": {"sentence_no": "bilen er fin", "is_correct": True, "feedback": "ok"},
                    },
                    {
                        "objective_id": "obj_predicative_agreement",
                        "operation": "choose",
                        "bloom_level": "understand",
                        "prompt_text": "Velg riktig form.",
                        "explanation_text": None,
                        "flat": {"options": ["bilen er fin", "bilen er fint"], "correct_index": 0},
                    },
                    {
                        "objective_id": "obj_diagnose_agreement",
                        "operation": "judge",
                        "bloom_level": "understand",
                        "prompt_text": "Finn feilen.",
                        "explanation_text": "Nei.",
                        "flat": {"sentence_no": "et fin hus", "is_correct": False, "feedback": "Use neuter -t."},
                    },
                    {
                        "objective_id": "obj_diagnose_agreement",
                        "operation": "choose",
                        "bloom_level": "understand",
                        "prompt_text": "Velg riktig retting.",
                        "explanation_text": None,
                        "flat": {"options": ["et fint hus", "et fin hus"], "correct_index": 0},
                    },
                ]
            },
            ensure_ascii=False,
        )


class FakeImproveAuthor:
    """Offline improvement author for add-exercise and whole-lesson improve."""

    def invoke(self, prompt: str, **kw: Any) -> str:
        if "Return the COMPLETE revised lesson" in prompt:
            return (K_TEST_ROOT / "data" / "lessons" / f"{K_DIST_LIFECYCLE_SLUG}.json").read_text(
                encoding="utf-8"
            )
        return json.dumps(
            {
                "exercises": [
                    {
                        "operation": "judge",
                        "prompt_text": "Is this correct?",
                        "explanation_text": "practice",
                        "flat": {"sentence_no": "Eksempel.", "is_correct": True, "feedback": "ok"},
                    }
                ]
            },
            ensure_ascii=False,
        )

    def structured(self, schema: type):
        from lesson_builder.pipeline.llm.base import extract_json_object

        agent = self

        class StructuredFakeImproveAuthor:
            def invoke(self, prompt: str, **kw: Any) -> object:
                raw_payload = agent.invoke(prompt, **kw)
                return schema.model_validate(extract_json_object(raw_payload))

        return StructuredFakeImproveAuthor()


def test_dist_lifecycle_given_empty_dist_populate_improve_regenerate_expect_dist_matches_data_projection(
    tmp_path: Path, monkeypatch
):
    repo_root = tmp_path / "repo_empty_dist"
    repo_root.mkdir()
    _copy_requirements(repo_root)
    _point_checkpointer_at(monkeypatch, repo_root)

    cold = cold_author_flow(
        K_DIST_LIFECYCLE_SLUG,
        repo_root=repo_root,
        run_id="cold_populate",
        commit=True,
        author_agent=FakeColdAuthor(),
        fixer=noop_fixer,
        judge=noop_judge,
    )
    cold_accept = resume_thread(
        K_DIST_LIFECYCLE_SLUG, "cold_populate", {"status": "accept"}, repo_root=repo_root
    )

    assert cold.status == "parked_for_review"
    assert cold.graph is not None
    assert cold.graph["next"] == ["human_gate"]
    assert cold_accept["park_status"] == "accepted"
    assert _dist_lesson_path(repo_root).exists()
    assert _internal_lesson_path(repo_root).exists()

    improved = run_improvement_flow(
        "improve the lesson",
        repo_root=repo_root,
        slug=K_DIST_LIFECYCLE_SLUG,
        run_id="improve_populated",
        commit=True,
        author_agent=FakeImproveAuthor(),
        fixer=noop_fixer,
        judge=noop_judge,
    )
    improved_accept = resume_thread(
        K_DIST_LIFECYCLE_SLUG, "improve_populated", {"status": "accept"}, repo_root=repo_root
    )

    assert improved.status == "parked_for_review"
    assert improved.graph is not None
    assert improved.graph["next"] == ["human_gate"]
    assert improved_accept["park_status"] == "accepted"
    assert _dist_lesson_path(repo_root).exists()

    derived_repo = tmp_path / "repo_regenerated_projection"
    derived_repo.mkdir()
    _copy_requirements(derived_repo)
    lessons_dir = derived_repo / "data" / "lessons"
    lessons_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_internal_lesson_path(repo_root), lessons_dir / f"{K_DIST_LIFECYCLE_SLUG}.json")

    summary = regenerate_dist(derived_repo)
    expected_export = json.dumps(
        lesson_to_export(
            Lesson.model_validate_json(
                (lessons_dir / f"{K_DIST_LIFECYCLE_SLUG}.json").read_text(encoding="utf-8")
            )
        ),
        ensure_ascii=False,
        indent=2,
    ) + "\n"

    assert summary == {"created": 1, "unchanged": 0, "updated": 0}
    assert _dist_lesson_path(derived_repo).read_text(encoding="utf-8") == expected_export


def _copy_requirements(repo_root: Path) -> None:
    requirements_dir = repo_root / "data" / "concept_requirements"
    requirements_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        K_TEST_ROOT / "data" / "concept_requirements" / f"{K_DIST_LIFECYCLE_SLUG}.json",
        requirements_dir / f"{K_DIST_LIFECYCLE_SLUG}.json",
    )
    (repo_root / "store").mkdir(parents=True, exist_ok=True)


def _point_checkpointer_at(monkeypatch, repo_root: Path) -> None:
    monkeypatch.setattr(graph_runner, "K_CHECKPOINTS_PATH", repo_root / "store" / "checkpoints.db")


def _dist_lesson_path(repo_root: Path) -> Path:
    return repo_root / "dist" / "lessons" / f"{K_DIST_LIFECYCLE_SLUG}.json"


def _internal_lesson_path(repo_root: Path) -> Path:
    return repo_root / "data" / "lessons" / f"{K_DIST_LIFECYCLE_SLUG}.json"
