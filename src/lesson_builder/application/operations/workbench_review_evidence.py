"""Entry point: `populate_exercise_finding` projects actionable exercise review evidence."""

from __future__ import annotations

import hashlib
import json

from lesson_builder.application.operations.workbench_records import ReviewFinding
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_REVIEW_EXERCISE_CRITERIA
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_REVIEW_FALLBACK_QUOTE_LENGTH
from lesson_builder.formats.workbench_source import SourceBlock


def populate_exercise_finding(
    finding: ReviewFinding, blocks: tuple[SourceBlock, ...], *, from_report: bool = False
) -> ReviewFinding:
    """Refresh display fields in place, preserving ledger identity, decisions, and raw details."""
    if finding.criterion.partition(".")[0] not in K_APPLICATION_WORKBENCH_REVIEW_EXERCISE_CRITERIA:
        return finding
    if finding.cited_excerpt is None:
        finding.cited_excerpt = finding.excerpt if finding.citation_exact else ""
    placeholder = "The reviewer flagged a possible exercise issue."
    is_fallback = from_report or (finding.message or finding.explanation) == placeholder
    if is_fallback:
        issues = _read_issues(finding.details)
        _populate_issue_descriptions(finding, issues)
        is_fallback = not _has_issue_evidence(issues)
    block = next((block for block in blocks if block.key == finding.key), None)
    if block and (
        not finding.excerpt
        or " ".join(finding.excerpt.split()) == " ".join(block.source.split())
        or (is_fallback and len(finding.excerpt) > K_APPLICATION_WORKBENCH_REVIEW_FALLBACK_QUOTE_LENGTH)
    ):
        finding.excerpt = _reason_quote(finding.explanation, block.source)
    return finding


def build_exercise_issue_digest(finding: ReviewFinding) -> str:
    """Bind a grouped card's owner decision to its normalized issue set."""
    issues = _read_issues(finding.details)
    normalized = sorted(
        {
            (
                " ".join(_text(issue, "category").split()),
                " ".join(_text(issue, "evidence").split()),
            )
            for issue in issues
        }
    )
    if not any(any(fields) for fields in normalized):
        return ""
    return hashlib.sha256(json.dumps(normalized, ensure_ascii=False).encode()).hexdigest()


def _read_issues(details: str) -> list[dict[str, object]]:
    try:
        raw = json.loads(details)
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, dict):
        return []
    nested = raw.get("issues")
    if isinstance(nested, list):
        return [issue for issue in nested if isinstance(issue, dict)]
    return [raw]


def _populate_issue_descriptions(finding: ReviewFinding, issues: list[dict[str, object]]) -> None:
    messages: list[str] = []
    evidence: list[str] = []
    glosses: list[str] = []
    suggestions: list[str] = []
    for issue in issues:
        reason = _text(issue, "reviewer_reason") or _text(issue, "explanation") or _text(issue, "reason")
        category = _text(issue, "category").replace("_", " ").capitalize()
        if reason:
            messages.append(f"{category}: {reason}" if category else reason)
        evidence.append(_text(issue, "evidence"))
        glosses.append(_text(issue, "quote_english"))
        suggestions.append(_text(issue, "suggested_change") or _text(issue, "fix"))
    if messages:
        finding.message = finding.explanation = "\n".join(messages)
    finding.excerpt = _join_quotes(evidence) or finding.excerpt
    finding.quote_english = _join_quotes(glosses) or finding.quote_english
    finding.suggested_change = _join_quotes(suggestions) or finding.suggested_change


def _has_issue_evidence(issues: list[dict[str, object]]) -> bool:
    return any(_text(issue, "evidence") for issue in issues)


def _text(issue: dict[str, object], field: str) -> str:
    value = issue.get(field)
    return value.strip() if isinstance(value, str) else ""


def _join_quotes(values: list[str]) -> str:
    return "\n".join(dict.fromkeys(value for value in values if value))


def _reason_quote(reason: str, source: str) -> str:
    for opening, closing in (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")):
        pieces = reason.split(opening)
        for piece in pieces[1::2] if opening == closing else pieces[1:]:
            quote = piece.partition(closing)[0]
            if (
                quote
                and len(quote) <= K_APPLICATION_WORKBENCH_REVIEW_FALLBACK_QUOTE_LENGTH
                and quote in source
                and " ".join(quote.split()) != " ".join(source.split())
            ):
                return quote
    return ""
