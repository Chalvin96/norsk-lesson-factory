"""Entry point: ``nynorsk_check``.

Also exports ``nynorsk_scan(text)``, the underlying marker scan, for callers
that need to check arbitrary text outside a full lesson check.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lesson_builder.pipeline.checks.result import CheckResult

# Source ranking: Wiktionary Nynorsk (NRK subtitles) frequency list.
# Exclusion filter: Wiktionary word pages with a Norwegian Nynorsk section and
# no Norwegian Bokmål section. Stored in source-rank order.
K_NYNORSK_FREQUENT_NON_BOKMAL_MARKERS = (
    "eg",
    "ikkje",
    "ein",
    "dei",
    "eit",
    "kva",
    "berre",
    "noko",
    "vere",
    "kjem",
    "sjå",
    "gjer",
    "gjere",
    "korleis",
    "vore",
    "meir",
    "kvar",
    "mykje",
    "sjølv",
    "kvifor",
    "saman",
    "nokon",
    "gjekk",
    "såg",
    "kven",
    "tek",
    "nokre",
    "sidan",
    "desse",
    "seie",
    "att",
    "utan",
    "betre",
    "halde",
    "meiner",
    "deira",
    "held",
    "finst",
    "kome",
    "enno",
    "einaste",
    "pengar",
    "framleis",
    "ver",
    "talet",
    "éin",
    "set",
    "dagar",
    "annan",
    "synest",
    "hennar",
    "eitt",
    "høyrer",
    "verkeleg",
    "snakkar",
    "son",
    "dårleg",
    "dykkar",
    "hovudet",
    "funne",
    "kalla",
    "ventar",
    "utanfor",
    "byrja",
    "hugsar",
    "døy",
    "høyrde",
    "tenkjer",
    "utruleg",
    "eigentleg",
    "særleg",
    "millionar",
    "endå",
    "meinte",
    "handlar",
    "mogleg",
    "tenkje",
    "heldt",
    "elles",
    "tidlegare",
    "høyrt",
    "fortelje",
    "setje",
    "køyre",
    "timar",
    "sjølve",
    "åleine",
    "pengane",
    "gut",
    "ungane",
    "jobbar",
    "spurde",
    "korfor",
    "sat",
    "eigen",
    "stader",
    "byrjar",
    "auga",
    "trong",
    "følgje",
)
K_NYNORSK_MARKERS = frozenset(K_NYNORSK_FREQUENT_NON_BOKMAL_MARKERS)
K_NYNORSK_WORD_RE = re.compile(r"\b\w+\b")


def nynorsk_check(data: dict[str, Any]) -> list[CheckResult]:
    results: list[CheckResult] = []
    for el in _exercises(data):
        combined = " ".join(_learner_facing_bokmal_targets(el))
        hits = nynorsk_scan(combined)
        if hits:
            results.append(
                CheckResult(
                    check_id="nynorsk_scan",
                    severity="blocker",
                    unit_id=str(el.get("id", "")),
                    message=f"Nynorsk markers in learner-facing answer content: {hits}",
                    fix_hint="Replace Nynorsk-only forms with standard Bokmål equivalents in learner-facing answer content.",
                )
            )
    return results


def nynorsk_scan(text: str) -> list[str]:
    """Return suspicious Nynorsk markers found in text."""
    words = K_NYNORSK_WORD_RE.findall(text.lower())
    return [word for word in words if word in K_NYNORSK_MARKERS]


def _learner_facing_bokmal_targets(el: dict[str, Any]) -> list[str]:
    op = el.get("operation")
    payload = el.get("payload", {})

    if op == "judge":
        return _judge_targets(payload)

    if op == "find_fix":
        return []

    if op == "choose":
        return _choose_targets(payload)

    if op == "recall_fill":
        return _recall_fill_targets(payload)

    if op == "match_pairs":
        return _text_fields(payload.get("left", [])) + _text_fields(payload.get("right", []))

    if op == "categorize":
        return _text_fields(payload.get("items", [])) + _label_fields(payload.get("buckets", []))

    if op == "build":
        return _text_fields(payload.get("tokens", []))

    texts: list[str] = []
    for val in payload.values():
        if isinstance(val, str):
            texts.append(val)
    return texts


def _judge_targets(payload: dict[str, Any]) -> list[str]:
    if payload.get("is_correct") is not True:
        return []
    return [json.dumps(payload.get("sentence", []), ensure_ascii=False)]


def _choose_targets(payload: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    answer_id = payload.get("answer_id")
    for option in payload.get("options", []):
        if option.get("option_id") == answer_id and isinstance(option.get("text"), str):
            texts.append(option["text"])
    stem = payload.get("stem")
    if stem is not None:
        texts.append(json.dumps(stem, ensure_ascii=False))
    return texts


def _recall_fill_targets(payload: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    for seg in payload.get("segments", []):
        if seg.get("kind") == "span":
            texts.append(json.dumps(seg.get("spans", []), ensure_ascii=False))
        elif seg.get("kind") == "blank":
            options = seg.get("options", [])
            answer_index = seg.get("answer_index")
            if isinstance(answer_index, int) and 0 <= answer_index < len(options):
                texts.append(str(options[answer_index]))
    return texts


def _text_fields(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [item["text"] for item in items if isinstance(item, dict) and isinstance(item.get("text"), str)]


def _label_fields(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [item["label"] for item in items if isinstance(item, dict) and isinstance(item.get("label"), str)]


def _exercises(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        el
        for el in data.get("elements", [])
        if isinstance(el, dict) and el.get("element_kind") == "exercise"
    ]
