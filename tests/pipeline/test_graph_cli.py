"""Tests for the graph CLI driver: run, show, resume, list."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

import lesson_builder.pipeline.cli as graph_cli
import lesson_builder.pipeline.graph_runner as graph_runner
from lesson_builder.pipeline.cli import (
    ThreadNotFoundError,
    ThreadNotParkedError,
    list_threads,
    resume_thread,
    run_graph,
    show_thread,
)

ROOT = Path(__file__).resolve().parents[2]

# An exercise the deterministic gate flags as blocking (judge op with
# ``is_correct=False`` and an empty feedback string), used to exercise the
# override / re-park / list paths against a thread that parks WITH blockers.
_BAD_JUDGE_EXERCISE = {
    "element_kind": "exercise",
    "id": "bad_judge_ex",
    "operation": "judge",
    "objective_id": "o1",
    "bloom_level": "understand",
    "derived_from": [],
    "prompt": [{"kind": "text", "value": "Mark the sentence."}],
    "explanation": None,
    "payload": {
        "sentence": [{"kind": "text", "value": "Eg gjekk heim."}],
        "is_correct": False,
        "feedback": "",
    },
}


def _tmp_repo(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path
    lesson = json.loads((ROOT / "data/lessons/ordinal_numbers.json").read_text())
    requirements = json.loads((ROOT / "data/concept_requirements/ordinal_numbers.json").read_text())
    (repo / "data/lessons").mkdir(parents=True, exist_ok=True)
    (repo / "data/concept_requirements").mkdir(parents=True, exist_ok=True)
    (repo / "data/lessons/ordinal_numbers.json").write_text(json.dumps(lesson, ensure_ascii=False, indent=2) + "\n")
    (repo / "data/concept_requirements/ordinal_numbers.json").write_text(
        json.dumps(requirements, ensure_ascii=False, indent=2) + "\n"
    )
    (repo / "store").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(graph_runner, "K_CHECKPOINTS_PATH", repo / "store" / "checkpoints.db")
    return repo


def _write_blocking_lesson(repo: Path, slug: str = "ordinal_numbers") -> None:
    """Append a blocking exercise to the seeded lesson so a run parks WITH blockers."""
    lesson_path = repo / "data" / "lessons" / f"{slug}.json"
    lesson = json.loads(lesson_path.read_text())
    lesson = deepcopy(lesson)
    objective_id = lesson["objectives"][0]["id"]
    bad_ex = deepcopy(_BAD_JUDGE_EXERCISE)
    bad_ex["objective_id"] = objective_id
    lesson["elements"].append(bad_ex)
    lesson_path.write_text(json.dumps(lesson, ensure_ascii=False, indent=2) + "\n")


def test_run_graph_given_clean_lesson_expect_parks_at_human_gate(tmp_path: Path, monkeypatch):
    repo = _tmp_repo(tmp_path, monkeypatch)
    result = run_graph("ordinal_numbers", repo_root=repo, run_id="cli1")

    assert result["park_status"] == "parked"
    assert result["next"] == ["human_gate"]
    assert result["slug"] == "ordinal_numbers"
    assert result["run_id"] == "cli1"
    assert result["thread_id"] == "ordinal_numbers:cli1"


def test_show_thread_given_parked_thread_expect_returns_state(tmp_path: Path, monkeypatch):
    repo = _tmp_repo(tmp_path, monkeypatch)
    run_graph("ordinal_numbers", repo_root=repo, run_id="cli2")
    result = show_thread("ordinal_numbers", "cli2", repo_root=repo)

    assert result["park_status"] == "parked"
    assert result["next"] == ["human_gate"]


def test_resume_thread_given_accept_decision_expect_exports(tmp_path: Path, monkeypatch):
    repo = _tmp_repo(tmp_path, monkeypatch)
    run_graph("ordinal_numbers", repo_root=repo, run_id="cli3", commit=True)
    result = resume_thread("ordinal_numbers", "cli3", {"status": "accept"}, repo_root=repo)

    assert result["park_status"] == "accepted"
    assert result["ledger_status"] == "accepted"
    assert result["next"] == []
    assert (repo / "dist/lessons/ordinal_numbers.json").exists()


def test_run_graph_given_no_commit_expect_scratch_export_leaves_tracked_clean(tmp_path: Path, monkeypatch):
    repo = _tmp_repo(tmp_path, monkeypatch)
    run_graph("ordinal_numbers", repo_root=repo, run_id="cli3b")
    result = resume_thread("ordinal_numbers", "cli3b", {"status": "accept"}, repo_root=repo)

    assert result["park_status"] == "accepted"
    assert result["ledger_status"] == "accepted"
    assert not (repo / "dist/lessons/ordinal_numbers.json").exists()
    assert not (repo / "data/lesson_acceptance_log.jsonl").exists()
    assert (repo / "store/scratch/dist/lessons/ordinal_numbers.json").exists()
    assert (repo / "store/scratch/lesson_acceptance_log.jsonl").exists()


# ---------------------------------------------------------------------------
# Fix 2: graph list
# ---------------------------------------------------------------------------


def test_list_threads_given_two_parked_runs_expect_both_listed(tmp_path: Path, monkeypatch):
    repo = _tmp_repo(tmp_path, monkeypatch)
    run_graph("ordinal_numbers", repo_root=repo, run_id="list-a")
    run_graph("ordinal_numbers", repo_root=repo, run_id="list-b")

    rows = list_threads()

    run_ids = {row["run_id"] for row in rows}
    assert {"list-a", "list-b"} <= run_ids
    for row in rows:
        assert row["slug"] == "ordinal_numbers"
        assert row["park_status"] == "parked"


def test_list_threads_given_accepted_run_expect_not_listed(tmp_path: Path, monkeypatch):
    repo = _tmp_repo(tmp_path, monkeypatch)
    run_graph("ordinal_numbers", repo_root=repo, run_id="list-done")
    resume_thread("ordinal_numbers", "list-done", {"status": "accept"}, repo_root=repo)

    rows = list_threads()

    assert "list-done" not in {row["run_id"] for row in rows}


# ---------------------------------------------------------------------------
# Fix 3: graph show --full renders the lesson + regression summary
# ---------------------------------------------------------------------------


def test_show_thread_given_full_flag_expect_renders_lesson_and_regression(tmp_path: Path, monkeypatch):
    repo = _tmp_repo(tmp_path, monkeypatch)
    run_graph("ordinal_numbers", repo_root=repo, run_id="show-full")

    result = show_thread("ordinal_numbers", "show-full", repo_root=repo, full=True)

    assert result["lesson"] is not None
    assert result["lesson"]["concept_slug"] == "ordinal_numbers"
    assert isinstance(result["regression_summary"], str)
    assert "no_baseline" not in result["regression_summary"]  # rendered, not a raw dict


def test_show_thread_given_no_full_flag_expect_no_lesson_key(tmp_path: Path, monkeypatch):
    repo = _tmp_repo(tmp_path, monkeypatch)
    run_graph("ordinal_numbers", repo_root=repo, run_id="show-plain")

    result = show_thread("ordinal_numbers", "show-plain", repo_root=repo)

    assert "lesson" not in result


# ---------------------------------------------------------------------------
# Fix 4: --override wiring
# ---------------------------------------------------------------------------


def test_resume_thread_given_accept_without_override_and_blockers_expect_reparks(
    tmp_path: Path, monkeypatch
):
    repo = _tmp_repo(tmp_path, monkeypatch)
    _write_blocking_lesson(repo)
    run_graph("ordinal_numbers", repo_root=repo, run_id="override-no")

    result = resume_thread("ordinal_numbers", "override-no", {"status": "accept"}, repo_root=repo)

    assert result["next"] == ["human_gate"]
    assert result["park_status"] == "parked"
    assert result["ledger_status"] is None


def test_resume_thread_given_accept_with_override_and_blockers_expect_exports_override(
    tmp_path: Path, monkeypatch
):
    repo = _tmp_repo(tmp_path, monkeypatch)
    _write_blocking_lesson(repo)
    run_graph("ordinal_numbers", repo_root=repo, run_id="override-yes")

    result = resume_thread(
        "ordinal_numbers",
        "override-yes",
        {"status": "accept", "override": True},
        repo_root=repo,
    )

    assert result["next"] == []
    assert result["park_status"] == "accepted"
    assert result["ledger_status"] == "accepted_override"


# ---------------------------------------------------------------------------
# Fix 5: respond removed from the CLI surface
# ---------------------------------------------------------------------------


def test_cli_decision_choices_given_respond_expect_not_advertised():
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    graph_cli._add_resume_parser(sub)
    resume_parser = sub.choices["resume"]
    decision_action = next(a for a in resume_parser._actions if a.dest == "decision")

    assert "respond" not in decision_action.choices
    assert set(decision_action.choices) == {"accept", "edit", "defer"}


def test_cli_resume_parser_given_no_notes_argument():
    import argparse

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    graph_cli._add_resume_parser(sub)
    resume_parser = sub.choices["resume"]
    dests = {a.dest for a in resume_parser._actions}

    assert "notes" not in dests


# ---------------------------------------------------------------------------
# Fix 6: clean error/empty states
# ---------------------------------------------------------------------------


def test_run_graph_given_unknown_slug_expect_lesson_load_error(tmp_path: Path, monkeypatch):
    # Task E: a missing/malformed lesson file surfaces as a typed LessonLoadError
    # instead of a bare FileNotFoundError/JSONDecodeError that crashes the graph.
    from lesson_builder.pipeline.lesson_qa_graph import LessonLoadError

    repo = _tmp_repo(tmp_path, monkeypatch)

    with pytest.raises(LessonLoadError):
        run_graph("no_such_slug", repo_root=repo, run_id="missing")


def test_show_thread_given_unknown_thread_expect_thread_not_found(tmp_path: Path, monkeypatch):
    repo = _tmp_repo(tmp_path, monkeypatch)
    # Open the checkpointer once so K_CHECKPOINTS_PATH exists, but never run a thread.
    with graph_runner._open_checkpointer(repo / "store" / "checkpoints.db"):
        pass

    with pytest.raises(ThreadNotFoundError):
        show_thread("ordinal_numbers", "never-ran", repo_root=repo)


def test_resume_thread_given_unparked_thread_expect_thread_not_parked(tmp_path: Path, monkeypatch):
    repo = _tmp_repo(tmp_path, monkeypatch)
    run_graph("ordinal_numbers", repo_root=repo, run_id="terminal")
    resume_thread("ordinal_numbers", "terminal", {"status": "accept"}, repo_root=repo)

    with pytest.raises(ThreadNotParkedError):
        resume_thread("ordinal_numbers", "terminal", {"status": "accept"}, repo_root=repo)


def test_run_graph_given_default_judge_name_expect_noop_offline_safe(tmp_path: Path, monkeypatch):
    """The library default ``judge_name`` stays 'noop' -- no live LLM call offline."""
    repo = _tmp_repo(tmp_path, monkeypatch)
    result = run_graph("ordinal_numbers", repo_root=repo, run_id="r1")
    assert result["slug"] == "ordinal_numbers"


def test_run_graph_given_unknown_judge_name_expect_value_error(tmp_path: Path, monkeypatch):
    repo = _tmp_repo(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="judge"):
        run_graph("ordinal_numbers", repo_root=repo, run_id="r1", judge_name="not-a-judge")


def test_cmd_run_given_unknown_slug_expect_clean_error_message(tmp_path: Path, monkeypatch, capsys):
    import argparse

    repo = _tmp_repo(tmp_path, monkeypatch)
    # Task F: noop is no longer a CLI choice; use the CLI defaults (codex/real).
    # The slug is unknown so the loader raises LessonLoadError before any fixer
    # or judge is invoked.
    args = argparse.Namespace(
        slug="no_such_slug", repo_root=str(repo), run_id="x", commit=False,
        fixer="codex", judge="real",
    )

    exit_code = graph_cli._cmd_run(args)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "no_such_slug" in captured.out
    assert "Traceback" not in captured.out
