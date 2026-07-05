"""Entry point: ``author_sections``.

Stage 2 of cold authoring: synthesize teaching ``Section`` elements for each
objective from the requirements brief. Python assigns section ids; the author
supplies the role, objective linkage, title, and block content.

Honors the real schema literal roles (``orient``/``model``/``contrast``/
``recap``; there is **no** ``teach``) and the model/contrast cardinality rule
(exactly one ``objective_id``). Every declared objective must be covered by at
least one section (the objective-coverage invariant, R5).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from lesson_builder.gen.retry import call_with_validation
from lesson_builder.pipeline.cold_author.models import StageFailure, StageOK
from lesson_builder.pipeline.cold_author.naturalness_guide import K_NATURALNESS_GUIDE
from lesson_builder.pipeline.llm.exceptions import BackendDownException, LlmQuotaException
from lesson_builder.schema.elements import Section

_LLM_FAILURES = (BackendDownException, LlmQuotaException)

_K_SECTION_ROLES = ("orient", "model", "contrast", "recap")


class _SectionSpec(BaseModel):
    """One section spec from the author: role + objective linkage + content."""

    model_config = ConfigDict(extra="forbid")

    role: str
    objective_ids: list[str] = Field(default_factory=list)
    title: str = Field(min_length=1)
    blocks: list[dict[str, Any]] = Field(min_length=1)


class _SectionsPayload(BaseModel):
    """Structured-output schema the author must return for Stage 2."""

    model_config = ConfigDict(extra="forbid")

    sections: list[_SectionSpec] = Field(min_length=1)


class _BuiltSections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sections: list[dict[str, Any]]


def author_sections(
    objectives: list[dict[str, Any]],
    requirements: dict[str, Any],
    *,
    author_agent: Any,
) -> StageOK | StageFailure:
    """Synthesize teaching sections covering every objective."""
    prompt = _build_prompt(objectives, requirements)

    def _emit() -> dict[str, Any]:
        raw = author_agent.invoke(prompt)
        parsed = json.loads(raw)
        spec = _SectionsPayload.model_validate(parsed)
        built = [_build_section(s) for s in spec.sections]
        _assert_full_objective_coverage(built, objectives)
        return {"sections": built}

    try:
        result = call_with_validation(_emit, _BuiltSections, max_attempts=2, label="cold_sections")
    except _LLM_FAILURES as exc:
        return StageFailure("sections", f"LLM backend unavailable: {exc}")
    except (ValidationError, ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
        return StageFailure("sections", f"author did not return valid sections: {exc}")
    return StageOK("sections", result.sections)


def _build_section(spec: _SectionSpec) -> dict[str, Any]:
    if spec.role not in _K_SECTION_ROLES:
        raise ValueError(
            f"section role {spec.role!r} is not one of the allowed literals {_K_SECTION_ROLES}"
        )
    for block in spec.blocks:
        kind = block.get("kind")
        if kind != "paragraph":
            raise ValueError(
                f"section block kind {kind!r} is not allowed; cold-author sections must use "
                'ONLY paragraph blocks of shape {"kind":"paragraph","spans":[{"kind":"text",'
                '"value":"..."}]}. Convey tables/rules as prose inside paragraph spans.'
            )
    section = {
        "element_kind": "section",
        "id": f"cold_sec_{uuid.uuid4().hex[:8]}",
        "role": spec.role,
        "objective_ids": list(spec.objective_ids),
        "title": spec.title,
        "blocks": list(spec.blocks),
    }
    TypeAdapter(Section).validate_python(section)
    return section


def _assert_full_objective_coverage(
    sections: list[dict[str, Any]],
    objectives: list[dict[str, Any]],
) -> None:
    declared = {obj["id"] for obj in objectives}
    covered: set[str] = set()
    for section in sections:
        covered.update(section["objective_ids"])
    missing = sorted(declared - covered)
    if missing:
        raise ValueError(
            f"sections do not cover every objective; missing: {missing} "
            "(add a model/contrast section per uncovered objective)"
        )


def _build_prompt(objectives: list[dict[str, Any]], requirements: dict[str, Any]) -> str:
    anchors = requirements.get("required_anchor_forms") or []
    notes = requirements.get("notes") or ""
    obj_summary = json.dumps(
        [{"id": o["id"], "statement": o["statement"], "bloom_targets": o["bloom_targets"]}
         for o in objectives],
        ensure_ascii=False,
    )
    return (
        "You are authoring the teaching sections of a Norwegian (Bokmål) language lesson.\n"
        f"Objectives to teach: {obj_summary}\n"
        f"Teaching notes: {notes}\n"
        f"Required anchor forms (Norwegian phrases that MUST appear in the lesson): "
        f"{json.dumps(anchors, ensure_ascii=False)}\n\n"
        "Author 2-4 teaching sections. Rules:\n"
        "- role must be one of: orient (lesson overview, no objective linkage), "
        "model (teach exactly ONE objective by id), contrast (contrast exactly ONE "
        "objective), recap (summary, no objective linkage).\n"
        "- There is NO 'teach' role.\n"
        "- Every objective id MUST appear in at least one section's objective_ids.\n"
        "- blocks is a non-empty list of PARAGRAPH blocks ONLY. Each block MUST have "
        'exactly this shape: {"kind":"paragraph","spans":[{"kind":"text","value":"..."}]}. '
        "Do NOT use any other block kind (no table, rule, list, heading, example, callout) "
        "— they have strict nested schemas and will be rejected. Convey grammar tables and "
        "rules as prose inside paragraph spans instead.\n"
        "- Inline span values are plain text only (no markdown characters: no * ` _).\n"
        "- Embed the required anchor forms as plain text inside section spans where "
        "they naturally fit.\n\n"
        f"{K_NATURALNESS_GUIDE}\n\n"
        "Respond with ONLY a JSON object (no markdown fences, no prose):\n"
        ' {"sections": [{"role": "model", "objective_ids": ["obj_001"], '
        '"title": "...", "blocks": [{"kind": "paragraph", "spans": [{"kind": "text", '
        '"value": "..."}]}]}]}'
    )


__all__ = ["author_sections"]
