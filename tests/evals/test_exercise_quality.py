"""Behavior tests for the frozen consensus exercise-quality evaluation layer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

import scripts.eval_exercise_quality as evaluation_script
from lesson_builder.domain.lesson.validation.review_payloads import extract_answer_questions
from lesson_builder.domain.lesson.validation.review_payloads import extract_attempt_questions
from scripts.eval_exercise_quality import main as run_evaluation
from tests.evals.exercise_quality import K_EXERCISE_QUALITY_FAMILIES
from tests.evals.exercise_quality import K_EXERCISE_QUALITY_FIXTURE
from tests.evals.exercise_quality import K_EXPECTED_MECHANICAL_CODES
from tests.evals.exercise_quality import load_consensus_cases
from tests.evals.exercise_quality import project_semantic_case
from tests.evals.utils import assert_semantic_projection
from tests.evals.utils import audit_snapshot


def test_consensus_cases_given_frozen_fixture_expect_exact_family_provenance():
    cases = load_consensus_cases()
    assert {case["family"] for case in cases} == K_EXERCISE_QUALITY_FAMILIES
    assert len(cases) == 9
    assert len({(case["lesson"], case["handle"]) for case in cases}) == 9
    for case in cases:
        assert case["evidence"]
        assert case["bad"] != case["good"]
        assert case["mode"] in {"mechanical", "semantic"}
        assert case["surfaces"] or case.get("deterministic_only") is True
        if case.get("deterministic_only"):
            assert "live" not in case


def test_mechanical_cases_given_source_snapshots_expect_production_audit_detects_bad_only(tmp_path: Path):
    cases = [case for case in load_consensus_cases() if case["mode"] == "mechanical"]
    for case in cases:
        bad_audit = audit_snapshot(tmp_path / case["family"] / "bad", case["bad"])
        good_audit = audit_snapshot(tmp_path / case["family"] / "good", case["good"])
        bad_codes = {finding.code for finding in bad_audit.findings}
        assert K_EXPECTED_MECHANICAL_CODES[case["family"]] in bad_codes
        assert good_audit.status == "clean"
        assert not good_audit.findings


def test_semantic_cases_given_compiled_internal_snapshots_expect_production_projections():
    semantic_cases = [case for case in load_consensus_cases() if case["mode"] == "semantic" or "review_bad" in case]
    for case in semantic_cases:
        for state in ("bad", "good"):
            assert_semantic_projection(case, state)


def test_semantic_cases_given_bad_and_good_projections_expect_visible_repairs():
    expected_pairs = {
        "isolating-an-unfamiliar-word": ("unfamiliar word", "hylla"),
        "use_irregular_forms_for_actions": ("lengre", "lenger"),
        "room_article_hunt": ("correct Norwegian", "traditional three-gender"),
        "reconstruct_the_meeting_route": ("route-location-direction-switching", "room"),
    }
    for case in load_consensus_cases():
        if case["mode"] != "semantic":
            continue
        bad_text = json.dumps(extract_answer_questions(project_semantic_case(case)), ensure_ascii=False)
        good_text = json.dumps(extract_answer_questions(project_semantic_case(case, "good")), ensure_ascii=False)
        bad_marker, good_marker = expected_pairs[case["handle"]]
        assert bad_marker in bad_text or bad_marker in json.dumps(
            extract_attempt_questions(project_semantic_case(case))
        )
        assert good_marker in good_text or good_marker in json.dumps(
            extract_attempt_questions(project_semantic_case(case, "good"))
        )


def test_quality_evaluation_given_all_corpus_with_semantic_cases_expect_strict_failure(tmp_path: Path):
    output_path = tmp_path / "all-report.json"

    exit_code = run_evaluation(
        [
            "--corpus",
            str(K_EXERCISE_QUALITY_FIXTURE),
            "--config",
            str(tmp_path / "missing-config.yaml"),
            "--output",
            str(output_path),
            "--strict",
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert report["corpus"]["selected_mode"] == "all"
    assert report["counts"]["not_evaluated"] == 8
    assert report["gate"]["passed"] is False
    assert "not-evaluated observations are present" in report["gate"]["failures"]


def test_quality_evaluation_given_mechanical_mode_expect_strict_pass(tmp_path: Path):
    output_path = tmp_path / "mechanical-report.json"

    exit_code = run_evaluation(
        [
            "--corpus",
            str(K_EXERCISE_QUALITY_FIXTURE),
            "--config",
            str(tmp_path / "missing-config.yaml"),
            "--output",
            str(output_path),
            "--mode",
            "mechanical",
            "--strict",
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["corpus"]["selected_mode"] == "mechanical"
    assert report["counts"] == {
        "observations": 10,
        "evaluated": 10,
        "not_evaluated": 0,
        "invalid": 0,
        "outage": 0,
    }
    assert report["gate"]["passed"] is True


def test_quality_evaluation_given_absolute_snapshot_filename_expect_sandbox_rejection(tmp_path: Path):
    escaped_path = tmp_path / "escaped.txt"
    corpus_path = tmp_path / "unsafe-corpus.yaml"
    corpus_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "family": "unsafe_snapshot_path",
                        "lesson": "sandbox test",
                        "handle": "sandbox-path",
                        "mode": "mechanical",
                        "surfaces": ["source"],
                        "evidence": "absolute filename must not be written",
                        "label_source": "test",
                        "captured_at": "2026-08-29",
                        "adversarial_category": "sandbox_escape",
                        "bad": {str(escaped_path): "must not be written"},
                        "good": {"lesson.md": "", "exercises.yaml": "[]"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "unsafe-report.json"

    run_evaluation(
        [
            "--corpus",
            str(corpus_path),
            "--config",
            str(tmp_path / "missing-config.yaml"),
            "--output",
            str(output_path),
            "--mode",
            "mechanical",
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert not escaped_path.exists()
    assert report["counts"]["invalid"] == 1
    assert report["errors"][0]["case_id"] == "sandbox-path__bad"
    assert "safe relative path" in report["errors"][0]["error"]


def test_quality_evaluation_given_absolute_case_family_expect_sandbox_rejection(tmp_path: Path):
    escaped_root = tmp_path / "escaped-family"
    corpus_path = tmp_path / "unsafe-family-corpus.yaml"
    corpus_path.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "cases": [
                    {
                        "family": str(escaped_root),
                        "lesson": "sandbox test",
                        "handle": "sandbox-family",
                        "mode": "mechanical",
                        "surfaces": ["source"],
                        "evidence": "absolute family must not be created",
                        "label_source": "test",
                        "captured_at": "2026-08-29",
                        "adversarial_category": "sandbox_escape",
                        "bad": {"lesson.md": "must not be written"},
                        "good": {"lesson.md": "must not be written"},
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "unsafe-family-report.json"

    run_evaluation(
        [
            "--corpus",
            str(corpus_path),
            "--config",
            str(tmp_path / "missing-config.yaml"),
            "--output",
            str(output_path),
            "--mode",
            "mechanical",
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert not escaped_root.exists()
    assert report["counts"]["invalid"] == 2
    assert all("safe identifier" in error["error"] for error in report["errors"])


@pytest.mark.parametrize(
    ("section", "key", "value", "message"),
    [
        ("thresholds", "min_recLl", 1.0, "unsupported exercise-quality threshold key"),
        ("thresholds", "min_recall", 1.1, "must be between 0 and 1"),
        ("thresholds", "min_recall", float("inf"), "must be finite"),
        ("thresholds", "min_recall", float("nan"), "must be finite"),
    ],
)
def test_quality_evaluation_given_invalid_limit_configuration_expect_explicit_error(
    tmp_path: Path, section: str, key: str, value: float, message: str
):
    config_path = tmp_path / "invalid-config.yaml"
    config_path.write_text(
        yaml.safe_dump({"evaluation": {"exercise_quality": {section: {key: value}}}}),
        encoding="utf-8",
    )

    with pytest.raises((TypeError, ValueError), match=message):
        run_evaluation(
            [
                "--corpus",
                str(K_EXERCISE_QUALITY_FIXTURE),
                "--config",
                str(config_path),
                "--output",
                str(tmp_path / "invalid-report.json"),
                "--mode",
                "mechanical",
            ]
        )


def test_quality_evaluation_given_missing_response_id_expect_incomplete_coverage(tmp_path: Path, monkeypatch):
    output_path = tmp_path / "incomplete-report.json"

    def skip_case(*_args: object) -> None:
        return None

    monkeypatch.setattr(evaluation_script, "_evaluate_case", skip_case)
    run_evaluation(
        [
            "--corpus",
            str(K_EXERCISE_QUALITY_FIXTURE),
            "--config",
            str(tmp_path / "missing-config.yaml"),
            "--output",
            str(output_path),
            "--mode",
            "mechanical",
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["returned_id_coverage"] == {
        "expected": 10,
        "returned": 0,
        "missing": [
            "first_checkpoint_opening__bad",
            "first_checkpoint_opening__good",
            "form_regular_comparatives__bad",
            "form_regular_comparatives__good",
            "form_regular_definite_plurals__bad",
            "form_regular_definite_plurals__good",
            "moving-note-correction__bad",
            "moving-note-correction__good",
            "practice_4_understand_and_accept__bad",
            "practice_4_understand_and_accept__good",
        ],
        "unexpected": [],
        "duplicates": [],
        "complete": False,
    }
