"""Entry point: ``exercise_structural_checks``.

Deterministic checks: policy rules on exercise/section structure that
Pydantic schema validation intentionally does not cover (e.g. judge exercises
without feedback and empty table rows). Presentation ordering is left to the
consuming learner application.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lesson_builder.domain.lesson.models.checks import CheckResult
from lesson_builder.domain.lesson.validation.checks.defect_rules import spans_to_text

K_STRUCTURAL_MIN_OPTIONS = 2
K_STRUCTURAL_MIN_OPERATION_EXERCISES = 3
K_STRUCTURAL_MIN_OPERATION_KINDS = 3


def exercise_structural_checks(
    data: dict[str, Any],
) -> list[CheckResult]:
    """Run deterministic exercise checks owned by the authoring factory."""
    results: list[CheckResult] = []
    for el in _exercises(data):
        op = el.get("operation")
        results.extend(_exercise_payload_integrity(el))
        if op == "judge":
            results.extend(_judge_feedback(el))
        elif op == "recall_fill":
            results.extend(_recall_fill_blanks(el))
    results.extend(_media_requests(data))
    results.extend(_empty_content_blocks(data))
    results.extend(_operation_depth(data))
    results.extend(_derived_build_targets(data))
    return results


def _exercise_payload_integrity(el: dict[str, Any]) -> list[CheckResult]:
    """Block malformed answer containers before an LLM answer review runs.

    Pydantic validates field types and cross-field references, but it does not
    express every authoring invariant that would make a learner task ambiguous
    (duplicate visible options, duplicate local ids, empty choices, or a
    dangling answer index).  These checks deliberately inspect only the
    learner-facing payload and its deterministic answer container.
    """
    operation = el.get("operation")
    payload = el.get("payload")
    handle = str(el.get("id", ""))
    if not isinstance(payload, dict):
        return [_payload_finding(handle, "payload is not a mapping")]
    handlers = {
        "choose": _choose_payload_findings,
        "recall_fill": _recall_fill_payload_findings,
        "match_pairs": _match_pairs_payload_findings,
        "categorize": _categorize_payload_findings,
        "build": _build_payload_findings,
        "find_fix": _find_fix_payload_findings,
        "judge": _judge_payload_findings,
        "speak": _speak_payload_findings,
        "write": _write_payload_findings,
    }
    handler = handlers.get(operation) if isinstance(operation, str) else None
    return handler(payload, handle) if handler is not None else []


def _choose_payload_findings(payload: dict[str, Any], handle: str) -> list[CheckResult]:
    """Validate a choose payload's options and answer reference."""
    options = payload.get("options")
    if not isinstance(options, list) or len(options) < K_STRUCTURAL_MIN_OPTIONS:
        return [_payload_finding(handle, "choose needs at least two options")]
    findings: list[CheckResult] = []
    _check_unique_ids(options, "option_id", handle, findings)
    _check_nonempty_texts(options, "text", handle, findings)
    _check_unique_texts(options, "text", handle, findings)
    option_ids = {item.get("option_id") for item in options if isinstance(item, dict)}
    if payload.get("answer_id") not in option_ids:
        findings.append(_payload_finding(handle, "choose answer_id does not reference an option"))
    return findings


def _recall_fill_payload_findings(payload: dict[str, Any], handle: str) -> list[CheckResult]:
    """Validate recall segments, blank options, and answer indexes."""
    findings: list[CheckResult] = []
    audio_target = payload.get("audio_target")
    if not isinstance(audio_target, str) or not audio_target.strip():
        findings.append(_payload_finding(handle, "recall_fill needs a non-empty audio_target"))
    segments = payload.get("segments")
    if not isinstance(segments, list):
        return findings + [_payload_finding(handle, "recall_fill segments are not a list")]
    blank_ids: list[str] = []
    for segment in segments:
        segment_findings, blank_id = _recall_fill_segment_findings(segment, handle)
        findings.extend(segment_findings)
        if blank_id is not None:
            blank_ids.append(blank_id)
    if len(blank_ids) != len(set(blank_ids)):
        findings.append(_payload_finding(handle, "recall_fill blank ids must be unique"))
    return findings


