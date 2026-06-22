"""Construct validated operation payloads from flat model content. Python owns every
ID, order, and flag -- so the schema's model_validators hold by construction. The LLM
never produces structure here."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from lesson_builder.gen.contracts_flat import FlatContractError, _dict_item, _require, _str, _str_list
from lesson_builder.gen.tokenize import find_word_indices, normalize_word, tokenize_sentence

_BLANK_RE = re.compile(r"_{3,}")


def _judge(flat: dict[str, Any]) -> dict[str, Any]:
    sentence = _str(flat, "sentence_no")
    is_correct = flat.get("is_correct")
    _require(isinstance(is_correct, bool), "'is_correct' must be bool")
    feedback = flat.get("feedback")
    _require(feedback is None or isinstance(feedback, str), "'feedback' must be str|null")
    _require(bool(is_correct) or (feedback is not None and feedback.strip() != ""),
             "is_correct=false requires a non-empty 'feedback'")
    return {"sentence": [{"kind": "text", "value": sentence}],
            "is_correct": is_correct, "feedback": feedback}


def _choose(flat: dict[str, Any]) -> dict[str, Any]:
    options = _str_list(flat, "options", min_len=2)
    _require(len(options) == len(set(options)), "'options' must be unique")
    idx = flat.get("correct_index")
    _require(isinstance(idx, int) and 0 <= idx < len(options), "'correct_index' out of range")
    opts = [{"option_id": f"o{i}", "text": t} for i, t in enumerate(options)]
    out: dict[str, Any] = {"options": opts, "answer_id": f"o{idx}"}
    stem = flat.get("stem")
    if isinstance(stem, str) and stem.strip():
        out["stem"] = [{"kind": "text", "value": stem}]
    return out


def _recall_fill(flat: dict[str, Any]) -> dict[str, Any]:
    sentence = _str(flat, "sentence")
    blanks = flat.get("blanks")
    if not isinstance(blanks, list) or not blanks:
        raise FlatContractError("'blanks' must be a non-empty list")
    parts = _BLANK_RE.split(sentence)
    _require(len(parts) - 1 == len(blanks),
             f"{len(parts) - 1} blank markers but {len(blanks)} blanks")
    segments: list[dict[str, Any]] = []
    for i, text in enumerate(parts):
        if text:
            segments.append({"kind": "span", "spans": [{"kind": "text", "value": text}]})
        if i < len(blanks):
            b = _dict_item(blanks[i], f"blanks[{i}]")
            opts = _str_list(b, "options", min_len=2)
            answer = _str(b, "answer")
            _require(answer in opts, "blank 'answer' must be one of its 'options'")
            segments.append({"kind": "blank", "blank_id": f"b{i}",
                             "options": opts, "answer_index": opts.index(answer)})
    return {"segments": segments}


def _match_pairs(flat: dict[str, Any]) -> dict[str, Any]:
    pairs = flat.get("pairs")
    if not isinstance(pairs, list) or len(pairs) < 2:
        raise FlatContractError("'pairs' must have >= 2 entries")
    left, right, links = [], [], []
    for i, p in enumerate(pairs):
        p = _dict_item(p, f"pairs[{i}]")
        lv, rv = _str(p, "left"), _str(p, "right")
        left.append({"left_id": f"l{i}", "text": lv})
        right.append({"right_id": f"r{i}", "text": rv})
        links.append({"left_id": f"l{i}", "right_id": f"r{i}"})
    _require(len({r["text"] for r in right}) == len(right), "'pairs' right-side texts must be unique")
    _require(len({item["text"] for item in left}) == len(left), "'pairs' left-side texts must be unique")
    return {"left": left, "right": right, "pairs": links}


def _categorize(flat: dict[str, Any]) -> dict[str, Any]:
    labels = _str_list(flat, "buckets", min_len=2)
    _require(len(labels) == len(set(labels)), "'buckets' labels must be unique")
    items = flat.get("items")
    if not isinstance(items, list) or not items:
        raise FlatContractError("'items' must be a non-empty list")
    label_to_id = {lab: f"k{i}" for i, lab in enumerate(labels)}
    buckets = [{"bucket_id": f"k{i}", "label": lab} for i, lab in enumerate(labels)]
    out_items = []
    for i, it in enumerate(items):
        it = _dict_item(it, f"items[{i}]")
        text, bucket = _str(it, "text"), _str(it, "bucket")
        _require(bucket in label_to_id, f"item bucket {bucket!r} is not one of 'buckets'")
        out_items.append({"item_id": f"i{i}", "text": text, "bucket_id": label_to_id[bucket]})
    return {"buckets": buckets, "items": out_items}


def _build(flat: dict[str, Any]) -> dict[str, Any]:
    if "items" in flat:
        words = _str_list(flat, "items", min_len=2)
    else:
        words = tokenize_sentence(_str(flat, "sentence"))
        _require(len(words) >= 2, "'sentence' must have >= 2 words")
    norm_words = [normalize_word(w) for w in words]
    fixed = {normalize_word(w) for w in (flat.get("fixed_words") or [])}
    for fw in fixed:
        _require(fw in norm_words, f"fixed word {fw!r} is not in the build items")
    tokens = [{"token_id": f"t{i}", "text": w, "fixed": norm_words[i] in fixed}
              for i, w in enumerate(words)]
    return {"tokens": tokens, "answer_order": [t["token_id"] for t in tokens]}


def _find_fix(flat: dict[str, Any]) -> dict[str, Any]:
    words = tokenize_sentence(_str(flat, "sentence_with_error"))
    error_word = _str(flat, "error_word")
    feedback = _str(flat, "feedback")
    hits = find_word_indices(words, error_word)
    _require(len(hits) == 1,
             f"'error_word' {error_word!r} must occur exactly once (found {len(hits)})")
    tokens = [{"token_id": f"t{i}", "text": w} for i, w in enumerate(words)]
    return {"tokens": tokens, "error_token_id": f"t{hits[0]}", "feedback": feedback}


_CONSTRUCTORS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "judge": _judge, "choose": _choose, "recall_fill": _recall_fill,
    "match_pairs": _match_pairs, "categorize": _categorize,
    "build": _build, "find_fix": _find_fix,
}


def construct_payload(operation: str, flat: dict[str, Any]) -> dict[str, Any]:
    ctor = _CONSTRUCTORS.get(operation)
    if ctor is None:
        raise FlatContractError(f"no constructor for operation {operation!r}")
    return ctor(flat)
