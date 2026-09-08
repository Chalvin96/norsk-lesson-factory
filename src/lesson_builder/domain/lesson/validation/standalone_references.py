"""Entry point: `scan_unresolved_references` finds context-dependent prompts."""

from __future__ import annotations

import re
from typing import Any

K_STANDALONE_RESTATEMENT_WINDOW = 80
K_STANDALONE_RESTATEMENT_QUOTE = "«"

# Backward-looking instructions. These phrasings instruct the learner to
# consult earlier material, so they can never be part of a standalone payload.
K_STANDALONE_BACKWARD_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("return_to", re.compile(r"(?i)\breturn\s+to\b")),
    (
        "ordinal_turn",
        re.compile(r"(?i)\b(?:first|second|third|fourth|fifth|last|final|next|previous|earlier)\s+turns?\b"),
    ),
    (
        "above_reference",
        re.compile(
            r"(?i)\b(?:examples?|items?|sentences?|turns?|lines?|lists?|tables?|dialogues?|sections?)\s+above\b"
            r"|\bas\s+above\b|\bfrom\s+above\b"
        ),
    ),
    (
        "adjacent_reference",
        re.compile(
            r"(?i)\b(?:previous|earlier|preceding)\s+"
            r"(?:exercises?|sections?|examples?|turns?|checkpoints?|dialogues?|steps?|scenes?)\b"
        ),
    ),
)

# References to lesson-local structures that the standalone exercise view does
# not carry. They are only defects when the referenced facts are not restated
# locally; a colon that introduces the restatement or quoted Norwegian material
# in the same payload counts as a local restatement.
K_STANDALONE_CONTEXT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("checkpoint_reference", re.compile(r"(?i)\b(?:from|of|in)\s+the\s+checkpoint\b|\bthe\s+checkpoint['’]s\b")),
    (
        "dialogue_reference",
        re.compile(r"(?i)\b(?:from|of|in)\s+the\s+dialogue\b|\bthe\s+dialogue['’]s\b|\bmatches?\s+the\s+dialogue\b"),
    ),
    (
        "lesson_object_reference",
        re.compile(
            r"(?i)\bthe\s+lesson['’]?s\s+(?:dialogue|example|scene|section|table|list)s?\b"
            r"|\b(?:dialogue|example|turn|scene|table|list)\s+from\s+the\s+lesson\b"
        ),
    ),
)


def scan_unresolved_references(
    prompt_fields: dict[str, str],
    *,
    restatement_text: str = "",
) -> list[dict[str, Any]]:
    """Return high-signal unresolved backward references in prompt surfaces.

    ``prompt_fields`` maps one exercise's learner-visible prompt field name to
    its text (for example ``prompt_md`` and ``stem_md``). Backward-looking
    instruction patterns always report. Context-reference patterns report only
    when the referenced facts are not restated locally: a colon shortly after
    the reference or quoted Norwegian material in ``restatement_text`` counts
    as a local restatement.
    """
    findings: list[dict[str, Any]] = []
    restated_payload = K_STANDALONE_RESTATEMENT_QUOTE in restatement_text
    for field_name, text in prompt_fields.items():
        if not isinstance(text, str) or not text:
            continue
        findings.extend(_pattern_findings(K_STANDALONE_BACKWARD_PATTERNS, field_name, text))
        findings.extend(_context_findings(field_name, text, restated_payload))
    return findings


def _pattern_findings(
    patterns: tuple[tuple[str, re.Pattern[str]], ...], field_name: str, text: str
) -> list[dict[str, Any]]:
    """Collect every match for a pattern group in declaration order."""
    return [
        _reference_finding(kind, field_name, match.group(0), text)
        for kind, pattern in patterns
        for match in pattern.finditer(text)
    ]


def _context_findings(field_name: str, text: str, restated_payload: bool) -> list[dict[str, Any]]:
    """Collect context references that have no local restatement."""
    findings: list[dict[str, Any]] = []
    for kind, pattern in K_STANDALONE_CONTEXT_PATTERNS:
        for match in pattern.finditer(text):
            if not restated_payload and not _restated_after_match(match, text):
                findings.append(_reference_finding(kind, field_name, match.group(0), text))
    return findings


def _restated_after_match(match: re.Match[str], text: str) -> bool:
    """Return whether a colon introduces the referenced content right after."""
    window = text[match.end() : match.end() + K_STANDALONE_RESTATEMENT_WINDOW]
    first_sentence_end = re.search(r"[.!?](?:\s|$)", window)
    candidate = window[: first_sentence_end.start()] if first_sentence_end else window
    return ":" in candidate


def _reference_finding(kind: str, field_name: str, matched: str, text: str) -> dict[str, Any]:
    """Format one deterministic unresolved-reference finding."""
    return {
        "kind": kind,
        "field": field_name,
        "reference": matched.strip(),
        "prompt": text.strip(),
    }


__all__ = ["scan_unresolved_references"]