def _recall_fill_segment_findings(segment: object, handle: str) -> tuple[list[CheckResult], str | None]:
    """Validate one recall segment and return its typed blank id, if any."""
    if not isinstance(segment, dict):
        return [_payload_finding(handle, "recall_fill contains a non-mapping segment")], None
    if segment.get("kind") == "span":
        visible = spans_to_text(segment.get("spans"))
        if "[BLANK]" in visible:
            return [_payload_finding(handle, "recall_fill span still contains [BLANK]")], None
        return [], None
    if segment.get("kind") != "blank":
        return [_payload_finding(handle, "recall_fill has an unknown segment kind")], None
    blank_id = segment.get("blank_id") if isinstance(segment.get("blank_id"), str) else None
    options = segment.get("options")
    if not isinstance(options, list) or len(options) < K_STRUCTURAL_MIN_OPTIONS:
        return [_payload_finding(handle, "recall_fill blank needs at least two options")], blank_id
    findings = _recall_fill_option_findings(options, segment.get("answer_index"), handle)
    return findings, blank_id


def _recall_fill_option_findings(options: list[Any], answer_index: object, handle: str) -> list[CheckResult]:
    """Validate recall options and their selected answer index."""
    findings: list[CheckResult] = []
    if any(not isinstance(option, str) or not option.strip() for option in options):
        findings.append(_payload_finding(handle, "recall_fill options must be non-empty strings"))
    if len({_normalize_text(option) for option in options}) != len(options):
        findings.append(_payload_finding(handle, "recall_fill options contain duplicate visible text"))
    if not isinstance(answer_index, int) or not 0 <= answer_index < len(options):
        findings.append(_payload_finding(handle, "recall_fill answer_index is out of range"))
    return findings


def _match_pairs_payload_findings(payload: dict[str, Any], handle: str) -> list[CheckResult]:
    """Validate matching lists, pair uniqueness, and references."""
    findings: list[CheckResult] = []
    left = payload.get("left")
    right = payload.get("right")
    pairs = payload.get("pairs")
    left_items = left if isinstance(left, list) else []
    right_items = right if isinstance(right, list) else []
    pair_items = pairs if isinstance(pairs, list) else []
    if not isinstance(left, list) or not left:
        findings.append(_payload_finding(handle, "match_pairs needs left items"))
    if not isinstance(right, list) or not right:
        findings.append(_payload_finding(handle, "match_pairs needs right items"))
    if not isinstance(pairs, list) or not pairs:
        findings.append(_payload_finding(handle, "match_pairs needs at least one pair"))
    left_ids = _check_unique_ids(left_items, "left_id", handle, findings)
    right_ids = _check_unique_ids(right_items, "right_id", handle, findings)
    _check_nonempty_texts(left_items, "text", handle, findings)
    _check_nonempty_texts(right_items, "text", handle, findings)
    _check_unique_texts(left_items, "text", handle, findings)
    _check_unique_texts(right_items, "text", handle, findings)
    seen_pairs: set[tuple[Any, Any]] = set()
    for pair in pair_items:
        if not isinstance(pair, dict):
            findings.append(_payload_finding(handle, "match_pairs contains a non-mapping pair"))
            continue
        pair_key = (pair.get("left_id"), pair.get("right_id"))
        if not all(isinstance(value, str) for value in pair_key):
            findings.append(_payload_finding(handle, "match_pairs pair ids must be strings"))
            continue
        if pair_key in seen_pairs:
            findings.append(_payload_finding(handle, "match_pairs contains a duplicate pair"))
        seen_pairs.add(pair_key)
        if pair_key[0] not in left_ids or pair_key[1] not in right_ids:
            findings.append(_payload_finding(handle, "match_pairs contains a dangling pair reference"))
    return findings


