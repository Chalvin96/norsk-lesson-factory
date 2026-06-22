"""Task E Step 6 + Task F Step F3: the ``author`` / ``import`` / ``regenerate-dist``
CLI subcommands route to the expected pipeline entry points.

Entry point: ``add_parsers`` (wires the CLI subcommands).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import lesson_builder.pipeline.graph_runner as graph_runner
from lesson_builder.pipeline.cli import add_parsers

ROOT = Path(__file__).resolve().parents[2]


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="lesson-data")
    sub = parser.add_subparsers(dest="command", required=True)
    add_parsers(sub)
    return parser


def _write_requirements(tmp_path: Path, slug: str = "adjective_agreement") -> Path:
    req = {
        "slug": slug,
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
        "notes": "teach agreement",
    }
    req_dir = tmp_path / "data" / "concept_requirements"
    req_dir.mkdir(parents=True, exist_ok=True)
    (req_dir / f"{slug}.json").write_text(
        json.dumps(req, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (tmp_path / "store").mkdir(parents=True, exist_ok=True)
    return tmp_path


class _FakeAuthor:
    def invoke(self, prompt: str, **kw: Any) -> str:
        if "metadata" in prompt.lower():
            return json.dumps(
                {
                    "title": "Adjective agreement",
                    "goal": "Learn agreement.",
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


def _patch_author_agent(monkeypatch) -> None:
    from lesson_builder.pipeline import cold_author as cold_pkg

    monkeypatch.setattr(cold_pkg.flow, "author", lambda: _FakeAuthor())


def _patch_offline_fixer_judge(monkeypatch) -> None:
    """Task F: ``noop`` is no longer a CLI/registry choice. Keep these CLI tests
    offline by pointing the flow's registries at the importable (but unselectable)
    noop collaborators for the duration of the test."""
    from lesson_builder.pipeline import graph_runner
    from lesson_builder.pipeline.judges import noop_judge
    from lesson_builder.pipeline.lesson_qa_graph import noop_fixer

    monkeypatch.setattr(graph_runner, "K_FIXERS", {"codex": noop_fixer})
    monkeypatch.setattr(graph_runner, "K_JUDGES", {"real": noop_judge})


def test_author_cli_given_cold_slug_expect_parks_and_parsers_wired(
    tmp_path: Path, monkeypatch
):
    repo = _write_requirements(tmp_path)
    monkeypatch.setattr(graph_runner, "K_CHECKPOINTS_PATH", repo / "store" / "checkpoints.db")
    _patch_author_agent(monkeypatch)
    _patch_offline_fixer_judge(monkeypatch)

    parser = _build_parser()
    args = parser.parse_args(
        ["author", "adjective_agreement", "--run-id", "cli_a1", "--repo-root", str(repo)]
    )
    rc = args.func(args)

    assert rc == 0


def test_author_cli_given_existing_lesson_without_force_expect_refused(
    tmp_path: Path, monkeypatch
):
    repo = _write_requirements(tmp_path)
    lessons = repo / "data" / "lessons"
    lessons.mkdir(parents=True, exist_ok=True)
    (lessons / "adjective_agreement.json").write_text("{}", encoding="utf-8")
    _patch_author_agent(monkeypatch)
    _patch_offline_fixer_judge(monkeypatch)

    parser = _build_parser()
    args = parser.parse_args(
        ["author", "adjective_agreement", "--repo-root", str(repo)]
    )
    rc = args.func(args)

    assert rc == 0  # the CLI returns 0 and prints the refusal result


def test_import_cli_given_internal_lesson_file_expect_registers(
    tmp_path: Path, monkeypatch
):
    # setup: import a real internal Lesson from the repo.
    src = ROOT / "data" / "lessons" / "ordinal_numbers.json"
    import_file = tmp_path / "ordinal_numbers.json"
    import_file.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)

    parser = _build_parser()
    args = parser.parse_args(
        ["import", str(import_file), "--repo-root", str(repo), "--commit"]
    )
    rc = args.func(args)

    assert rc == 0
    assert (repo / "data" / "lessons" / "ordinal_numbers.json").exists()
    assert not (repo / "dist" / "lessons" / "ordinal_numbers.json").exists()
    # ledger lands under data/ (where default_loader reads it), not flat at repo root
    assert (repo / "data" / "lesson_acceptance_log.jsonl").exists()
    assert not (repo / "lesson_acceptance_log.jsonl").exists()


def test_import_cli_without_commit_expect_scratch_not_tracked(tmp_path: Path):
    # A bare import (no --commit) must NOT dirty the tracked tree; it writes
    # under store/scratch/ instead (mirrors run_graph scratch-vs-commit).
    src = ROOT / "data" / "lessons" / "ordinal_numbers.json"
    import_file = tmp_path / "ordinal_numbers.json"
    import_file.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)

    parser = _build_parser()
    args = parser.parse_args(["import", str(import_file), "--repo-root", str(repo)])
    rc = args.func(args)

    assert rc == 0
    # tracked tree stays clean
    assert not (repo / "data" / "lessons" / "ordinal_numbers.json").exists()
    assert not (repo / "dist" / "lessons" / "ordinal_numbers.json").exists()
    assert not (repo / "data" / "lesson_acceptance_log.jsonl").exists()
    # scratch gets the write
    scratch = repo / "store" / "scratch"
    assert (scratch / "data" / "lessons" / "ordinal_numbers.json").exists()
    assert (scratch / "lesson_acceptance_log.jsonl").exists()


def test_regenerate_dist_cli_given_data_lesson_expect_writes_projection(tmp_path: Path):
    src = ROOT / "data" / "lessons" / "ordinal_numbers.json"
    repo = tmp_path / "repo"
    lessons_dir = repo / "data" / "lessons"
    lessons_dir.mkdir(parents=True)
    (lessons_dir / "ordinal_numbers.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    parser = _build_parser()
    args = parser.parse_args(["regenerate-dist", "--repo-root", str(repo)])
    rc = args.func(args)

    assert rc == 0
    assert (repo / "dist" / "lessons" / "ordinal_numbers.json").exists()
