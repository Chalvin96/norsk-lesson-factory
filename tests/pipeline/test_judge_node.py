"""The graph runs the injected judge and folds its reviews as advisory."""
from __future__ import annotations

import json
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from lesson_builder.pipeline.lesson_qa_graph import GraphDeps, LoadedLesson, build_lesson_qa_graph, noop_fixer
from lesson_builder.pipeline.state import thread_id_for

ROOT = Path(__file__).resolve().parents[2]


def _loaded(slug: str) -> LoadedLesson:
    lesson = json.loads((ROOT / f"data/lessons/{slug}.json").read_text())
    reqs = json.loads((ROOT / f"data/concept_requirements/{slug}.json").read_text())
    return LoadedLesson(lesson=lesson, requirements=reqs, baseline_export=None, recorded_requirements_hash=None)


def test_graph_folds_injected_pedagogy_issue_as_advisory():
    slug = "ordinal_numbers"
    bad_ped = {
        "pedagogy_review": {
            "scores": dict.fromkeys(
                ("on_concept", "complete", "bokmal", "sequencing", "presentable", "answerable", "depth"), 2
            ),
            "summary": "weak",
            "passes": [],
            "issues": [
                {
                    "severity": "P2",
                    "category": "depth",
                    "title": "too shallow",
                    "evidence": "only 1 example",
                    "fix": "add examples",
                }
            ],
        },
        "objective_alignment_review": None,
        "answer_review": None,
    }
    deps = GraphDeps(loader=lambda s, *, repo_root: _loaded(s), fixer=noop_fixer, judge=lambda lesson: bad_ped)
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": thread_id_for(slug, "j1")}}
    graph.invoke(
        {
            "slug": slug,
            "run_id": "j1",
            "repo_root": str(ROOT),
            "acceptance_log_path": str(ROOT / "x.jsonl"),
            "output_root": str(ROOT / "x"),
        },
        config=cfg,
    )
    state = graph.get_state(cfg)
    advisory = [
        i
        for i in state.values["current_issues"]
        if i.get("advisory") and i.get("check_id") == "pedagogy_check"
    ]
    assert advisory, "expected the injected pedagogy issue folded as advisory"
    assert not any(i.get("is_blocking") for i in advisory), "advisory must not block pre-calibration"


def test_graph_folds_injected_naturalness_issue_as_advisory():
    slug = "ordinal_numbers"
    judge_output = {
        "naturalness_review": {
            "scores": {
                "idiomatic_phrasing": 3,
                "register_appropriateness": 4,
                "terminology_consistency": 4,
            },
            "issues": [
                {
                    "unit_id": "ex_001",
                    "severity": "P2",
                    "message": "Prefer 'heller ikke' over 'ikke ... heller' as the neutral default.",
                }
            ],
        },
        "pedagogy_review": None,
        "objective_alignment_review": None,
        "answer_review": None,
    }
    deps = GraphDeps(
        loader=lambda s, *, repo_root: _loaded(s),
        fixer=noop_fixer,
        judge=lambda lesson: judge_output,
    )
    graph = build_lesson_qa_graph(deps, checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": thread_id_for(slug, "j2")}}
    graph.invoke(
        {
            "slug": slug,
            "run_id": "j2",
            "repo_root": str(ROOT),
            "acceptance_log_path": str(ROOT / "x.jsonl"),
            "output_root": str(ROOT / "x"),
        },
        config=cfg,
    )
    state = graph.get_state(cfg)
    # judge_node populates naturalness_review in state
    assert state.values.get("naturalness_review") is not None
    # run_checks_node folds it as advisory, non-blocking
    naturalness_advisory = [
        i
        for i in state.values["current_issues"]
        if i.get("advisory") and i.get("check_id") == "naturalness_check"
    ]
    assert naturalness_advisory, "expected the injected naturalness issue folded as advisory"
    assert not any(i.get("is_blocking") for i in naturalness_advisory), "advisory must not block"
