"""Entry point: ``find_fix_integrity_check``.

Deterministic integrity check for ``find_fix`` exercises. Two defect classes,
both ``blocker``:

- The corrected sentence embedded in ``feedback`` is identical to the sentence
  reconstructed from ``tokens`` (no real error exists). This comparison is
  case- and punctuation-SENSITIVE (whitespace-collapse only): a capitalization-
  or punctuation-only correction is a real change, so it must NOT trip this gate.
- The ``error_token_id`` does not point to the token that actually changes
  between the presented sentence and the corrected sentence.

The corrected sentence is extracted from the text after the last ``:`` in the
feedback (the canonical "instruction: corrected sentence" pattern). This is a
high-precision check, not a coverage check — it deliberately does NOT verify:
- feedback with no colon or fewer than 2 words after it (skipped);
- insertion/deletion corrections (word counts differ), where the wrong-token
  alignment step bails rather than risk a false positive;
- case-only corrections, where the case-insensitive alignment step reports no
  changed token (so error_token_id is not verified for those);
- terminal-period-only corrections, since ``_extract_corrected_sentence`` strips
  the trailing ``.`` from the feedback sentence (a missing final period cannot be
  distinguished and may read as "no change" — kept out of scope deliberately).
"""

from __future__ import annotations

from typing import Any

from lesson_builder.pipeline.checks.defect_rules import (
    check_error_token_alignment,
    collapse_ws,
    extract_corrected_sentence,
    normalize,
)
from lesson_builder.pipeline.checks.result import CheckResult

K_FIND_FIX_NO_CHANGE = "find_fix_no_change"
K_FIND_FIX_WRONG_ERROR_TOKEN = "find_fix_wrong_error_token"


def find_fix_integrity_check(data: dict[str, Any]) -> list[CheckResult]:
    """Flag find_fix exercises with no real error or a mis-pointed error_token_id."""
    results: list[CheckResult] = []
    for ex in _find_fix_exercises(data):
        ex_id = str(ex.get("id", ""))
        payload = ex.get("payload", {})
        tokens = payload.get("tokens", []) or []
        error_token_id = payload.get("error_token_id")
        feedback = str(payload.get("feedback", "") or "")

        presented = " ".join(str(t.get("text", "")) for t in tokens if isinstance(t, dict))
        corrected = extract_corrected_sentence(feedback)
        if corrected is None:
            continue

        presented_norm = normalize(presented)
        corrected_norm = normalize(corrected)

        # The no-change gate is case/punctuation SENSITIVE: a capitalization- or
        # punctuation-only fix is a real correction and must not be flagged.
        if collapse_ws(presented) == collapse_ws(corrected):
            results.append(
                CheckResult(
                    check_id=K_FIND_FIX_NO_CHANGE,
                    severity="blocker",
                    unit_id=ex_id,
                    message="feedback corrected sentence is identical to the presented token sentence",
                    fix_hint="Ensure the feedback contains a corrected sentence that differs from the presented tokens.",
                )
            )
            continue

        wrong_token = check_error_token_alignment(tokens, error_token_id, presented_norm, corrected_norm)
        if wrong_token:
            results.append(
                CheckResult(
                    check_id=K_FIND_FIX_WRONG_ERROR_TOKEN,
                    severity="blocker",
                    unit_id=ex_id,
                    message=(
                        f"error_token_id '{error_token_id}' does not correspond to the "
                        f"token that changes in the corrected sentence"
                    ),
                    fix_hint="Verify error_token_id points to the token that actually differs in the corrected sentence.",
                )
            )
    return results


def _find_fix_exercises(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        el
        for el in data.get("elements", [])
        if isinstance(el, dict)
        and el.get("element_kind") == "exercise"
        and el.get("operation") == "find_fix"
    ]