def _categorize_payload_findings(payload: dict[str, Any], handle: str) -> list[CheckResult]:
    """Validate category buckets, items, and bucket references."""
    findings: list[CheckResult] = []
    buckets = payload.get("buckets")
    items = payload.get("items")
    bucket_items = buckets if isinstance(buckets, list) else []
    category_items = items if isinstance(items, list) else []
    if not isinstance(buckets, list) or len(buckets) < K_STRUCTURAL_MIN_OPTIONS:
        findings.append(_payload_finding(handle, "categorize needs at least two buckets"))
    if not isinstance(items, list) or not items:
        findings.append(_payload_finding(handle, "categorize needs at least one item"))
    bucket_ids = _check_unique_ids(bucket_items, "bucket_id", handle, findings)
    _check_nonempty_texts(bucket_items, "label", handle, findings)
    _check_unique_texts(bucket_items, "label", handle, findings)
    _check_unique_ids(category_items, "item_id", handle, findings)
    _check_nonempty_texts(category_items, "text", handle, findings)
    _check_unique_texts(category_items, "text", handle, findings)
    for item in category_items:
        if isinstance(item, dict) and item.get("bucket_id") not in bucket_ids:
            findings.append(_payload_finding(handle, "categorize item references an unknown bucket"))
    return findings


def _build_payload_findings(payload: dict[str, Any], handle: str) -> list[CheckResult]:
    """Validate build tokens and their answer permutation."""
    findings: list[CheckResult] = []
    tokens = payload.get("tokens")
    answer_order = payload.get("answer_order")
    token_items = tokens if isinstance(tokens, list) else []
    token_ids = _check_unique_ids(token_items, "token_id", handle, findings)
    _check_nonempty_texts(token_items, "text", handle, findings)
    if (
        not isinstance(answer_order, list)
        or len(answer_order) != len(token_ids)
        or any(not isinstance(token_id, str) for token_id in answer_order)
        or set(answer_order) != token_ids
    ):
        findings.append(_payload_finding(handle, "build answer_order is not a token permutation"))
    return findings


def _find_fix_payload_findings(payload: dict[str, Any], handle: str) -> list[CheckResult]:
    """Validate find_fix tokens, target, and corrective feedback."""
    findings: list[CheckResult] = []
    tokens = payload.get("tokens")
    token_items = tokens if isinstance(tokens, list) else []
    token_ids = _check_unique_ids(token_items, "token_id", handle, findings)
    _check_nonempty_texts(token_items, "text", handle, findings)
    if payload.get("error_token_id") not in token_ids:
        findings.append(_payload_finding(handle, "find_fix error_token_id is not a supplied token"))
    if not str(payload.get("feedback") or "").strip():
        findings.append(_payload_finding(handle, "find_fix feedback is empty"))
    return findings


def _judge_payload_findings(payload: dict[str, Any], handle: str) -> list[CheckResult]:
    """Validate the sentence and feedback of a judge payload."""
    findings: list[CheckResult] = []
    if not spans_to_text(payload.get("sentence")):
        findings.append(_payload_finding(handle, "judge sentence is empty"))
    if payload.get("is_correct") is False and not str(payload.get("feedback") or "").strip():
        findings.append(_payload_finding(handle, "judge false answer has no feedback"))
    return findings


def _speak_payload_findings(payload: dict[str, Any], handle: str) -> list[CheckResult]:
    """Validate the target utterance of a speak payload."""
    if not str(payload.get("target") or "").strip():
        return [_payload_finding(handle, "speak target is empty")]
    return []


def _write_payload_findings(payload: dict[str, Any], handle: str) -> list[CheckResult]:
    """Validate writing criteria and the downstream judge prompt."""
    findings: list[CheckResult] = []
    criteria = payload.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        findings.append(_payload_finding(handle, "write needs at least one criterion"))
    else:
        _check_unique_ids(criteria, "id", handle, findings)
        _check_nonempty_texts(criteria, "instruction", handle, findings)
    if not str(payload.get("judge_prompt") or "").strip():
        findings.append(_payload_finding(handle, "write judge_prompt is empty"))
    return findings


