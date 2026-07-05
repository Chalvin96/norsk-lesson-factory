"""Entry point: ``exercise_structural_checks``.

Deterministic checks: policy rules on exercise/section structure that
Pydantic schema validation intentionally does not cover (e.g. judge exercises
without feedback, empty table rows, trivial builds).
"""

from __future__ import annotations

import json
from typing import Any

from lesson_builder.pipeline.checks.result import CheckResult


def exercise_structural_checks(data: dict[str, Any]) -> list[CheckResult]:
    """Policy checks that Pydantic schema validation intentionally does not cover."""
    results: list[CheckResult] = []
    for el in _exercises(data):
        op = el.get("operation")
        if op == "judge":
            results.extend(_judge_feedback(el))
        elif op == "recall_fill":
            results.extend(_recall_fill_blanks(el))
    results.extend(_media_requests(data))
    results.extend(_empty_content_blocks(data))
    results.extend(_operation_depth(data))
    results.extend(_trivial_builds(data))
    return results


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
        seg
        for seg in el.get("payload", {}).get("segments", [])
        if isinstance(seg, dict) and seg.get("kind") == "blank"
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
        prompt_text = json.dumps(el.get("prompt", []), ensure_ascii=False).lower()
        if "audio" in prompt_text or "listen" in prompt_text:
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
    if len(exercises) < 3:
        results.append(CheckResult(check_id="operation_depth", severity="warning", message="thin_lesson"))
    if len(set(ops)) < 3:
        results.append(CheckResult(check_id="operation_depth", severity="warning", message="repetitive_operation_mix"))
    return results


def _trivial_builds(data: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for el in _exercises(data):
        if el.get("operation") != "build":
            continue
        payload = el.get("payload", {})
        token_order = [t.get("token_id") for t in payload.get("tokens", [])]
        if token_order and token_order == payload.get("answer_order"):
            results.append(
                CheckResult(
                    check_id="trivial_build",
                    severity="warning",
                    unit_id=str(el.get("id", "")),
                    message="build tokens stored in answer order (trivial unless UI shuffles)",
                    fix_hint="Scramble stored token order or confirm the renderer shuffles build exercises.",
                )
            )
    return results


def _exercises(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        el
        for el in data.get("elements", [])
        if isinstance(el, dict) and el.get("element_kind") == "exercise"
    ]


def _sections(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        el
        for el in data.get("elements", [])
        if isinstance(el, dict) and el.get("element_kind") == "section"
    ]
