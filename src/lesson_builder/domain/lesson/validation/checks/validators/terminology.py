"""Entry point: ``terminology_check``.

Deterministic terminology ban-list + prose-tell linter for Norwegian (Bokmal)
lessons. Flags banned phrases (hard ban, ``severity="blocker"``) and
generated-prose tells (density-based appositive patterns + literal phrases,
``severity="warning"``). Uses the shared ``extract_learner_text`` so the live
gate and the audit CLI produce identical findings for the same lesson.

Simplified from the glossary-v2 machinery: no variants, no CEFR tiers, no
systems, no drift detection. Terminology CONSISTENCY and register are moved to
the LLM naturalness reviewer (``terminology_consistency`` axis). This module
keeps ONLY deterministic hard bans + prose-tells.

Hard-ban severity is ``blocker`` in both the catalog-generation generation pipeline
and the final/replay compilation gate so the two are identical.
"""

from __future__ import annotations

import re
from typing import Any

from lesson_builder.domain.lesson.models.checks import CheckResult
from lesson_builder.domain.lesson.models.terminology import LearnerText
from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from lesson_builder.domain.lesson.validation.terminology import extract_learner_text

K_TERM_CHECK_BANNED = "terminology_banned"
K_TERM_CHECK_PROSE_TELL = "terminology_prose_tell"

# Prose-tell density threshold. Density (count above threshold) is the signal:
# one define-on-first-use is fine, repetition is the tell.
K_PROSE_TELL_DENSITY_THRESHOLD = 2

# Appositive-density patterns: ``"X, meaning Y"`` / ``"X, which means Y"``
# repeated above ``K_PROSE_TELL_DENSITY_THRESHOLD``. One is fine; repetition is
# the tell.
K_PROSE_TELL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r",\s*meaning\s+", re.IGNORECASE),
    re.compile(r",\s*which\s+means\s+", re.IGNORECASE),
    re.compile(r",\s*the\s+(\w+[- ])*form\b", re.IGNORECASE),
)

# "+"-notation is machine shorthand when it substitutes for real Norwegian in a
# learner answer/prompt slot ("bil + the"), but it is legitimate pattern notation
# in teaching rules/lists/tables ("subject + finite verb + object"). Flag only the
# answer-facing slots, per-occurrence (one is already a tell — no density gate).
K_PLUS_NOTATION_PATTERN: re.Pattern[str] = re.compile(r"\S\s*\+\s*\S")
K_PLUS_NOTATION_CONTEXTS: frozenset[str] = frozenset(
    {"option_text", "feedback", "exercise_prompt", "categorize_text", "judge_payload", "speak_target"}
)


def terminology_check(
    lesson: dict[str, Any],
    bans: TerminologyBans,
) -> list[CheckResult]:
    """Run the ban-list + prose-tell linter over a lesson.

    Returns one CheckResult per finding. Banned phrases are
    ``severity="blocker"`` (hard ban) so the final/replay gate treats them
    identically to the catalog-generation generation pipeline. Prose-tells
    remain ``severity="warning"`` (advisory).
    """
    fragments = extract_learner_text(lesson)
    results: list[CheckResult] = []
    results.extend(_banned_phrase_findings(fragments, bans))
    results.extend(_prose_tell_findings(fragments, bans))
    return results


def terminology_audit(
    lesson: dict[str, Any],
    bans: TerminologyBans,
) -> list[dict[str, Any]]:
    """Audit variant of ``terminology_check`` returning enriched dicts.

    The audit CLI uses this so its report carries path/context_kind per finding,
    not just the CheckResult message. Same extraction + matching as the live
    gate, so the audit baseline and the live findings are directly comparable.
    """
    fragments = extract_learner_text(lesson)
    rows: list[dict[str, Any]] = []
    rows.extend(_banned_phrase_audit_rows(fragments, bans))
    rows.extend(_prose_tell_audit_rows(fragments, bans))
    return rows


# ---------------------------------------------------------------------------
# Banned phrases
# ---------------------------------------------------------------------------


def _banned_phrase_findings(fragments: list[LearnerText], bans: TerminologyBans) -> list[CheckResult]:
    return [
        CheckResult(
            check_id=K_TERM_CHECK_BANNED,
            severity="blocker",
            unit_id=row["unit_id"],
            message=row["message"],
            fix_hint=row.get("fix_hint"),
        )
        for row in _banned_phrase_audit_rows(fragments, bans)
    ]


