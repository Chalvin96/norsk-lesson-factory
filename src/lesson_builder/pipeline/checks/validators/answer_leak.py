"""Entry point: ``answer_leak_check``.

Deterministic check: flags exercise stems that contain an explicit English
meta-gloss telegraphing the answer. High-precision patterns only — this is an
advisory check (``severity="warning"``, ``advisory=True``) until calibrated for
routing.

Detection is a fixed high-precision whitelist of 5 English meta-gloss phrases
(NOT a general "English clause before a colon" heuristic): ``Meaning:``,
``As we both know:``, ``As (we )?(discussed|mentioned|noted):``,
``Remember that:``, ``Recall that:``. Precision is high; recall is limited to
these phrases by design.

Scope: scans ``choose`` stems (``payload.stem``) and ``recall_fill`` segment
spans only. Leak vectors in other exercise types (e.g. ``judge``/``find_fix``
feedback) are out of scope for now.
"""

from __future__ import annotations

from typing import Any

from lesson_builder.pipeline.checks.defect_rules import extract_stem_texts, has_answer_leak
from lesson_builder.pipeline.checks.result import CheckResult

K_ANSWER_LEAK = "answer_leak"


def answer_leak_check(data: dict[str, Any]) -> list[CheckResult]:
    """Flag exercise stems containing an English meta-gloss that telegraphs the answer."""
    results: list[CheckResult] = []
    for ex in _exercises(data):
        ex_id = str(ex.get("id", ""))
        stem_texts = extract_stem_texts(ex)
        for text in stem_texts:
            if has_answer_leak(text):
                results.append(
                    CheckResult(
                        check_id=K_ANSWER_LEAK,
                        severity="warning",
                        unit_id=ex_id,
                        advisory=True,
                        message="exercise stem contains an English meta-gloss that telegraphs the answer",
                        fix_hint="Remove the meta-gloss or rephrase the stem without revealing the answer.",
                    )
                )
                break
    return results


def _exercises(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        el
        for el in data.get("elements", [])
        if isinstance(el, dict) and el.get("element_kind") == "exercise"
    ]
