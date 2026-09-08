"""Entry points: ``parse_terminology_registry`` for YAML-boundary callers;
``render_prompt_context``
and ``render_concept_index`` for prompt builders; ``resolve_concept_ids`` and
release and CLI callers.

Pure, concept-oriented terminology registry policy. It validates the typed
registry, resolves concept identifiers and labels, computes a deterministic
registry hash/version, and renders compact prompt context for author/reviewer
prompts. Repository file loading belongs to the application loader.

The registry replaces the markdown Active Rules table as the single machine
source of truth. The markdown style guide remains rationale and usage
documentation; CLI list/audit/ban/unban read and write this YAML file.

Industry-standard framing: each concept is a SKOS-style concept with one
preferred label (``preferred_label``), zero or more alternative labels
(``alternative_labels``), and zero or more forbidden phrases
(``forbidden_phrases`` following TBX ``forbiddenTerm``). A stable ``id`` anchors
brief references and deterministic enforcement.

Not a check itself — deterministic ban enforcement lives in
``checks/validators/terminology.py`` and the brief/source boundary enforcement
lives in the lesson-generation workflow's rich authoring stages.
"""

from __future__ import annotations

import hashlib

import yaml

from lesson_builder.domain.lesson.models.terminology import K_TERMINOLOGY_SCHEMA_VERSION
from lesson_builder.domain.lesson.models.terminology import GlossaryConcept
from lesson_builder.domain.lesson.models.terminology import TerminologyRegistry

K_REGISTRY_SCHEMA_VERSION = K_TERMINOLOGY_SCHEMA_VERSION
K_REGISTRY_HASH_ALGO = "sha256"
K_REGISTRY_HASH_PREFIX = "sha256:"


# ---------------------------------------------------------------------------
# Resolution and rendering
# ---------------------------------------------------------------------------


def parse_terminology_registry(data: dict[str, object]) -> TerminologyRegistry:
    """Parse and validate a raw mapping into a TerminologyRegistry."""
    return TerminologyRegistry.model_validate(data)


def render_prompt_context(
    registry: TerminologyRegistry,
    *,
    concept_ids: list[str] | None = None,
) -> str:
    """Render compact concept context for author/reviewer prompts.

    Active concepts are always rendered. When ``concept_ids`` is provided,
    any referenced reference-only concepts are also rendered so a brief that
    names a reference concept (e.g. ``demonstrative``) gets its preferred label
    and guidance into the prompt. Each line carries the concept id, preferred
    label, Norwegian label, alternatives, scaffold, and guidance.
    """
    rendered_ids: set[str] = set()
    lines: list[str] = []

    for active_concept in registry.collect_active_concepts():
        rendered_ids.add(active_concept.id)
        lines.append(_render_concept_line(active_concept))

    for concept_id in concept_ids or []:
        if concept_id in rendered_ids:
            continue
        concept = registry.find_concept_by_id(concept_id)
        if concept is None:
            continue
        rendered_ids.add(concept.id)
        lines.append(_render_concept_line(concept))

    if not lines:
        return "- No terminology concepts were found; use standard learner terms."
    return "\n".join(lines)


def render_concept_index(registry: TerminologyRegistry) -> str:
    """Render every stable concept ID for explainer discovery.

    The concise index lets the explainer select reference-only concepts without
    injecting every concept's detailed guidance into every prompt. Detailed
    active and plan-selected context remains the job of ``render_prompt_context``.
    """
    lines = [
        "- id="
        + concept.id
        + "; learner term="
        + concept.preferred_label
        + (f" ({concept.norwegian_label})" if concept.norwegian_label else "")
        for concept in registry.concepts
    ]
    return "\n".join(lines) if lines else "- No terminology concepts were found."


def resolve_concept_ids(
    registry: TerminologyRegistry,
    concept_ids: list[str],
) -> list[GlossaryConcept]:
    """Resolve a list of concept IDs to concepts, failing on unknown IDs.

    Raises ``ValueError`` naming the first unknown ID. Duplicate IDs in the
    input are de-duplicated while preserving order.
    """
    seen: set[str] = set()
    resolved: list[GlossaryConcept] = []
    for concept_id in concept_ids:
        if concept_id in seen:
            continue
        seen.add(concept_id)
        concept = registry.find_concept_by_id(concept_id)
        if concept is None:
            raise ValueError(f"unknown terminology concept id: {concept_id!r}")
        resolved.append(concept)
    return resolved


def build_registry_version(registry: TerminologyRegistry) -> str:
    """Return the deterministic content-addressed version of a registry."""
    payload = yaml.safe_dump(
        registry.model_dump(mode="json"),
        allow_unicode=True,
        sort_keys=True,
        default_flow_style=False,
    ).encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return f"{K_REGISTRY_HASH_PREFIX}{digest}"


def _render_concept_line(concept: GlossaryConcept) -> str:
    """Render one concept as a single compact prompt line."""
    parts: list[str] = [f"{concept.id}: use {concept.preferred_label}"]
    labels: list[str] = list(concept.alternative_labels)
    if concept.norwegian_label:
        labels.append(f"Norwegian: {concept.norwegian_label}")
    if concept.scaffold:
        labels.append(f"scaffold: {concept.scaffold}")
    if labels:
        parts.append("; allowed: " + ", ".join(labels))
    if concept.guidance:
        parts.append(f"; guidance: {concept.guidance}")
    if concept.cefr_note:
        parts.append(f"; level: {concept.cefr_note}")
    return "- " + "".join(parts)


__all__ = [
    "K_REGISTRY_HASH_ALGO",
    "K_REGISTRY_HASH_PREFIX",
    "K_REGISTRY_SCHEMA_VERSION",
    "parse_terminology_registry",
    "render_concept_index",
    "render_prompt_context",
    "resolve_concept_ids",
    "build_registry_version",
]