def _banned_phrase_audit_rows(fragments: list[LearnerText], bans: TerminologyBans) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for phrase in bans.banned_phrases:
        for fragment in fragments:
            if phrase.lower() in fragment.text.lower():
                rows.append(
                    _audit_row(
                        check_id=K_TERM_CHECK_BANNED,
                        phrase=phrase,
                        fragment=fragment,
                        message=f"banned terminology phrase '{phrase}'",
                        fix_hint=f"Replace '{phrase}' with the house-style term (see knowledge/quality/terminology.md).",
                        severity="blocker",
                    )
                )
    return rows


# ---------------------------------------------------------------------------
# Prose tells
# ---------------------------------------------------------------------------


def _prose_tell_findings(fragments: list[LearnerText], bans: TerminologyBans) -> list[CheckResult]:
    return [
        CheckResult(
            check_id=K_TERM_CHECK_PROSE_TELL,
            severity="warning",
            unit_id=row["unit_id"],
            message=row["message"],
            fix_hint=row.get("fix_hint"),
        )
        for row in _prose_tell_audit_rows(fragments, bans)
    ]


def _prose_tell_audit_rows(fragments: list[LearnerText], bans: TerminologyBans) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    rows.extend(_appositive_tell_rows(fragments))
    rows.extend(_plus_notation_rows(fragments))
    rows.extend(_literal_prose_tell_rows(fragments, bans))
    return rows


def _appositive_tell_rows(fragments: list[LearnerText]) -> list[dict[str, Any]]:
    """Find repeated appositive definition patterns."""
    threshold = K_PROSE_TELL_DENSITY_THRESHOLD
    pattern_hits: list[tuple[LearnerText, str]] = []
    for fragment in fragments:
        for regex in K_PROSE_TELL_PATTERNS:
            count = len(regex.findall(fragment.text))
            for _ in range(count):
                pattern_hits.append((fragment, regex.pattern))
    if len(pattern_hits) < threshold:
        return []
    return [
        _audit_row(
            check_id=K_TERM_CHECK_PROSE_TELL,
            phrase=pattern_str,
            fragment=fragment,
            message=(
                f"repeated appositive define-pattern '{pattern_str}' "
                f"(density {len(pattern_hits)} >= threshold {threshold})"
            ),
            fix_hint="Vary how terms are defined; one define-on-first-use is fine, repetition is a tell.",
        )
        for fragment, pattern_str in pattern_hits
    ]


def _plus_notation_rows(fragments: list[LearnerText]) -> list[dict[str, Any]]:
    """Find plus-notation shorthand in learner-facing slots."""
    rows: list[dict[str, Any]] = []
    for fragment in fragments:
        if fragment.context_kind not in K_PLUS_NOTATION_CONTEXTS:
            continue
        if K_PLUS_NOTATION_PATTERN.search(fragment.text):
            rows.append(
                _audit_row(
                    check_id=K_TERM_CHECK_PROSE_TELL,
                    phrase="+",
                    fragment=fragment,
                    message=(
                        "'+'-notation shorthand in a learner-facing answer/prompt "
                        f"slot ({fragment.context_kind}); write the actual Norwegian"
                    ),
                    fix_hint="Spell out the Norwegian form instead of using '+' shorthand.",
                )
            )
    return rows


def _literal_prose_tell_rows(fragments: list[LearnerText], bans: TerminologyBans) -> list[dict[str, Any]]:
    """Find configured literal generated-prose tells."""
    rows: list[dict[str, Any]] = []
    for phrase in bans.prose_tells:
        for fragment in fragments:
            if phrase.lower() in fragment.text.lower():
                rows.append(
                    _audit_row(
                        check_id=K_TERM_CHECK_PROSE_TELL,
                        phrase=phrase,
                        fragment=fragment,
                        message=f"generated-prose tell: literal '{phrase}'",
                        fix_hint="Reword to address the learner directly without the formulaic phrase.",
                    )
                )
    return rows


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _audit_row(
    *,
    check_id: str,
    phrase: str,
    fragment: LearnerText,
    message: str,
    fix_hint: str | None,
    severity: str = "warning",
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "phrase": phrase,
        "severity": severity,
        "revision_target": severity != "blocker",
        "advisory": severity != "blocker",
        "unit_id": fragment.unit_id,
        "path": fragment.path,
        "context_kind": fragment.context_kind,
        "message": message,
        "fix_hint": fix_hint,
    }


__all__ = [
    "terminology_check",
    "terminology_audit",
    "K_TERM_CHECK_BANNED",
    "K_TERM_CHECK_PROSE_TELL",
    "K_PROSE_TELL_PATTERNS",
    "K_PROSE_TELL_DENSITY_THRESHOLD",
    "K_PLUS_NOTATION_PATTERN",
    "K_PLUS_NOTATION_CONTEXTS",
]