def _payload_finding(handle: str, message: str) -> CheckResult:
    """Build one blocking payload-integrity result."""
    return CheckResult(
        check_id="exercise_payload_integrity",
        severity="blocker",
        unit_id=handle,
        message=message,
        fix_hint="Repair the operation payload mechanically or regenerate only this exercise.",
    )


def _check_unique_ids(items: list[Any], key: str, handle: str, findings: list[CheckResult]) -> set[Any]:
    """Record duplicate/missing local ids and return the observed id set."""
    values = [item.get(key) for item in items if isinstance(item, dict)]
    if any(not isinstance(value, str) or not value.strip() for value in values):
        findings.append(_payload_finding(handle, f"{key} values must be non-empty strings"))
    comparable_values = [value for value in values if isinstance(value, str)]
    if len(comparable_values) != len(set(comparable_values)):
        findings.append(_payload_finding(handle, f"{key} values must be unique"))
    return set(comparable_values)


def _check_nonempty_texts(items: list[Any], key: str, handle: str, findings: list[CheckResult]) -> None:
    """Record empty visible text fields in a list of mappings."""
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(key), str) or not item[key].strip():
            findings.append(_payload_finding(handle, f"{key} must be non-empty text"))


def _check_unique_texts(items: list[Any], key: str, handle: str, findings: list[CheckResult]) -> None:
    """Record duplicate visible answer text after whitespace/case normalization."""
    values = [_normalize_text(item.get(key)) for item in items if isinstance(item, dict)]
    if len(values) != len(set(values)):
        findings.append(_payload_finding(handle, f"{key} values must be unique"))


def _normalize_text(value: object) -> str:
    """Normalize visible text for deterministic duplicate detection."""
    return " ".join(value.split()).casefold() if isinstance(value, str) else ""


def _judge_feedback(el: dict[str, Any]) -> list[CheckResult]:
    payload = el.get("payload", {})
    if payload.get("is_correct") is False and not str(payload.get("feedback") or "").strip():
        return [
            CheckResult(
                check_id="judge_feedback",
                severity="blocker",
                unit_id=str(el.get("id", "")),
                message="judge false has empty feedback",
                fix_hint="Add concise corrective feedback explaining why the sentence is wrong.",
            )
        ]
    return []


def _recall_fill_blanks(el: dict[str, Any]) -> list[CheckResult]:
    blanks = [
        seg for seg in el.get("payload", {}).get("segments", []) if isinstance(seg, dict) and seg.get("kind") == "blank"
    ]
    if not blanks:
        return [
            CheckResult(
                check_id="recall_fill_blanks",
                severity="blocker",
                unit_id=str(el.get("id", "")),
                message="recall_fill has no blanks",
                fix_hint="Add at least one blank segment with options and an answer_index.",
            )
        ]
    return []


