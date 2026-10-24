"""Entry points: `response_contract`, `validate_review_outcomes`, and `validate_normalization_negative_markers`; Promptfoo calls them as response assertions."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel
from pydantic import ValidationError

from evals.promptfoo.lesson_generation.authoring_models import NormalizedPackage
from evals.promptfoo.lesson_generation.review_edit import ReviewEditPackage
from lesson_builder.application.operations.review_judges import AnswerReview
from lesson_builder.application.operations.review_judges import AttemptReview
from lesson_builder.application.operations.review_judges import OpenRubricReview
from lesson_builder.application.operations.review_judges import OpenSemanticReview
from lesson_builder.application.operations.review_standalone import StandaloneExerciseReview
from lesson_builder.clients.llm.base import extract_json_object
from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from lesson_builder.domain.lesson.models.quality_review import NormalizationPreservationReview
from lesson_builder.domain.lesson.services.review_projection import build_expected_answers
from lesson_builder.domain.lesson.services.review_projection import restore_answer_review_ids
from lesson_builder.domain.lesson.validation.checks.validators.answer_valid import compare_answers

try:
    from evals.promptfoo.prompts import K_PROMPTFOO_RESPONSE_SCHEMAS
    from evals.promptfoo.prompts import K_PROMPTFOO_REVIEW_SCHEMAS
except ModuleNotFoundError:
    from prompts import K_PROMPTFOO_RESPONSE_SCHEMAS
    from prompts import K_PROMPTFOO_REVIEW_SCHEMAS

K_PROMPTFOO_NORMALIZATION_SURFACE = "normalization"
K_PROMPTFOO_INCORRECT_ROLE_RE = re.compile(
    r"^:::\s+\{\.examples?\s+[^}]*\bteaching_role=incorrect\b[^}]*\}\s*$",
    re.MULTILINE,
)
K_PROMPTFOO_LEGACY_EXAMPLE_MARKER_RE = re.compile(
    r"^\s*-\s+(?:no|en):\s*['\"]?[*_`]*\s*"
    r"(?:✗|incorrect:|wrong:|intended meaning:|not standard:|not normally:|not:)",
    re.IGNORECASE | re.MULTILINE,
)


def response_contract(output: str, context: Mapping[str, Any]) -> dict[str, object]:
    """Validate one Promptfoo response with the production parser and schema."""
    variables = _context_variables(context)
    surface = variables.get("surface")
    if not isinstance(surface, str) or not surface.strip():
        return _failure("case variable `surface` must name a structured response surface")
    schema = K_PROMPTFOO_RESPONSE_SCHEMAS.get(surface)
    if schema is None:
        return _failure(f"unknown structured response surface {surface!r}")
    if not isinstance(output, str):
        return _failure(f"{surface} response is not text")
    try:
        schema.model_validate(extract_json_object(output))
    except (TypeError, ValueError, ValidationError) as exc:
        return _failure(f"{surface} response failed the production JSON/Pydantic contract: {exc}")
    return _success(f"{surface} response passed production JSON extraction and schema validation")


def validate_review_outcomes(output: str, context: Mapping[str, Any]) -> dict[str, object]:
    """Compare declared case outcomes with a production-schema reviewer response."""
    variables = _context_variables(context)
    surface = variables.get("surface")
    if not isinstance(surface, str):
        return _failure("review case variable `surface` must name a review response")
    review, problem = _parse_review_response(surface, output)
    if problem:
        return {
            "pass": True,
            "score": 0.0,
            "reason": f"protocol failure; scored under response_contract: {problem}",
        }
    if review is None:
        return _failure(f"unknown review response surface {surface!r}")
    return _validate_review_surface(review, variables)


def validate_normalization_negative_markers(output: str, context: Mapping[str, Any]) -> dict[str, object]:
    """Require typed incorrect-example roles without legacy inline markers."""
    variables = _context_variables(context)
    if variables.get("surface") != K_PROMPTFOO_NORMALIZATION_SURFACE:
        return _success("not applicable: this case is not a normalization response")
    if not isinstance(output, str):
        return _failure("normalization response is not text")
    try:
        package = NormalizedPackage.model_validate(extract_json_object(output))
    except (TypeError, ValueError, ValidationError) as exc:
        return _failure(f"normalization response cannot be checked for negative markers: {exc}")
    if K_PROMPTFOO_LEGACY_EXAMPLE_MARKER_RE.search(package.lesson_md):
        return _failure("normalized lesson retains a legacy inline incorrect-example marker")
    if not K_PROMPTFOO_INCORRECT_ROLE_RE.search(package.lesson_md):
        return _failure("normalized lesson does not encode the supplied negative example as teaching_role=incorrect")
    return _success("normalized lesson uses the typed incorrect-example role without inline status text")


def _parse_review_response(surface: str, output: object) -> tuple[BaseModel | None, str | None]:
    schema = K_PROMPTFOO_REVIEW_SCHEMAS.get(surface)
    if schema is None:
        return None, None
    if not isinstance(output, str):
        return None, "review response is not text"
    try:
        return schema.model_validate(extract_json_object(output)), None
    except (TypeError, ValueError, ValidationError) as exc:
        return None, f"{surface} response failed the production JSON/Pydantic contract: {exc}"


def _validate_review_surface(
    review: BaseModel,
    variables: Mapping[str, Any],
) -> dict[str, object]:
    if isinstance(review, AnswerReview):
        review, lesson, problem = _restore_answer_ids(review, variables)
        if problem:
            return _failure(problem)
        restored_payload = review.model_dump(mode="json")
        comparison = compare_answers(build_expected_answers(lesson), restored_payload)
        if not comparison["passed"]:
            return _failure(f"answer review does not match authored answers: {comparison}")
        return _validate_record_outcomes(review.answers, variables)
    if isinstance(review, (AttemptReview, OpenRubricReview, OpenSemanticReview)):
        return _validate_record_outcomes(review.checks, variables)
    if isinstance(review, StandaloneExerciseReview):
        return _validate_record_outcomes(review.results, variables)
    if isinstance(review, ReviewEditPackage):
        return _validate_review_edit_outcomes(review, variables)
    if isinstance(review, LessonQualityReview):
        return _validate_lesson_review_outcomes(review, variables)
    if isinstance(review, NormalizationPreservationReview):
        return _validate_preservation_outcomes(review, variables)
    return _failure(f"review outcome checks are not defined for {type(review).__name__!r}")


def _validate_review_edit_outcomes(review: ReviewEditPackage, variables: Mapping[str, Any]) -> dict[str, object]:
    problem = _check_exercise_coverage(variables, review.audited_handles)
    if problem:
        return _failure(problem)
    problem = _check_finding_handles(variables, review.findings)
    if problem:
        return _failure(problem)
    return _success("review findings and audited exercise coverage match the case expectations")


def _validate_record_outcomes(records: list[Any], variables: Mapping[str, Any]) -> dict[str, object]:
    problem = _check_exercise_coverage(variables, [item.id for item in records])
    if problem:
        return _failure(problem)
    problem = _check_record_expectations(variables, records)
    if problem:
        return _failure(problem)
    return _success("review outcomes match the case expectations and cover each exercise once")


def _validate_lesson_review_outcomes(
    review: LessonQualityReview,
    variables: Mapping[str, Any],
) -> dict[str, object]:
    problem = _check_target_issue_categories(variables, review.findings)
    if problem:
        return _failure(problem)
    return _success("target finding categories match the case expectations")


def _validate_preservation_outcomes(
    review: NormalizationPreservationReview,
    variables: Mapping[str, Any],
) -> dict[str, object]:
    problem = _check_global_issue_categories(variables, [finding.category for finding in review.findings])
    if problem:
        return _failure(problem)
    return _success("preservation finding categories match the case expectations")


def _restore_answer_ids(
    review: AnswerReview,
    variables: Mapping[str, Any],
) -> tuple[AnswerReview, dict[str, Any], str | None]:
    lesson_value = variables.get("lesson")
    if isinstance(lesson_value, str):
        try:
            lesson_value = json.loads(lesson_value)
        except (TypeError, ValueError) as exc:
            return review, {}, f"answer review case has invalid lesson JSON: {exc}"
    if not isinstance(lesson_value, Mapping):
        return review, {}, "answer review case variable `lesson` must be a JSON object"
    lesson = dict(lesson_value)
    restored = restore_answer_review_ids(review.model_dump(mode="json"), lesson)
    return AnswerReview.model_validate(restored), lesson, None


def _check_exercise_coverage(variables: Mapping[str, Any], actual: list[str]) -> str | None:
    expected = variables.get("expected_exercise_ids")
    if not isinstance(expected, list) or not all(isinstance(item, str) for item in expected):
        return "review case must declare `expected_exercise_ids` as a list of exercise ids"
    return _check_coverage(expected, actual)


def _check_record_expectations(variables: Mapping[str, Any], records: list[Any]) -> str | None:
    expected_statuses = variables.get("expected_statuses", {})
    if not isinstance(expected_statuses, Mapping):
        return "`expected_statuses` must map exercise ids to statuses"
    expected_categories = variables.get("expected_issue_categories")
    if expected_categories is not None and not isinstance(expected_categories, Mapping):
        return "per-exercise `expected_issue_categories` must map exercise ids to lists"
    records_by_id = {item.id: item for item in records}
    problem = _check_expected_statuses(expected_statuses, records_by_id)
    if problem:
        return problem
    if isinstance(expected_categories, Mapping):
        return _check_record_issue_categories(expected_categories, records_by_id)
    return None


def _check_expected_statuses(expected: Mapping[str, Any], records: Mapping[str, Any]) -> str | None:
    for exercise_id, expected_status in expected.items():
        record = records.get(exercise_id)
        actual = getattr(record, "status", None)
        if actual is None:
            actual = getattr(record, "verdict", None)
        if actual != expected_status:
            return f"exercise {exercise_id!r} status mismatch: expected {expected_status!r}, got {actual!r}"
    return None


def _check_record_issue_categories(expected: Mapping[str, Any], records: Mapping[str, Any]) -> str | None:
    for exercise_id, expected_categories in expected.items():
        if not _is_string_list(expected_categories):
            return f"issue categories for {exercise_id!r} must be a string list"
        record = records.get(exercise_id)
        actual = [issue.category for issue in getattr(record, "semantic_issues", [])]
        problem = _check_issue_categories(expected_categories, actual, exercise_id)
        if problem:
            return problem
    return None


def _check_global_issue_categories(variables: Mapping[str, Any], actual: list[str]) -> str | None:
    expected = variables.get("expected_issue_categories")
    if expected is None:
        return None
    if not _is_string_list(expected):
        return "global `expected_issue_categories` must be a string list"
    return _check_issue_categories(expected, actual, "review")


def _check_finding_handles(variables: Mapping[str, Any], findings: list[Any]) -> str | None:
    expected = variables.get("expected_finding_handles")
    if expected is None:
        return None
    if not _is_string_list(expected):
        return "`expected_finding_handles` must be a string list"
    exercise_handles = variables.get("expected_exercise_ids")
    if not _is_string_list(exercise_handles):
        return "review case must declare `expected_exercise_ids` as a list of exercise ids"
    actual = {
        handle
        for handle in exercise_handles
        if any(
            finding.artifact == "exercises.yaml"
            and finding.severity == "major"
            and _is_handle_in_location(finding.location, handle)
            for finding in findings
        )
    }
    expected_set = set(expected)
    if actual == expected_set:
        return None
    missing = [handle for handle in expected if handle not in actual]
    unexpected = [handle for handle in exercise_handles if handle in actual and handle not in expected_set]
    return f"finding handle mismatch: missing={missing!r}; unexpected={unexpected!r}"


def _check_target_issue_categories(variables: Mapping[str, Any], findings: list[Any]) -> str | None:
    accepted = variables.get("accepted_issue_categories")
    if accepted is None:
        return None
    if not _is_string_list(accepted):
        return "target `accepted_issue_categories` must be a string list"
    anchor = variables.get("expected_target_anchor")
    if not isinstance(anchor, str) or not anchor.strip():
        return "lesson review case must declare a non-empty `expected_target_anchor`"
    normalized_anchor = " ".join(anchor.split()).casefold()
    codes = [
        finding.code
        for finding in findings
        if finding.severity == "major"
        and finding.artifact == "lesson.md"
        and normalized_anchor in " ".join(finding.evidence.split()).casefold()
    ]
    if not accepted:
        return None if not codes else f"clean target {anchor!r} has major finding(s): {codes!r}"
    if any(code in accepted for code in codes):
        return None
    return f"planted target {anchor!r} has no major finding in {accepted!r}; got {codes!r}"


def _check_issue_categories(expected: list[str], actual: list[str], label: str) -> str | None:
    expected_counts = Counter(expected)
    actual_counts = Counter(actual)
    if not expected and not actual:
        return None
    if expected and not (expected_counts - actual_counts):
        return None
    return f"{label} issue categories mismatch: expected {expected!r}, got {actual!r}"


def _check_coverage(expected: list[str], actual: list[str]) -> str | None:
    expected_duplicates = sorted(item for item, count in Counter(expected).items() if count > 1)
    if expected_duplicates:
        return f"case expects duplicate exercise id(s): {expected_duplicates!r}"
    actual_duplicates = sorted(item for item, count in Counter(actual).items() if count > 1)
    if actual_duplicates:
        return f"review contains duplicate exercise id(s): {actual_duplicates!r}"
    if set(expected) == set(actual) and len(expected) == len(actual):
        return None
    missing = [item for item in expected if item not in actual]
    extra = [item for item in actual if item not in expected]
    return f"exercise coverage mismatch: missing={missing!r}; extra={extra!r}"


def _is_string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_handle_in_location(location: str, handle: str) -> bool:
    return re.search(rf"(?<![\w-]){re.escape(handle)}(?![\w-])", location, re.IGNORECASE) is not None


def _context_variables(context: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return Promptfoo test variables from its assertion context."""
    variables = context.get("vars")
    if not isinstance(variables, Mapping):
        return {}
    return variables


def _success(reason: str) -> dict[str, object]:
    """Build a passing Promptfoo grading result."""
    return {"pass": True, "score": 1.0, "reason": reason}


def _failure(reason: str) -> dict[str, object]:
    """Build a failing Promptfoo grading result."""
    return {"pass": False, "score": 0.0, "reason": reason}


__all__ = [
    "response_contract",
    "validate_normalization_negative_markers",
    "validate_review_outcomes",
]
