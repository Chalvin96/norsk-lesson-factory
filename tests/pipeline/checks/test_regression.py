import json
from pathlib import Path

from lesson_builder.pipeline.checks.validators.regression import regression_check
from lesson_builder.schema import Lesson, to_export_dict

ROOT = Path(__file__).resolve().parents[3]


def _lesson():
    return json.loads((ROOT / "data/lessons/past_tense.json").read_text())


def test_regression_check_given_matching_export_baseline_expect_no_results():
    lesson = _lesson()
    baseline = to_export_dict(Lesson.model_validate(lesson))
    assert regression_check("past_tense", lesson, baseline) == []


def test_regression_check_given_different_export_baseline_expect_warning():
    lesson = _lesson()
    baseline = to_export_dict(Lesson.model_validate(lesson))
    baseline["title"] = "Changed title"
    results = regression_check("past_tense", lesson, baseline)
    assert len(results) == 1
    assert results[0].check_id == "regression_diff"
    assert results[0].severity == "warning"


def test_regression_check_given_no_baseline_expect_no_results():
    assert regression_check("past_tense", _lesson(), None) == []
