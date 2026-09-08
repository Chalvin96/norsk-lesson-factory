"""Entry points: ``parse_plan_coverage`` and ``evaluate_required_coverage``.

This module owns the small semantic-coverage contract used by lesson
generation. It checks traceability and evidence shape deterministically; it does not
decide whether Norwegian prose is idiomatic or pedagogically elegant.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import ValidationError

from lesson_builder.formats.markdown.frontmatter import parse_source_frontmatter
from lesson_builder.formats.yaml import load_unique_yaml

K_COVERAGE_SCOPE_REQUIRED = "required"
K_COVERAGE_EVIDENCE_EXPLANATION = "explanation"
K_COVERAGE_EVIDENCE_EXAMPLE = "example"
K_COVERAGE_EVIDENCE_CONTRAST_EXAMPLE = "contrast_example"
K_COVERAGE_EVIDENCE_CONTROLLED_PRACTICE = "controlled_practice"
K_COVERAGE_EVIDENCE_WHOLE_LESSON = "whole_lesson"
K_COVERAGE_EXAMPLES_BLOCK_MARKER = "::: examples"
K_COVERAGE_CONTRAST_ROLE = "contrast"

CoverageScope = Literal["required", "supporting", "guardrail", "excluded"]
CoverageEvidenceKind = Literal[
    "explanation",
    "example",
    "contrast_example",
    "controlled_practice",
    "whole_lesson",
]

K_COVERAGE_HEADING_WITH_ATTRS_RE = re.compile(
    r"(?m)^(?P<heading>#{2,6}\s+.+?)\{#(?P<section_id>[A-Za-z0-9][A-Za-z0-9_-]*)\b"
    r"(?P<attrs>[^}]*)\}\s*$"
)
K_COVERAGE_OBJECTIVES_ATTR_RE = re.compile(r"(?<![\w-])objectives=(?P<ids>[A-Za-z0-9_(),.-]+)")
K_COVERAGE_ROLE_ATTR_RE = re.compile(r"\brole=(?P<role>[A-Za-z-]+)")


class CoverageUnit(BaseModel):
    """One human-stable teaching or generation-boundary unit from a plan."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,63}$")
    objective: str | None = None
    claim: str = Field(min_length=1)
    scope: CoverageScope
    evidence: list[CoverageEvidenceKind] = Field(default_factory=list)


class CoverageContract(BaseModel):
    """Parsed plan coverage, including objective IDs for reference traceability."""

    model_config = ConfigDict(extra="forbid")

    configured: bool
    objective_ids: list[str] = Field(default_factory=list)
    units: list[CoverageUnit] = Field(default_factory=list)


def parse_plan_coverage(plan_text: str) -> CoverageContract:
    """Parse and validate plan-owned coverage units from YAML front matter."""
    metadata = _plan_front_matter(plan_text)
    objective_ids = _objective_ids(metadata.get("objectives"))
    raw_units = metadata.get("coverage")
    if raw_units is None:
        return CoverageContract(configured=False, objective_ids=objective_ids)
    if not isinstance(raw_units, list):
        raise TypeError("plan.md coverage must be a YAML list")
    try:
        units = [CoverageUnit.model_validate(value) for value in raw_units]
    except ValidationError as exc:
        raise ValueError(f"plan.md coverage contains an invalid unit: {exc}") from exc
    ids = [unit.id for unit in units]
    if not units:
        raise ValueError("plan.md coverage must contain at least one unit")
    if len(ids) != len(set(ids)):
        raise ValueError("plan.md coverage unit IDs must be unique")
    unknown_objectives = sorted(
        {unit.objective for unit in units if unit.objective is not None and unit.objective not in objective_ids}
    )
    if unknown_objectives:
        raise ValueError("plan.md coverage references unknown objective(s): " + ", ".join(unknown_objectives))
    return CoverageContract(
        configured=True,
        objective_ids=objective_ids,
        units=units,
    )


def evaluate_required_coverage(
    *,
    plan_text: str,
    lesson_md: str,
    exercises_yaml: str,
) -> dict[str, Any]:
    """Check plan-required teaching evidence deterministically.

    Every required coverage unit must find its declared evidence kinds in the
    exact normalized lesson and final exercises: an explanation section linked
    to the unit's objective, a bilingual example inside a linked section, an
    aligned contrast, or controlled practice from an exercise that references
    the objective. A plan objective without any exercise is a concrete
    contract gap. A plan without a coverage contract reports `unconfigured`:
    production plans always configure coverage, and an unconfigured package is
    not approval-ready. Advisory observations stay in the diagnostics sidecar;
    this gate records only concrete coverage failures.
    """
    contract = parse_plan_coverage(plan_text)
    findings: list[str] = []
    if not contract.configured:
        return {
            "status": "unconfigured",
            "findings": ["coverage_unconfigured: plan declares no coverage contract"],
            "units_checked": 0,
        }
    sections = _sections_with_objectives(lesson_md)
    linked_sections = {
        objective: [section for section in sections if objective in section.objectives]
        for objective in contract.objective_ids
    }
    exercise_objectives = _exercise_objectives(exercises_yaml)
    findings.extend(_coverage_findings(contract, sections, linked_sections, exercise_objectives))
    return {
        "status": "needs_human" if findings else "pass",
        "findings": findings,
        "units_checked": len(contract.units),
    }


