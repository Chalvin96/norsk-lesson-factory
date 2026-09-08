"""Entry points: metric and gate behavior tests for AI evaluation reports."""

from __future__ import annotations

import pytest

from scripts.eval_metrics import EvalObservation
from scripts.eval_metrics import build_report
from scripts.eval_metrics import calculate_classification_metrics
from scripts.eval_metrics import calculate_group_metrics
from scripts.eval_metrics import count_confusion_matrix


def test_count_confusion_matrix_given_labeled_observations_expect_binary_counts():
    observations = [
        EvalObservation("bad-pass", "family", "bad", predicted="bad"),
        EvalObservation("bad-miss", "family", "bad", predicted="good"),
        EvalObservation("good-false-alarm", "family", "good", predicted="bad"),
        EvalObservation("good-pass", "family", "good", predicted="good"),
        EvalObservation("outage", "family", "bad", outcome="outage"),
    ]

    assert count_confusion_matrix(observations) == {
        "true_positive": 1,
        "false_positive": 1,
        "true_negative": 1,
        "false_negative": 1,
    }


def test_calculate_classification_metrics_given_no_positive_predictions_expect_explicit_none():
    observations = [EvalObservation("good", "family", "good", predicted="good")]

    assert calculate_classification_metrics(observations) == {
        "precision": None,
        "recall": None,
        "specificity": 1.0,
        "f1": None,
    }


def test_calculate_group_metrics_given_adversarial_categories_expect_separate_confusion():
    observations = [
        EvalObservation("bad-a", "family-a", "bad", predicted="bad", adversarial_category="hidden_key"),
        EvalObservation("good-a", "family-a", "good", predicted="bad", adversarial_category="hidden_key"),
        EvalObservation("bad-b", "family-b", "bad", predicted="good", adversarial_category="capability"),
    ]

    grouped = calculate_group_metrics(observations, by="adversarial_category")

    assert grouped["hidden_key"]["confusion"] == {
        "true_positive": 1,
        "false_positive": 1,
        "true_negative": 0,
        "false_negative": 0,
    }
    assert grouped["capability"]["counts"]["evaluated"] == 1


def test_calculate_group_metrics_given_unsupported_group_expect_validation_error():
    with pytest.raises(ValueError, match="grouping must be family or adversarial_category"):
        calculate_group_metrics([], by="model")


def test_build_report_given_not_evaluated_case_expect_category_and_id_coverage_visible():
    report = build_report(
        [EvalObservation("semantic", "family", "bad", outcome="not_evaluated", adversarial_category="semantic")],
        corpus_hash="corpus-sha",
        expected_case_ids=["semantic"],
        returned_case_ids=["semantic"],
    )

    assert report["counts"]["not_evaluated"] == 1
    assert report["by_adversarial_category"]["semantic"]["counts"]["evaluated"] == 0
    assert report["returned_id_coverage"]["complete"] is True
    assert report["gate"]["passed"] is False
    assert "not-evaluated observations are present" in report["gate"]["failures"]


def test_observation_given_blank_adversarial_category_expect_validation_error():
    with pytest.raises(ValueError, match="adversarial_category must be non-empty"):
        EvalObservation("case", "family", "bad", predicted="bad", adversarial_category=" ")


def test_build_report_given_duplicate_ids_expect_provenance_and_missing_id():
    observations = [
        EvalObservation("case-a", "family-a", "bad", predicted="bad"),
        EvalObservation("case-b", "family-b", "good", predicted="good"),
    ]

    report = build_report(
        observations,
        corpus_hash="corpus-sha",
        config_hash="config-sha",
        prompt_hash="prompt-sha",
        model="test-model",
        expected_case_ids=["case-a", "case-b"],
        returned_case_ids=["case-a", "case-a"],
    )

    assert report["provenance"] == {
        "corpus_sha256": "corpus-sha",
        "config_sha256": "config-sha",
        "prompt_sha256": "prompt-sha",
        "model": "test-model",
    }
    assert report["returned_id_coverage"] == {
        "expected": 2,
        "returned": 2,
        "missing": ["case-b"],
        "unexpected": [],
        "duplicates": ["case-a"],
        "complete": False,
    }
    assert report["gate"]["passed"] is False


def test_threshold_gate_given_metric_breach_and_invalid_observation_expect_failure_reasons():
    observations = [
        EvalObservation("bad", "family", "bad", predicted="good"),
        EvalObservation("invalid", "family", "good", outcome="invalid"),
    ]

    report = build_report(
        observations,
        corpus_hash="corpus-sha",
        expected_case_ids=["bad"],
        returned_case_ids=[],
        thresholds={"min_recall": 1.0},
    )

    assert report["gate"]["passed"] is False
    assert "recall below minimum 1.0" in report["gate"]["failures"]
    assert "invalid observations are present" in report["gate"]["failures"]
    assert "returned case IDs are incomplete or duplicated" in report["gate"]["failures"]


def test_observation_given_non_evaluated_prediction_expect_validation_error():
    with pytest.raises(ValueError, match="non-evaluated observations cannot carry a prediction"):
        EvalObservation("case", "family", "bad", predicted="bad", outcome="outage")
