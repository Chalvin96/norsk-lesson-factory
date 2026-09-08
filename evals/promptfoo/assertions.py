"""Entry points: `response_contract`, `validate_review_edit_handle_coverage`, and `validate_review_edit_replacements`."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from lesson_builder.clients.llm.base import extract_json_object
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from lesson_builder.workflow.lesson_generation.review_edit import ReviewEditPackage

try:
    from evals.promptfoo.prompts import K_PROMPTFOO_RESPONSE_SCHEMAS
except ModuleNotFoundError:
    from prompts import K_PROMPTFOO_RESPONSE_SCHEMAS

K_PROMPTFOO_REVIEW_EDIT_SURFACE = "review_edit"


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
        payload = extract_json_object(output)
        schema.model_validate(payload)
    except (TypeError, ValueError, ValidationError) as exc:
        return _failure(f"{surface} response failed the production JSON/Pydantic contract: {exc}")
    return _success(f"{surface} response passed production JSON extraction and schema validation")


def validate_review_edit_handle_coverage(output: str, context: Mapping[str, Any]) -> dict[str, object]:
    """Require one audited handle for every prepared exercise in the case."""
    variables = _context_variables(context)
    surface = variables.get("surface")
    if surface != K_PROMPTFOO_REVIEW_EDIT_SURFACE:
        return _success("not applicable: this case is not a review/edit response")
    audit_value = variables.get("mechanical_audit")
    if not isinstance(audit_value, (str, Mapping)):
        return _failure("review/edit case variable `mechanical_audit` must be a JSON object")
    try:
        if isinstance(audit_value, str):
            audit_value = json.loads(audit_value)
        audit = MechanicalAudit.model_validate(audit_value)
    except (TypeError, ValueError, ValidationError) as exc:
        return _failure(f"could not validate review/edit mechanical audit: {exc}")
    if audit.material_findings:
        details = "; ".join(f"{finding.code}@{finding.location}" for finding in audit.material_findings)
        return _failure(f"review/edit source audit is not usable: {details}")
    if not isinstance(output, str):
        return _failure("review/edit response is not text")
    try:
        review = ReviewEditPackage.model_validate(extract_json_object(output))
    except (TypeError, ValueError, ValidationError) as exc:
        return _failure(f"review/edit response cannot be checked for audited handles: {exc}")
    expected = audit.exercise_handles
    actual = review.audited_handles
    duplicates = sorted(handle for handle, count in Counter(actual).items() if count > 1)
    if duplicates:
        return _failure(f"audited_handles contains duplicate handle(s): {duplicates!r}")
    if len(actual) != len(expected) or set(actual) != set(expected):
        missing = [handle for handle in expected if handle not in actual]
        extra = [handle for handle in actual if handle not in expected]
        return _failure(f"audited_handles mismatch: missing={missing!r}; extra={extra!r}")
    return _success(f"audited_handles covers all {len(expected)} audited exercises exactly once")


def validate_review_edit_replacements(output: str, context: Mapping[str, Any]) -> dict[str, object]:
    """Require every proposed replacement to match the prepared source bytes."""
    variables = _context_variables(context)
    surface = variables.get("surface")
    if surface != K_PROMPTFOO_REVIEW_EDIT_SURFACE:
        return _success("not applicable: this case is not a review/edit response")
    if not isinstance(output, str):
        return _failure("review/edit response is not text")
    try:
        review = ReviewEditPackage.model_validate(extract_json_object(output))
    except (TypeError, ValueError, ValidationError) as exc:
        return _failure(f"review/edit response cannot be checked for grounded edits: {exc}")
    sources = {
        "lesson.md": variables.get("lesson_md"),
        "exercises.yaml": variables.get("exercises_yaml"),
    }
    if any(not isinstance(source, str) for source in sources.values()):
        return _failure("review/edit case must provide prepared lesson_md and exercises_yaml text")
    errors: list[str] = []
    for edit in review.edits:
        source = sources[edit.artifact]
        occurrences = source.count(edit.old_text)
        if occurrences != edit.expected_occurrences:
            errors.append(
                f"{edit.artifact}:{edit.finding_code} expected {edit.expected_occurrences} "
                f"occurrence(s) but found {occurrences}"
            )
    if errors:
        return _failure("review/edit contains edits that do not match prepared source: " + "; ".join(errors))
    return _success(f"all {len(review.edits)} review/edit replacement(s) match prepared source bytes")


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


__all__ = ["response_contract", "validate_review_edit_handle_coverage", "validate_review_edit_replacements"]
