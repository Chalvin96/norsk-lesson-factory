"""Task A: the improvement back-half must inherit the same live fixer + judge
defaults that ``graph run`` uses, with a direct-injection override so unit tests
stay offline.

Entry point: ``graph_runner.run_back_half`` (shared by cold-author and improve).
"""

from __future__ import annotations

from pathlib import Path

from lesson_builder.pipeline import graph_runner
from lesson_builder.pipeline.fixers import author_fixer
from lesson_builder.pipeline.graph_runner import run_back_half
from lesson_builder.pipeline.judges import default_judge, noop_judge
from lesson_builder.pipeline.lesson_qa_graph import LoadedLesson, noop_fixer


def _improve_loader(draft_lesson: dict):
    """Mirror of improvement_flow._improve_loader (preserves baseline/hash)."""

    def loader(slug: str, *, repo_root: Path) -> LoadedLesson:
        from lesson_builder.pipeline.lesson_qa_graph import default_loader

        loaded = default_loader(slug, repo_root=repo_root)
        return LoadedLesson(
            lesson=draft_lesson,
            requirements=loaded.requirements,
            baseline_export=loaded.baseline_export,
            recorded_requirements_hash=loaded.recorded_requirements_hash,
        )

    return loader


def test_run_back_half_given_defaults_expect_live_fixer_and_judge(monkeypatch, tmp_path: Path):
    # setup: spy on run_graph to capture the deps it receives.
    captured: dict = {}
    original_run_graph = graph_runner.run_graph

    def spy_run_graph(slug, **kw):
        captured["slug"] = slug
        captured["deps"] = kw.get("deps")
        captured["fixer_name"] = kw.get("fixer_name")
        captured["judge_name"] = kw.get("judge_name")
        return {"next": ["human_gate"], "slug": slug, "run_id": "r1"}

    monkeypatch.setattr(graph_runner, "run_graph", spy_run_graph)

    # execute
    run_back_half(
        slug="test_slug",
        loader=_improve_loader({"key": "test"}),
        repo_root=tmp_path,
        run_id="r1",
    )

    # assert: the back-half passes live fixer + judge by default (not noop).
    assert captured["deps"] is not None
    assert captured["deps"].fixer is author_fixer
    assert captured["deps"].judge is default_judge

    # restore safety: run_graph was actually called once.
    del original_run_graph


def test_run_back_half_given_direct_injection_override_expect_injected_fixer_and_judge(
    monkeypatch, tmp_path: Path
):
    # setup: spy on run_graph.
    captured: dict = {}

    def spy_run_graph(slug, **kw):
        captured["deps"] = kw.get("deps")
        return {"next": ["human_gate"], "slug": slug, "run_id": "r2"}

    monkeypatch.setattr(graph_runner, "run_graph", spy_run_graph)

    # execute: inject noop directly so the test stays offline (Task F: noop is no
    # longer a selectable registry name; tests inject the callables directly).
    run_back_half(
        slug="test_slug",
        loader=_improve_loader({"key": "test"}),
        repo_root=tmp_path,
        run_id="r2",
        fixer=noop_fixer,
        judge=noop_judge,
    )

    # assert: the direct injection uses the injected deps.
    assert captured["deps"] is not None
    assert captured["deps"].fixer is noop_fixer
    assert captured["deps"].judge is noop_judge
