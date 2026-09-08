"""Tests for the catalog-package graph nodes using fake deps."""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from lesson_builder.workflow.lesson_generation.graph import build_lesson_generation_graph
from tests.paths import K_CATALOG_PACKAGE_FIXTURE_ROOT
from tests.workflow.lesson_generation.fakes import FakeLessonPackageDeps
from tests.workflow.lesson_generation.fakes import FakeRichAuthoringStages


def _initial_state(output_root: Path, run_id: str = "graph-test") -> dict:
    return {
        "run_id": run_id,
        "repo_root": str(output_root),
        "output_root": str(output_root),
        "fixture_source": str(K_CATALOG_PACKAGE_FIXTURE_ROOT),
    }


def test_graph_given_rich_stage_failure_expect_resume_from_failed_checkpoint(tmp_path: Path):
    stages = FakeRichAuthoringStages(fail_stage="draft_reviewed")
    fakes = FakeLessonPackageDeps()
    deps = fakes.deps()
    deps.rich_stages = stages
    graph = build_lesson_generation_graph(deps, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "rich-retry"}}
    initial = _initial_state(tmp_path, run_id="rich-retry")
    initial["generation_job"] = "author"

    try:
        graph.invoke(initial, config=config)
    except RuntimeError as exc:
        assert str(exc) == "injected draft_reviewed failure"
    else:
        raise AssertionError("injected rich stage failure was not propagated")

    assert stages.calls == ["draft_authored", "draft_reviewed"]
    graph.invoke(None, config=config)

    assert stages.calls == [
        "draft_authored",
        "draft_reviewed",
        "draft_reviewed",
        "draft_normalized",
        "normalization_preserved",
        "intent_reviewed",
        "exercises_authored",
        "exercises_compiled",
        "exercises_verified",
        "complete",
    ]


def test_graph_given_prepare_run_expect_copier_and_compiler_called(tmp_path: Path):
    fakes = FakeLessonPackageDeps()
    graph = build_lesson_generation_graph(fakes.deps(), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t1"}}

    graph.invoke(_initial_state(tmp_path), config=config)

    assert fakes.copier_calls == 1
    assert fakes.compiler_calls == 1
    assert fakes.ledger_calls == 0


def test_graph_given_accept_resume_expect_finalize_writes_ledger(tmp_path: Path):
    fakes = FakeLessonPackageDeps()
    graph = build_lesson_generation_graph(fakes.deps(), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t2"}}

    graph.invoke(_initial_state(tmp_path), config=config)
    graph.invoke(Command(resume={"status": "accept"}), config=config)

    assert fakes.ledger_calls == 1
    assert len(fakes.ledger_entries) == 1


def test_graph_given_source_changed_after_prepare_expect_acceptance_refused(tmp_path: Path):
    fakes = FakeLessonPackageDeps()
    graph = build_lesson_generation_graph(fakes.deps(), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t-source-changed"}}

    graph.invoke(_initial_state(tmp_path), config=config)
    source_dir = tmp_path / "source"
    (source_dir / "lesson.md").write_text(
        (source_dir / "lesson.md").read_text(encoding="utf-8") + "\nChanged after prepare.\n",
        encoding="utf-8",
    )

    try:
        graph.invoke(Command(resume={"status": "accept"}), config=config)
    except ValueError as exc:
        assert "source changed" in str(exc)
    else:
        raise AssertionError("acceptance unexpectedly used stale prepared bytes")

    assert fakes.ledger_calls == 0


def test_graph_given_defer_resume_expect_no_ledger_write(tmp_path: Path):
    fakes = FakeLessonPackageDeps()
    graph = build_lesson_generation_graph(fakes.deps(), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t3"}}

    graph.invoke(_initial_state(tmp_path), config=config)
    result = graph.invoke(Command(resume={"status": "defer"}), config=config)

    assert fakes.ledger_calls == 0
    assert result["stage"] == "deferred"


def test_graph_given_reject_resume_expect_no_ledger_write(tmp_path: Path):
    fakes = FakeLessonPackageDeps()
    graph = build_lesson_generation_graph(fakes.deps(), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t4"}}

    graph.invoke(_initial_state(tmp_path), config=config)
    result = graph.invoke(Command(resume={"status": "reject"}), config=config)

    assert fakes.ledger_calls == 0
    assert result["stage"] == "rejected"
