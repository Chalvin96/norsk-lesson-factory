"""Entry point: the pure defect-detection core shared by the deterministic
validators and the eval harness (``scripts/eval_lesson_defects.py``).

This module holds constants, span-tree walkers, and detection predicates ONLY —
no ``CheckResult``, no severity policy, no file IO, no printing. Severity /
advisory decisions live in the individual validators (they are gate policy, not
detection). Callers wrap these primitives however they need:

- validators produce ``CheckResult`` objects with their own severities;
- the eval harness aggregates raw counts for its metrics table.

Keeping one implementation here means a defect rule (e.g. the answer-leak phrase
list, the find_fix no-change gate) changes in exactly one place instead of
drifting between a validator and a hand-copied stdlib script.
"""

from __future__ import annotations

import re
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CANNED_OPENERS: tuple[str, ...] = (
    "correct",
    "good",
    "yes",
    "right",
    "exactly",
    "nice",
    "riktig",
    "bra",
)

ENGLISH_FUNCTION_WORDS: frozenset[str] = frozenset({"the", "a", "an", "of"})

# Norwegian function words used by ``looks_norwegian`` to detect Norwegian
# metalanguage in English-by-convention fields. Each entry must match as a
# whole word (word-boundary regex), so short entries like ``"å"`` only fire on
# the standalone preposition/infinitive marker, not as a substring of ``"går"``.
K_NORWEGIAN_FUNCTION_WORDS: frozenset[str] = frozenset(
    {
        "er",
        "ikke",
        "som",
        "på",
        "det",
        "og",
        "å",
        "må",
        "kan",
        "den",
        "en",
        "et",
        "for",
        "med",
        "til",
        "har",
        "ble",
        "blir",
    }
)

ANSWER_LEAK_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bMeaning\b\s*:"),
    re.compile(r"\bAs we both know\b\s*:", re.IGNORECASE),
    re.compile(r"\bAs (?:we )?(?:discussed|mentioned|noted)\b\s*:", re.IGNORECASE),
    re.compile(r"\bRemember that\b\s*:", re.IGNORECASE),
    re.compile(r"\bRecall that\b\s*:", re.IGNORECASE),
)


# ---------------------------------------------------------------------------
# Span-tree walkers
# ---------------------------------------------------------------------------


def find_foreign_term_spans(obj: Any) -> list[dict[str, Any]]:
    """Recursively collect every ``foreign_term`` span dict in the element tree."""
    spans: list[dict[str, Any]] = []
    if isinstance(obj, dict):
        if obj.get("kind") == "foreign_term":
            spans.append(obj)
        for value in obj.values():
            spans.extend(find_foreign_term_spans(value))
    elif isinstance(obj, list):
        for item in obj:
            spans.extend(find_foreign_term_spans(item))
    return spans


def spans_to_text(spans: Any) -> str:
    """Flatten a span list to a single space-joined string (text/foreign values)."""
    parts: list[str] = []
    if not isinstance(spans, list):
        return ""
    for span in spans:
        if isinstance(span, dict) and isinstance(span.get("value"), str):
            parts.append(span["value"])
        elif isinstance(span, str):
            parts.append(span)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Normalization — deliberately NON-NFC.
# ``schema/normalize.normalize_for_compare`` is the NFC grading-domain variant;
# the find_fix integrity gate compares presented-vs-corrected token sentences
# and must NOT fold NFC/NFD codepoints, so these stay bytewise (case/whitespace
# only). Do not unify the two.
# ---------------------------------------------------------------------------


def collapse_ws(text: str) -> str:
    """Whitespace-collapse only — case- and punctuation-preserving (non-NFC)."""
    return re.sub(r"\s+", " ", text.strip())


def normalize(text: str) -> str:
    """Lowercase + whitespace-collapse + trailing-punctuation strip (non-NFC)."""
    return re.sub(r"\s+", " ", text.strip().lower().rstrip(".!?,"))


# ---------------------------------------------------------------------------
# Canned-opener detection
# ---------------------------------------------------------------------------


def is_canned_opener(text: str) -> bool:
    """True if ``text`` starts with a canned acknowledgment opener."""
    lower = text.lower()
    for opener in CANNED_OPENERS:
        if lower.startswith(opener):
            rest = text[len(opener):]
            if not rest or rest[0] in ".!,: ":
                return True
    return False


# ---------------------------------------------------------------------------
# Norwegian-metalanguage detection
# ---------------------------------------------------------------------------

_K_NORWEGIAN_LETTERS = "æøåÆØÅ"

# Distinct Norwegian function words required to call a slot "Norwegian".
# Set to 4 (not 2) deliberately: good English metalanguage about Norwegian
# grammar naturally names 1-2 Norwegian tokens inline ("No å after kan",
# "uses gjøre en feil"), which sit at exactly 2 hits. A slot written *as*
# Norwegian prose carries 4+ function words. 4 separates the two cleanly:
# it drops those inline-citation false positives while still catching a
# whole explanation/why/gloss mistakenly authored in Norwegian.
K_MIN_NORWEGIAN_HITS = 4


