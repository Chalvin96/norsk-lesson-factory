"""Entry point: `scan_exercise_evidence` finds source-level evidence review candidates.

The scanner is deliberately advisory: lexical patterns cannot establish whether
an exercise has one semantically valid answer.
"""

from __future__ import annotations

import re
from typing import Any

from lesson_builder.domain.lesson.models.source_audit import K_SOURCE_AUDIT_EXERCISES_FILE
from lesson_builder.domain.lesson.models.source_audit import MechanicalFinding

K_EVIDENCE_SOURCE_ACTION_RE = re.compile(
    r"\b(?:correct|rewrite|translate|summarize|repair|transform)\s+"
    r"(?:the|this|following|given)\s+(?:Norwegian\s+)?"
    r"(?:sentence|text|message|utterance|dialogue|paragraph)\b",
    re.IGNORECASE,
)
K_EVIDENCE_META_CUE_RE = re.compile(
    r"^(?:Experience\b|Present (?:result|consequence|relevance)\b|"
    r"(?:[A-Z][A-Za-z'-]*\s+)?(?:was|were) supposed to\b|"
    r"(?:[A-Z][A-Za-z'-]*\s+)?(?:would|could|should) have\b|"
    r"(?:The|A|An|He|She|Leila|Jonas)\b[^)]*\b(?:up to now|continues|still lives|does not need .{1,40} now)\b)",
    re.IGNORECASE,
)
K_EVIDENCE_ENGLISH_LEAD_RE = re.compile(
    r"^(?:An? (?:arranged|agreed|assigned|unfulfilled|missed)\b|"
    r"The (?:speaker|task|host|plan|arrangement|result)\b)"
    r"[^:]{0,130}:",
    re.IGNORECASE,
)
K_EVIDENCE_MODAL_STEM_RE = re.compile(r"\b(?:skulle|kunne|ville|burde)\s+ha\s*$", re.IGNORECASE)
K_EVIDENCE_PARENTHETICAL_RE = re.compile(r"\(([^()]*)\)")
K_EVIDENCE_MIN_OPTIONS = 2


def scan_exercise_evidence(raw: dict[str, Any], handle: str) -> list[MechanicalFinding]:
    """Return high-signal review candidates from learner-visible source fields."""
    findings: list[MechanicalFinding] = []
    _scan_missing_source(raw, handle, findings)
    _scan_category_cues(raw, handle, findings)
    _scan_action_options(raw, handle, findings)
    return findings


def _scan_missing_source(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    if raw.get("op") != "write":
        return
    prompt = raw.get("prompt_md")
    if not isinstance(prompt, str):
        return
    match = K_EVIDENCE_SOURCE_ACTION_RE.search(prompt)
    if match is None:
        return
    tail = prompt[match.end() :]
    if "«" in tail or "“" in tail or re.search(r":\s*\S.{7,}", tail):
        return
    findings.append(
        MechanicalFinding(
            code="evidence-source-object-missing",
            severity="minor",
            artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
            location=f"{handle}.prompt_md",
            evidence=match.group(0),
            explanation="The task refers to a source sentence, text, or message, but the visible exercise may not supply it.",
        )
    )


def _scan_category_cues(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    if raw.get("op") == "categorize" and isinstance(raw.get("items"), list):
        _scan_categorize_cues(raw["items"], handle, findings)
    elif raw.get("op") == "choose" and isinstance(raw.get("options"), list):
        _scan_choose_cues(raw["options"], handle, findings)


def _scan_categorize_cues(items: list[object], handle: str, findings: list[MechanicalFinding]) -> None:
    for index, item in enumerate(items):
        cue = _find_category_cue(item)
        if cue is not None:
            _append_category_cue_finding(handle, f"items[{index}].text", cue, findings)


def _scan_choose_cues(options: list[object], handle: str, findings: list[MechanicalFinding]) -> None:
    cues_by_option = [_find_parenthetical_category_cues(option) for option in options]
    if not cues_by_option:
        return
    minimum_cue_count = min(len(cues) for cues in cues_by_option)
    for index, cues in enumerate(cues_by_option):
        if len(cues) > minimum_cue_count:
            _append_category_cue_finding(
                handle,
                f"options[{index}].text",
                " | ".join(cues),
                findings,
            )


def _append_category_cue_finding(
    handle: str,
    location: str,
    cue: str,
    findings: list[MechanicalFinding],
) -> None:
    findings.append(
        MechanicalFinding(
            code="evidence-category-cue",
            severity="minor",
            artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
            location=f"{handle}.{location}",
            evidence=cue.strip(),
            explanation="An English interpretation may reveal the category without interpreting the Norwegian; review its role.",
        )
    )


def _scan_action_options(raw: dict[str, Any], handle: str, findings: list[MechanicalFinding]) -> None:
    if raw.get("op") != "recall_fill" or not isinstance(raw.get("segments"), list):
        return
    preceding = ""
    for index, segment in enumerate(raw["segments"]):
        text = segment.get("text_md") if isinstance(segment, dict) else None
        if isinstance(text, str):
            preceding += text
            continue
        evidence = _find_action_option_evidence(preceding, segment)
        if evidence is None:
            continue
        findings.append(
            MechanicalFinding(
                code="evidence-action-options-review",
                severity="minor",
                artifact=K_SOURCE_AUDIT_EXERCISES_FILE,
                location=f"{handle}.segments[{index}]",
                evidence=evidence,
                explanation="The blank chooses an action after a supplied modal; check whether visible context uniquely selects one action.",
            )
        )


def _find_category_cue(item: object) -> str | None:
    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
        return None
    text = item["text"]
    lead = K_EVIDENCE_ENGLISH_LEAD_RE.search(text)
    if lead is not None:
        return lead.group(0)
    cues = _find_parenthetical_category_cues(item)
    return cues[0] if cues else None


def _find_parenthetical_category_cues(item: object) -> list[str]:
    if not isinstance(item, dict) or not isinstance(item.get("text"), str):
        return []
    values = (match.group(1) for match in K_EVIDENCE_PARENTHETICAL_RE.finditer(item["text"]))
    return [value for value in values if K_EVIDENCE_META_CUE_RE.search(value)]


def _find_action_option_evidence(preceding: str, segment: object) -> str | None:
    if not K_EVIDENCE_MODAL_STEM_RE.search(preceding) or not isinstance(segment, dict):
        return None
    options = _normalize_action_options(segment.get("options"))
    if options is None:
        return None
    option_tails = {" ".join(option.split()[1:]).casefold() for option in options}
    if len(option_tails) == 1:
        return None
    return " | ".join(options)


def _normalize_action_options(value: object) -> list[str] | None:
    if not isinstance(value, list) or len(value) < K_EVIDENCE_MIN_OPTIONS:
        return None
    options: list[str] = []
    for option in value:
        if not isinstance(option, str) or not option.strip():
            return None
        options.append(option)
    return options
