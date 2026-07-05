"""Entry point: ``language_slot_check``.

Deterministic check: flags English-by-convention text slots that appear to
contain Norwegian metalanguage (Norwegian letters + Norwegian function words
after stripping single-quoted citations). Slots scanned:

- choose / recall_fill option ``why`` strings (``payload.options[].why``),
- exercise ``explanation`` TEXT spans only (``kind == "text"``; ``foreign_term``
  spans are skipped — they are supposed to hold Norwegian),
- example ``en`` span lists (blocks with ``kind == "example"``; their ``en``
  span values are joined).

Ships ADVISORY (``severity="warning"``, ``advisory=True``): it surfaces the
pattern for authoring review without blocking the corpus at the gate. Detection
reuses ``looks_norwegian`` and the ``spans_to_text`` span walker from
``defect_rules``.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.pipeline.checks.defect_rules import looks_norwegian, spans_to_text
from lesson_builder.pipeline.checks.result import CheckResult

K_LANGUAGE_SLOT = "language_slot"

K_MESSAGE = "English-slot text appears to contain Norwegian metalanguage"

K_FIX_HINT = (
    "Write this explanation/gloss in English; keep Norwegian only in "
    "foreign_term spans or single-quoted citations."
)


def language_slot_check(data: dict[str, Any]) -> list[CheckResult]:
    """Flag English-by-convention text slots that look like Norwegian metalanguage."""
    results: list[CheckResult] = []
    for el in data.get("elements", []):
        if not isinstance(el, dict):
            continue
        kind = el.get("element_kind")
        if kind == "exercise":
            results.extend(_check_exercise(el))
        elif kind == "section":
            results.extend(_check_section_examples(el))
    return results


# ---------------------------------------------------------------------------
# Exercise slots: option "why" strings + explanation text spans
# ---------------------------------------------------------------------------


def _check_exercise(exercise: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    unit_id = str(exercise.get("id", ""))

    # choose / recall_fill option "why" strings
    payload = exercise.get("payload")
    if isinstance(payload, dict):
        for opt in payload.get("options", []) or []:
            if not isinstance(opt, dict):
                continue
            why = opt.get("why")
            if isinstance(why, str) and why and looks_norwegian(why):
                results.append(_result(unit_id))

    # explanation TEXT spans only (skip foreign_term spans)
    explanation = exercise.get("explanation")
    if isinstance(explanation, list):
        for span in explanation:
            if not isinstance(span, dict) or span.get("kind") != "text":
                continue
            value = span.get("value")
            if isinstance(value, str) and value and looks_norwegian(value):
                results.append(_result(unit_id))

    return results


# ---------------------------------------------------------------------------
# Section example "en" span lists
# ---------------------------------------------------------------------------


def _check_section_examples(section: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    unit_id = str(section.get("id", ""))
    for block in section.get("blocks", []) or []:
        if not isinstance(block, dict) or block.get("kind") != "example":
            continue
        en_spans = block.get("en")
        if isinstance(en_spans, list):
            text = spans_to_text(en_spans)
            if text and looks_norwegian(text):
                results.append(_result(unit_id))
    return results


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _result(unit_id: str) -> CheckResult:
    return CheckResult(
        check_id=K_LANGUAGE_SLOT,
        severity="warning",
        advisory=True,
        unit_id=unit_id,
        message=K_MESSAGE,
        fix_hint=K_FIX_HINT,
    )