def _media_requests(data: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for el in _exercises(data):
        if el.get("operation") == "speak":
            continue
        prompt_text = json.dumps(el.get("prompt", []), ensure_ascii=False).lower()
        if re.search(r"\b(?:audio|listen|listening)\b", prompt_text):
            results.append(
                CheckResult(
                    check_id="media_request",
                    severity="blocker",
                    unit_id=str(el.get("id", "")),
                    message="unsupported media request (audio/listen)",
                    fix_hint="Remove audio/listening requirements; this lesson format is text-only.",
                )
            )
    return results


def _empty_content_blocks(data: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for el in _sections(data):
        for block in el.get("blocks", []):
            if not isinstance(block, dict):
                continue
            kind = block.get("kind")
            if kind == "example" and (not block.get("no") or not block.get("en")):
                results.append(
                    CheckResult(
                        check_id="empty_content_block",
                        severity="blocker",
                        unit_id=str(el.get("id", "")),
                        message="example block with empty no/en",
                        fix_hint="Fill both Norwegian and English example content or remove the empty block.",
                    )
                )
            elif kind == "table":
                headers = block.get("headers", [])
                rows = block.get("rows", [])
                body_cells = [cell for row in rows for cell in row]
                if not headers or all(not cell for cell in headers):
                    results.append(
                        CheckResult(
                            check_id="empty_content_block",
                            severity="blocker",
                            unit_id=str(el.get("id", "")),
                            message="table with empty headers",
                            fix_hint="Fill table headers or remove the empty table.",
                        )
                    )
                elif not rows or all(not cell for cell in body_cells):
                    results.append(
                        CheckResult(
                            check_id="empty_content_block",
                            severity="blocker",
                            unit_id=str(el.get("id", "")),
                            message="table with empty body rows",
                            fix_hint="Fill table body rows or remove the header-only table.",
                        )
                    )
    return results


def _operation_depth(data: dict[str, Any]) -> list[CheckResult]:
    exercises = _exercises(data)
    ops = [e.get("operation") for e in exercises]
    results: list[CheckResult] = []
    if len(exercises) < K_STRUCTURAL_MIN_OPERATION_EXERCISES:
        results.append(CheckResult(check_id="operation_depth", severity="warning", message="thin_lesson"))
    if len(set(ops)) < K_STRUCTURAL_MIN_OPERATION_KINDS:
        results.append(CheckResult(check_id="operation_depth", severity="warning", message="repetitive_operation_mix"))
    return results


def _derived_build_targets(data: dict[str, Any]) -> list[CheckResult]:
    """Block build exercises that change the exact source example they cite.

    A build exercise can be authored from a lesson example through
    ``derived_from``. Comparing the reconstructed answer with that example
    catches omitted function words such as ``på`` while leaving free-standing
    build exercises unchanged. The source contract remains authoritative: an
    unresolvable reference is ignored here and is handled by the compiler's
    normal source-reference checks.
    """
    sections = {str(section.get("id")): section for section in _sections(data) if section.get("id")}
    results: list[CheckResult] = []
    for exercise in _exercises(data):
        if exercise.get("operation") != "build":
            continue
        payload = exercise.get("payload") or {}
        tokens = {
            str(token.get("token_id")): str(token.get("text", ""))
            for token in payload.get("tokens", [])
            if isinstance(token, dict) and token.get("token_id")
        }
        answer_order = payload.get("answer_order") or []
        actual = _normalize_sentence(" ".join(tokens[token_id] for token_id in answer_order if token_id in tokens))
        for source in exercise.get("derived_from", []) or []:
            if not isinstance(source, dict) or not isinstance(source.get("section_id"), str):
                continue
            section = sections.get(source["section_id"])
            block_index = source.get("block_index")
            if section is None or not isinstance(block_index, int):
                continue
            blocks = section.get("blocks") or []
            if not 0 <= block_index < len(blocks):
                continue
            block = blocks[block_index]
            if not isinstance(block, dict) or block.get("kind") != "example":
                continue
            expected = _normalize_sentence(spans_to_text(block.get("no") or []))
            if expected and actual and expected != actual:
                results.append(
                    CheckResult(
                        check_id="derived_build_target",
                        severity="blocker",
                        unit_id=str(exercise.get("id", "")),
                        message=f"build answer differs from derived Norwegian example: expected {expected!r}, got {actual!r}",
                        fix_hint="Restore every source word, including Norwegian function words, or update derived_from to the intended example.",
                    )
                )
    return results


def _normalize_sentence(value: str) -> str:
    """Normalize only spacing and punctuation for source-target comparison."""
    compact = re.sub(r"\s+", " ", value.strip().lower())
    return re.sub(r"\s+([.,!?;:])", r"\1", compact)


def _exercises(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [el for el in data.get("elements", []) if isinstance(el, dict) and el.get("element_kind") == "exercise"]


def _sections(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [el for el in data.get("elements", []) if isinstance(el, dict) and el.get("element_kind") == "section"]