def _coverage_findings(
    contract: CoverageContract,
    sections: list[_LinkedSection],
    linked_sections: dict[str, list[_LinkedSection]],
    exercise_objectives: dict[str, int],
) -> list[str]:
    """Collect concrete findings for configured coverage requirements."""
    findings: list[str] = []
    if not exercise_objectives:
        findings.append("no_exercises")
    findings.extend(
        f"objective_without_exercise:{objective}"
        for objective in contract.objective_ids
        if not exercise_objectives.get(objective)
    )
    for unit in contract.units:
        if unit.scope != K_COVERAGE_SCOPE_REQUIRED:
            continue
        linked = linked_sections.get(unit.objective, []) if unit.objective is not None else sections
        findings.extend(_unit_coverage_findings(unit, linked, exercise_objectives))
    return findings


def _unit_coverage_findings(
    unit: CoverageUnit,
    linked: Sequence[_LinkedSection],
    exercise_objectives: dict[str, int],
) -> list[str]:
    """Collect missing-evidence findings for one required coverage unit."""
    findings: list[str] = []
    for kind in unit.evidence:
        if kind == K_COVERAGE_EVIDENCE_WHOLE_LESSON:
            continue
        if kind == K_COVERAGE_EVIDENCE_CONTROLLED_PRACTICE:
            if unit.objective is not None and not exercise_objectives.get(unit.objective):
                findings.append(f"{unit.id}:missing_evidence:{K_COVERAGE_EVIDENCE_CONTROLLED_PRACTICE}")
            continue
        if not _section_provides_evidence(linked, kind):
            findings.append(f"{unit.id}:missing_evidence:{kind}")
    return findings


@dataclass(frozen=True)
class _LinkedSection:
    """One normalized lesson section with its objective and role attributes."""

    section_id: str
    objectives: tuple[str, ...]
    role: str | None
    body: str


def _sections_with_objectives(lesson_md: str) -> list[_LinkedSection]:
    """Index normalized sections with their linked objectives and body text."""
    matches = list(K_COVERAGE_HEADING_WITH_ATTRS_RE.finditer(lesson_md))
    sections: list[_LinkedSection] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(lesson_md)
        attrs = match.group("attrs")
        objectives_match = K_COVERAGE_OBJECTIVES_ATTR_RE.search(attrs)
        objectives = (
            tuple(value.strip() for value in objectives_match.group("ids").split(",") if value.strip())
            if objectives_match
            else ()
        )
        role_match = K_COVERAGE_ROLE_ATTR_RE.search(attrs)
        sections.append(
            _LinkedSection(
                section_id=match.group("section_id"),
                objectives=objectives,
                role=role_match.group("role") if role_match else None,
                body=lesson_md[match.end() : end],
            )
        )
    return sections


def _section_provides_evidence(sections: Sequence[_LinkedSection], kind: str) -> bool:
    """Return whether linked sections supply one declared lesson evidence kind."""
    for section in sections:
        has_body = bool(section.body.strip())
        if kind == K_COVERAGE_EVIDENCE_EXPLANATION and has_body:
            return True
        if kind == K_COVERAGE_EVIDENCE_EXAMPLE and K_COVERAGE_EXAMPLES_BLOCK_MARKER in section.body:
            return True
        if kind == K_COVERAGE_EVIDENCE_CONTRAST_EXAMPLE and (
            section.role == K_COVERAGE_CONTRAST_ROLE or "✗" in section.body
        ):
            return True
    return False


def _exercise_objectives(exercises_yaml: str) -> dict[str, int]:
    """Count final exercise references per authored objective."""
    raw = load_unique_yaml(exercises_yaml)
    counts: dict[str, int] = {}
    if not isinstance(raw, list):
        return counts
    for item in raw:
        if not isinstance(item, dict):
            continue
        objective = item.get("objective")
        if isinstance(objective, str) and objective:
            counts[objective] = counts.get(objective, 0) + 1
    return counts


def _plan_front_matter(plan_text: str) -> dict[str, object]:
    """Read the leading YAML mapping without importing lesson-package deps."""
    value, _ = parse_source_frontmatter(plan_text)
    return value


def _objective_ids(raw_objectives: object) -> list[str]:
    """Return objective IDs from the plan metadata, rejecting malformed entries."""
    if raw_objectives is None:
        return []
    if not isinstance(raw_objectives, list):
        raise TypeError("plan.md objectives must be a YAML list")
    result: list[str] = []
    for value in raw_objectives:
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise TypeError("each plan objective must be a mapping with a string id")
        result.append(value["id"])
    if len(result) != len(set(result)):
        raise ValueError("plan.md objective IDs must be unique")
    return result


__all__ = [
    "CoverageContract",
    "CoverageEvidenceKind",
    "CoverageScope",
    "CoverageUnit",
    "evaluate_required_coverage",
    "parse_plan_coverage",
]
