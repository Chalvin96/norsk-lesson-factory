"""Entry point: `scan_authoring_prose` finds request prose in lesson source."""

from __future__ import annotations

import re

from lesson_builder.domain.lesson.models.source_audit import MechanicalFinding
from lesson_builder.domain.lesson.validation.learner_visibility import is_internal_request_anchor

K_AUTHORING_PROSE_LOOKBACK_LINES = 12
K_AUTHORING_PROSE_METADATA_LABEL_RE = re.compile(
    r"^(?P<label>purpose|context|learner action|success demonstrates|"
    r"success meaning|successful response demonstrates|success would demonstrate|"
    r"primary scored evidence goal|checkpoint activity)\s*:(?:\s|$)",
    re.IGNORECASE,
)
K_AUTHORING_PROSE_UNAMBIGUOUS_LABELS = frozenset(
    {
        "checkpoint activity",
        "learner action",
        "primary scored evidence goal",
        "success demonstrates",
        "success meaning",
        "success would demonstrate",
        "successful response demonstrates",
    }
)
K_AUTHORING_PROSE_NARRATIVE_RE = re.compile(
    r"\bthe learner (?:must|should|needs? to|is asked to|will)\b|"
    r"\b(?:this|the) (?:activity|checkpoint|practice) (?:belongs|comes|appears|is placed) here\b|"
    r"\b(?:primary|sole|main|supporting) scored evidence\b|"
    r"\b(?:a )?successful (?:response|review) (?:demonstrates|shows|will show)\b|"
    r"\bsuccess (?:demonstrates|would demonstrate)\b",
    re.IGNORECASE,
)


def scan_authoring_prose(
    lines: list[str],
    plain_lines: list[str],
    linguistic_div_lines: set[int],
) -> list[MechanicalFinding]:
    """Report known request-sidecar prose without deciding legacy enforcement."""
    findings: list[MechanicalFinding] = []
    for line_index, (line, plain_line) in enumerate(zip(lines, plain_lines, strict=True)):
        if is_internal_request_anchor(plain_line):
            findings.append(_build_finding("internal-request-anchor", line_index, line))
        label = _find_authoring_metadata_label(plain_line)
        if label in K_AUTHORING_PROSE_UNAMBIGUOUS_LABELS:
            findings.append(_build_finding("authoring-request-metadata", line_index, line))

    for marker_index, line in enumerate(lines):
        if not re.fullmatch(r"\s*\{\{exercise:\s*[^}\s]+\}\}\s*", line):
            continue
        rationale_index = _find_authoring_rationale_index_before(lines, marker_index, linguistic_div_lines)
        if rationale_index is not None:
            findings.append(_build_finding("authoring-rationale", rationale_index, lines[rationale_index]))
        bundle = _find_purpose_context_bundle_before(lines, plain_lines, marker_index)
        if bundle:
            evidence = " | ".join(lines[index].strip() for index in bundle)
            findings.append(_build_finding("authoring-purpose-context-bundle", marker_index, evidence))
    return findings


def _build_finding(code: str, line_index: int, evidence: str) -> MechanicalFinding:
    """Create a legacy-compatible, non-blocking source finding."""
    return MechanicalFinding(
        code=code,
        severity="minor",
        artifact="lesson.md",
        location=f"line {line_index + 1}",
        evidence=evidence.strip(),
        explanation="Request-sidecar authoring prose must not appear in learner-facing lesson Markdown.",
    )


def _find_authoring_metadata_label(plain_line: str) -> str | None:
    """Return a normalized request-sidecar label from one plain line."""
    match = K_AUTHORING_PROSE_METADATA_LABEL_RE.match(plain_line)
    return match.group("label").lower() if match is not None else None


def _find_authoring_rationale_index_before(
    lines: list[str], marker_index: int, linguistic_div_lines: set[int]
) -> int | None:
    """Find request rationale in bounded prose before a checkpoint."""
    first_index = max(0, marker_index - K_AUTHORING_PROSE_LOOKBACK_LINES)
    for line_index in range(marker_index - 1, first_index - 1, -1):
        line = lines[line_index]
        if line.startswith("## ") or "{{exercise:" in line:
            break
        if line_index not in linguistic_div_lines and K_AUTHORING_PROSE_NARRATIVE_RE.search(line):
            return line_index
    return None


def _find_purpose_context_bundle_before(lines: list[str], plain_lines: list[str], marker_index: int) -> list[int]:
    """Return nearby purpose/context label lines when both are present."""
    nearby: dict[str, int] = {}
    first_index = max(0, marker_index - K_AUTHORING_PROSE_LOOKBACK_LINES)
    for line_index in range(marker_index - 1, first_index - 1, -1):
        if lines[line_index].startswith("## ") or "{{exercise:" in lines[line_index]:
            break
        label = _find_authoring_metadata_label(plain_lines[line_index])
        if label in {"purpose", "context"}:
            nearby[label] = line_index
    if {"purpose", "context"}.issubset(nearby):
        return sorted(nearby.values())
    return []


__all__ = ["scan_authoring_prose"]