def looks_norwegian(text: str) -> bool:
    """True when English-by-convention text appears to contain Norwegian metalanguage.

    After removing single-quoted inline citations (legit Norwegian references like
    ``'i går'``), returns True iff the remaining text contains at least one
    Norwegian letter (``æøå`` in either case) AND at least ``K_MIN_NORWEGIAN_HITS``
    distinct function words from ``K_NORWEGIAN_FUNCTION_WORDS`` matched as whole
    words.
    """
    stripped = re.sub(r"'[^']*'", " ", text)
    if not any(c in stripped for c in _K_NORWEGIAN_LETTERS):
        return False
    lower = stripped.lower()
    hits = sum(
        1
        for word in K_NORWEGIAN_FUNCTION_WORDS
        if re.search(r"\b" + re.escape(word) + r"\b", lower)
    )
    return hits >= K_MIN_NORWEGIAN_HITS


# ---------------------------------------------------------------------------
# Answer-leak detection
# ---------------------------------------------------------------------------


def has_answer_leak(text: str) -> bool:
    """True if ``text`` contains an English meta-gloss that telegraphs the answer."""
    return any(pattern.search(text) for pattern in ANSWER_LEAK_PATTERNS)


def extract_stem_texts(exercise: dict[str, Any]) -> list[str]:
    """Collect non-empty stem texts from choose and recall_fill exercise payloads."""
    operation = exercise.get("operation")
    payload = exercise.get("payload", {})
    texts: list[str] = []

    if operation == "choose":
        stem = payload.get("stem")
        if isinstance(stem, list):
            texts.append(spans_to_text(stem))
    elif operation == "recall_fill":
        for segment in payload.get("segments", []) or []:
            if isinstance(segment, dict) and segment.get("kind") == "span":
                texts.append(spans_to_text(segment.get("spans") or []))

    return [text for text in texts if text]


# ---------------------------------------------------------------------------
# Render-bug classification
# ---------------------------------------------------------------------------


def classify_foreign_term(span: dict[str, Any]) -> Literal["advisory", "blocker"] | None:
    """Classify a foreign_term span:

    - ``"advisory"`` — ``lang`` other than ``"no"`` (legit inline English contrast
      term, surfaced for review);
    - ``"blocker"`` — English function word mis-tagged ``lang="no"``;
    - ``None`` — no defect.
    """
    lang = str(span.get("lang", ""))
    value = str(span.get("value", ""))
    if lang != "no":
        return "advisory"
    if value.lower().strip() in ENGLISH_FUNCTION_WORDS:
        return "blocker"
    return None


# ---------------------------------------------------------------------------
# find_fix integrity
# ---------------------------------------------------------------------------


def extract_corrected_sentence(feedback: str) -> str | None:
    """Extract the corrected sentence from feedback (text after the last colon)."""
    if ":" not in feedback:
        return None
    candidate = feedback.rsplit(":", 1)[1].strip().rstrip(".")
    if len(candidate.split()) < 2:
        return None
    return candidate


def check_error_token_alignment(
    tokens: list[dict[str, Any]],
    error_token_id: str | None,
    presented_norm: str,
    corrected_norm: str,
) -> bool:
    """Return True if ``error_token_id`` does NOT align with the changed token."""
    if error_token_id is None:
        return False

    error_index = next(
        (i for i, t in enumerate(tokens) if isinstance(t, dict) and t.get("token_id") == error_token_id),
        None,
    )
    if error_index is None:
        return False

    presented_words = presented_norm.split()
    corrected_words = corrected_norm.split()
    if len(presented_words) != len(corrected_words):
        return False

    token_ranges: list[tuple[int, int]] = []
    pos = 0
    for token in tokens:
        if not isinstance(token, dict):
            continue
        word_count = len(normalize(str(token.get("text", ""))).split())
        token_ranges.append((pos, pos + word_count))
        pos += word_count

    if pos != len(presented_words):
        return False

    changed_indices: list[int] = []
    for i, (start, end) in enumerate(token_ranges):
        if presented_words[start:end] != corrected_words[start:end]:
            changed_indices.append(i)

    return bool(changed_indices) and error_index not in changed_indices


# ---------------------------------------------------------------------------
# Section coverage
# ---------------------------------------------------------------------------


def section_coverage_gaps(lesson: dict[str, Any]) -> list[str]:
    """Declared objectives not present in any section's ``objective_ids``."""
    objective_ids = [
        str(obj["id"])
        for obj in lesson.get("objectives", [])
        if isinstance(obj, dict) and obj.get("id")
    ]
    covered: set[str] = set()
    for el in lesson.get("elements", []):
        if isinstance(el, dict) and el.get("element_kind") == "section":
            for oid in el.get("objective_ids", []) or []:
                covered.add(str(oid))
    return [oid for oid in objective_ids if oid not in covered]


# ---------------------------------------------------------------------------
# Taught-surface extraction (grounding)
# ---------------------------------------------------------------------------


