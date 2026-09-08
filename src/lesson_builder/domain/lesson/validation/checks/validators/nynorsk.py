"""Entry point: ``nynorsk_check``.

Also exports ``nynorsk_scan(text)``, the underlying marker scan, for callers
that need to check arbitrary text outside a full lesson check.
"""

from __future__ import annotations

import json
import re
from typing import Any

from lesson_builder.domain.lesson.models.checks import CheckResult

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
# These short forms are also ordinary English words.  Lesson prompts and
# bilingual answer options intentionally contain English, so treating them as
# standalone Nynorsk evidence would block valid packages (for example,
# ``Which set ...``).  Keep the source-ranked list above for provenance, but
# require stronger markers for the mechanical blocker.
K_NYNORSK_ENGLISH_COLLISIONS = frozenset({"gut", "sat", "set", "son"})
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
    return [word for word in words if word in K_NYNORSK_MARKERS and word not in K_NYNORSK_ENGLISH_COLLISIONS]


def _learner_facing_bokmal_targets(el: dict[str, Any]) -> list[str]:
    op = el.get("operation")
    payload = el.get("payload", {})
    handlers = {
        "judge": _judge_targets,
        "find_fix": _get_empty_targets,
        "choose": _choose_targets,
        "recall_fill": _recall_fill_targets,
        "match_pairs": _collect_match_pairs_targets,
        "categorize": _collect_categorize_targets,
        "build": _collect_build_targets,
        "speak": _get_speak_targets,
        "write": _get_empty_targets,
    }
    handler = handlers.get(op) if isinstance(op, str) else None
    if handler is not None:
        return handler(payload)
    return [value for value in payload.values() if isinstance(value, str)]


def _get_empty_targets(payload: dict[str, Any]) -> list[str]:
    """Return no learner-facing targets for operations without answer text."""
    return []


def _collect_match_pairs_targets(payload: dict[str, Any]) -> list[str]:
    """Collect learner-visible matching item text."""
    return _extract_text_fields(payload.get("left", [])) + _extract_text_fields(payload.get("right", []))


def _collect_categorize_targets(payload: dict[str, Any]) -> list[str]:
    """Collect learner-visible category item and bucket labels."""
    return _extract_text_fields(payload.get("items", [])) + _extract_label_fields(payload.get("buckets", []))


def _collect_build_targets(payload: dict[str, Any]) -> list[str]:
    """Collect learner-visible build token text."""
    return _extract_text_fields(payload.get("tokens", []))


def _get_speak_targets(payload: dict[str, Any]) -> list[str]:
    """Collect the target utterance of a speak exercise."""
    target = payload.get("target")
    return [target] if isinstance(target, str) else []


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


def _extract_text_fields(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    return [item["text"] for item in items if isinstance(item, dict) and isinstance(item.get("text"), str)]


def _extract_label_fields(items: object) -> list[str]:
    if not isinstance(items, list):
        return []
    return [item["label"] for item in items if isinstance(item, dict) and isinstance(item.get("label"), str)]


def _exercises(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [el for el in data.get("elements", []) if isinstance(el, dict) and el.get("element_kind") == "exercise"]
