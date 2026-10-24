"""Entry points: reviewer suite case and production-prompt contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from evals.promptfoo.prompts import create_review_prompt
from lesson_builder.domain.lesson.services.review_projection import build_expected_answers

K_REPO_ROOT = Path(__file__).resolve().parents[2]
K_PROMPTFOO_ROOT = K_REPO_ROOT / "evals/promptfoo"
K_REVIEW_CASE_PATH = K_PROMPTFOO_ROOT / "cases/reviews.yaml"
K_REVIEW_CONFIG_PATH = K_PROMPTFOO_ROOT / "reviews.yaml"
K_REQUIRED_REVIEW_CASE_IDS = frozenset(
    {
        "answer-composite-map",
        "answer-match-pairs-control",
        "lesson-meaning-reversal",
        "minimal-past-tense-clean",
        "minimal-past-tense-planted",
        "preservation-dropped-contrast",
        "standalone-context-coverage",
        "answer-bring-meaning",
        "attempt-context-coverage",
        "open-rubric-blocked-task",
        "review-edit-jo-desto-planted",
        "review-edit-jo-desto-control",
        "review-edit-language-conflict",
        "review-edit-present-perfect-criterion",
        "open-rubric-hidden-tense",
        "open-rubric-explicit-tense",
    }
)
K_REQUIRED_CASE_METADATA = ("case_id", "surface", "kind", "source_lesson")
K_REVIEW_ITEM_SURFACES = frozenset({"answer", "attempt", "open_rubric", "open_semantic", "standalone_review"})


def test_reviewer_cases_given_scored_suite_expect_declared_python_outcomes():
    cases = _load_cases(K_REVIEW_CASE_PATH)
    cases_by_id = {case["metadata"]["case_id"]: case for case in cases}

    assert len(cases) == 16
    assert set(cases_by_id) == K_REQUIRED_REVIEW_CASE_IDS
    for case_id, case in cases_by_id.items():
        _assert_case_structure(case_id, case)
        _assert_case_outcome_declarations(case_id, case)
        _assert_case_promptfoo_options(case_id, case)


def test_reviewer_fixtures_given_blocked_task_and_tense_pair_expect_unambiguous_prompts():
    cases = _cases_by_id()
    blocked_lesson = json.loads(cases["open-rubric-blocked-task"]["vars"]["lesson"])
    hidden_lesson = json.loads(cases["open-rubric-hidden-tense"]["vars"]["lesson"])
    explicit_lesson = json.loads(cases["open-rubric-explicit-tense"]["vars"]["lesson"])
    blocked_criteria = blocked_lesson["elements"][0]["payload"]["criteria"]
    hidden_prompt = hidden_lesson["elements"][0]["prompt"][0]["value"]
    explicit_prompt = explicit_lesson["elements"][0]["prompt"][0]["value"]

    assert blocked_criteria[0]["instruction"] == "States the blocked task."
    assert "one sentence" not in hidden_prompt
    assert "one sentence" not in explicit_prompt
    assert "present perfect" not in hidden_prompt
    assert "present perfect" in explicit_prompt


def test_review_prompt_given_past_tense_case_expect_draft_and_production_schema():
    case = _cases_by_id()["minimal-past-tense-clean"]

    prompt = create_review_prompt({"vars": case["vars"]})

    assert "particular known past occasion" in prompt
    assert "LessonQualityReview" in prompt


def test_promptfoo_config_given_review_suite_expect_case_file_loaded():
    config = yaml.safe_load(K_REVIEW_CONFIG_PATH.read_text(encoding="utf-8"))

    assert config["tests"] == "file://cases/reviews.yaml"
    assert config["providers"]
    assert config["defaultTest"] == "file://defaults/structured-response.yaml"


def _load_cases(path: Path) -> list[dict]:
    cases = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(cases, list), f"{path.name}: expected a YAML case list"
    return cases


def _cases_by_id() -> dict[str, dict]:
    return {case["metadata"]["case_id"]: case for case in _load_cases(K_REVIEW_CASE_PATH)}


def _assert_case_structure(case_id: str, case: dict) -> None:
    metadata = case["metadata"]
    for field in K_REQUIRED_CASE_METADATA:
        assert metadata.get(field), f"{case_id}: missing metadata field {field}"
    if metadata["surface"] == "lesson_review":
        anchor = case["vars"].get("expected_target_anchor")
        assert isinstance(anchor, str) and anchor.strip(), f"{case_id}: missing target anchor"
        draft = case["vars"].get("draft")
        if not isinstance(draft, str):
            draft_path = case["vars"].get("draft_path")
            assert isinstance(draft_path, str) and draft_path, f"{case_id}: missing draft source"
            draft = (K_REPO_ROOT / draft_path).read_text(encoding="utf-8")
        normalized_anchor = " ".join(anchor.split()).casefold()
        normalized_draft = " ".join(draft.split()).casefold()
        assert normalized_anchor in normalized_draft, f"{case_id}: target anchor is absent from the draft"
    assert case["vars"]["surface"] == metadata["surface"]
    assert case["assert"] == [
        {
            "type": "python",
            "metric": case["assert"][0]["metric"],
            "value": "file://assertions.py:validate_review_outcomes",
        }
    ]
    assert case["assert"][0]["metric"] in {"false_alarm", "planted_error_recall", "answer_accuracy"}
    assert "llm-rubric" not in str(case["assert"]), f"{case_id}: grader model call remains"


def _assert_case_outcome_declarations(case_id: str, case: dict) -> None:
    surface = case["metadata"]["surface"]
    variables = case["vars"]
    if surface in K_REVIEW_ITEM_SURFACES | {"review_edit"}:
        expected_ids = variables["expected_exercise_ids"]
        assert len(expected_ids) == len(set(expected_ids)), f"{case_id}: duplicate expected exercise id"
    if surface == "review_edit":
        assert "expected_finding_handles" in variables, f"{case_id}: no finding-handle expectation"
    if surface == "preservation_review":
        assert "expected_issue_categories" in variables, f"{case_id}: no issue-category expectation"
    if surface in K_REVIEW_ITEM_SURFACES:
        assert any(name in variables for name in ("expected_statuses", "expected_issue_categories")), (
            f"{case_id}: no item outcome expectation"
        )
    if surface == "answer":
        assert "expected_answers" not in variables, f"{case_id}: answers must come from the lesson key"
        source_answers = build_expected_answers(json.loads(variables["lesson"]))
        assert set(source_answers) == set(variables["expected_exercise_ids"]), (
            f"{case_id}: expected answers do not cover the declared exercises"
        )
    if surface == "lesson_review":
        assert isinstance(variables.get("accepted_issue_categories"), list), f"{case_id}: no accepted codes"


def _assert_case_promptfoo_options(case_id: str, case: dict) -> None:
    options = case.get("options", {})
    for name, value in case["vars"].items():
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            assert options.get("disableVarExpansion") is True, f"{case_id}/{name}: string lists expand"