def content_tokens(text: str) -> set[str]:
    """Whitespace-split ``text`` into lowercased word tokens, stripping edge
    punctuation, keeping tokens of length >= 3."""
    tokens: set[str] = set()
    for raw in re.split(r"\s+", text or ""):
        word = raw.strip(".,!?;:()[]{}\"'«»–—-_/").lower()
        if len(word) >= 3:
            tokens.add(word)
    return tokens


def taught_surface(lesson: dict[str, Any]) -> set[str]:
    """All Norwegian content tokens the learner is shown in the teaching sections.

    Walks every ``element_kind == "section"`` element and collects tokens from
    paragraph/rule span text, example ``no`` (Norwegian) span lists, and table
    header/row cells. A conservative superset — a larger taught surface only
    REDUCES grounding-gap flags, never invents them.
    """
    surface: set[str] = set()
    for el in lesson.get("elements", []):
        if not isinstance(el, dict) or el.get("element_kind") != "section":
            continue
        for block in el.get("blocks", []) or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("kind")
            if kind in ("paragraph", "rule"):
                surface |= content_tokens(spans_to_text(block.get("spans") or []))
            elif kind == "example":
                surface |= content_tokens(spans_to_text(block.get("no") or []))
            elif kind == "table":
                for row in block.get("rows", []) or []:
                    if isinstance(row, list):
                        for cell in row:
                            surface |= content_tokens(spans_to_text(cell))
                for header in block.get("headers", []) or []:
                    surface |= content_tokens(spans_to_text(header))
    return surface


# ---------------------------------------------------------------------------
# Grounding-gap diagnostic (report-only, never a gate)
# ---------------------------------------------------------------------------

K_STEM_PREFIX_LEN = 4


def exercise_target_forms(exercise: dict[str, Any]) -> set[str]:
    """Norwegian content tokens the learner must PRODUCE in this exercise's answer.

    - choose: the answer option's text (skipped when it is plainly English —
      an English meaning-selection option is not a Norwegian target form);
    - recall_fill: each blank's selected answer option;
    - build: the non-fixed tokens the learner orders;
    - categorize: each item's text.
    Function words are dropped (they are never the graded target)."""
    operation = exercise.get("operation")
    payload = exercise.get("payload") or {}
    forms: set[str] = set()

    if operation == "choose":
        answer_id = payload.get("answer_id")
        for opt in payload.get("options", []) or []:
            if isinstance(opt, dict) and opt.get("option_id") == answer_id:
                text = str(opt.get("text", ""))
                if not _looks_plainly_english(text):
                    forms |= content_tokens(text)
    elif operation == "recall_fill":
        for seg in payload.get("segments", []) or []:
            if isinstance(seg, dict) and seg.get("kind") == "blank":
                options = seg.get("options") or []
                idx = seg.get("answer_index")
                if isinstance(idx, int) and 0 <= idx < len(options):
                    forms |= content_tokens(str(options[idx]))
    elif operation == "build":
        for tok in payload.get("tokens", []) or []:
            if isinstance(tok, dict) and not tok.get("fixed"):
                forms |= content_tokens(str(tok.get("text", "")))
    elif operation == "categorize":
        for item in payload.get("items", []) or []:
            if isinstance(item, dict):
                forms |= content_tokens(str(item.get("text", "")))

    return {
        f
        for f in forms
        if f not in K_NORWEGIAN_FUNCTION_WORDS and f not in ENGLISH_FUNCTION_WORDS
    }


def _looks_plainly_english(text: str) -> bool:
    """True when every word is ASCII-latin with no Norwegian letter (a whole-option
    English meaning choice, not a Norwegian target form)."""
    words = [w.strip(".,!?;:").lower() for w in text.split() if w.strip(".,!?;:")]
    if not words:
        return False
    return all(re.fullmatch(r"[a-z]+", w) for w in words) and not any(
        c in text for c in _K_NORWEGIAN_LETTERS
    )


def _stem_covered(form: str, surface: set[str]) -> bool:
    if form in surface:
        return True
    if len(form) < K_STEM_PREFIX_LEN:
        return False
    prefix = form[:K_STEM_PREFIX_LEN]
    return any(s[:K_STEM_PREFIX_LEN] == prefix for s in surface if len(s) >= K_STEM_PREFIX_LEN)


def grounding_gap_count(lesson: dict[str, Any]) -> int:
    """Report-only diagnostic: number of exercises with >= 1 target form absent
    from the lesson's taught surface (stem-prefix matched). Noisy by nature —
    grammar lessons exemplify rules with fresh vocabulary — so this is a corpus
    DRIFT signal, never a gate."""
    surface = taught_surface(lesson)
    count = 0
    for el in lesson.get("elements", []):
        if not isinstance(el, dict) or el.get("element_kind") != "exercise":
            continue
        forms = exercise_target_forms(el)
        if forms and any(not _stem_covered(f, surface) for f in forms):
            count += 1
    return count
