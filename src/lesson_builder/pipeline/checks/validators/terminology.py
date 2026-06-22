"""Entry point: ``terminology_check``.

Deterministic terminology ban-list + prose-tell linter for Norwegian (Bokmal)
lessons. Flags banned phrases (hard ban, ``severity="warning"`` in step (i)) and
generated-prose tells (density-based appositive patterns + literal phrases).
Uses the shared ``extract_learner_text`` so the live gate and the audit CLI
produce identical findings for the same lesson.

Simplified from the glossary-v2 machinery: no variants, no CEFR tiers, no
systems, no drift detection. Terminology CONSISTENCY and register are moved to
the LLM naturalness reviewer (``terminology_consistency`` axis). This module
keeps ONLY deterministic hard bans + prose-tells.

Step (i): all findings are ``severity="warning"`` (audit + report only).
"""

from __future__ import annotations

import re
from typing import Any

from lesson_builder.pipeline.checks.result import CheckResult
from lesson_builder.pipeline.terminology import (
    LearnerText,
    TerminologyBans,
    extract_learner_text,
    load_terminology_bans,
)

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
)


def terminology_check(
    lesson: dict[str, Any],
    bans: TerminologyBans | None = None,
) -> list[CheckResult]:
    """Run the ban-list + prose-tell linter over a lesson.

    ``bans`` defaults to the on-disk config (cached). Returns one CheckResult
    per finding: banned phrase or prose-tell. All findings are
    ``severity="warning"`` (step (i)).
    """
    if bans is None:
        bans = load_terminology_bans()
    fragments = extract_learner_text(lesson)
    results: list[CheckResult] = []
    results.extend(_banned_phrase_findings(fragments, bans))
    results.extend(_prose_tell_findings(fragments, bans))
    return results


def terminology_audit(
    lesson: dict[str, Any],
    bans: TerminologyBans | None = None,
) -> list[dict[str, Any]]:
    """Audit variant of ``terminology_check`` returning enriched dicts.

    The audit CLI uses this so its report carries path/context_kind per finding,
    not just the CheckResult message. Same extraction + matching as the live
    gate, so the audit baseline and the live findings are directly comparable.
    """
    if bans is None:
        bans = load_terminology_bans()
    fragments = extract_learner_text(lesson)
    rows: list[dict[str, Any]] = []
    rows.extend(_banned_phrase_audit_rows(fragments, bans))
    rows.extend(_prose_tell_audit_rows(fragments, bans))
    return rows


# ---------------------------------------------------------------------------
# Banned phrases
# ---------------------------------------------------------------------------


def _banned_phrase_findings(
    fragments: list[LearnerText], bans: TerminologyBans
) -> list[CheckResult]:
    return [
        CheckResult(
            check_id=K_TERM_CHECK_BANNED,
            severity="warning",
            unit_id=row["unit_id"],
            message=row["message"],
            fix_hint=row.get("fix_hint"),
        )
        for row in _banned_phrase_audit_rows(fragments, bans)
    ]


def _banned_phrase_audit_rows(
    fragments: list[LearnerText], bans: TerminologyBans
) -> list[dict[str, Any]]:
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
                        fix_hint=f"Replace '{phrase}' with the house-style term (see docs/terminology-style-guide.md).",
                    )
                )
    return rows


# ---------------------------------------------------------------------------
# Prose tells
# ---------------------------------------------------------------------------


def _prose_tell_findings(
    fragments: list[LearnerText], bans: TerminologyBans
) -> list[CheckResult]:
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


def _prose_tell_audit_rows(
    fragments: list[LearnerText], bans: TerminologyBans
) -> list[dict[str, Any]]:
    threshold = K_PROSE_TELL_DENSITY_THRESHOLD
    rows: list[dict[str, Any]] = []

    # Appositive-density: count every occurrence so density reflects real
    # repetition. A single fragment with two ", meaning" patterns counts as 2.
    pattern_hits: list[tuple[LearnerText, str]] = []
    for fragment in fragments:
        for regex in K_PROSE_TELL_PATTERNS:
            count = len(regex.findall(fragment.text))
            for _ in range(count):
                pattern_hits.append((fragment, regex.pattern))
    if len(pattern_hits) >= threshold:
        for fragment, pattern_str in pattern_hits:
            rows.append(
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
            )

    # Literal prose-tell phrases from the config.
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
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "phrase": phrase,
        "severity": "warning",
        "revision_target": False,
        "advisory": True,
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
]
