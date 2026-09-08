"""Not a check itself — typed terminology contracts and model-local queries."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field
from pydantic import model_validator

ConceptScope = Literal["active", "reference"]
ContextKind = Literal[
    "section_prose",
    "section_reading",
    "section_rule",
    "section_example",
    "section_list_item",
    "table_header",
    "table_cell",
    "callout",
    "exercise_prompt",
    "exercise_explanation",
    "option_text",
    "option_why",
    "judge_payload",
    "feedback",
    "match_text",
    "categorize_text",
    "build_token",
    "speak_target",
]
K_TERMINOLOGY_SCHEMA_VERSION = "1"


class ForbiddenPhrase(BaseModel):
    """One hard-banned phrase with its human-authored rationale."""

    model_config = ConfigDict(extra="forbid")

    phrase: str = Field(min_length=1)
    rationale: str = ""


class ProseTell(BaseModel):
    """One advisory literal prose-tell phrase."""

    model_config = ConfigDict(extra="forbid")

    phrase: str = Field(min_length=1)
    rationale: str = ""


class ProseTellDensity(BaseModel):
    """Density-based prose-tell configuration."""

    model_config = ConfigDict(extra="forbid")

    threshold: int = Field(default=2, ge=1)
    patterns: list[str] = Field(default_factory=list)


class LearnerText(BaseModel):
    """One extracted learner-facing text fragment with its location."""

    text: str
    path: str
    unit_id: str
    context_kind: ContextKind


class TerminologyBans(BaseModel):
    """Hard-banned phrases and literal prose-tell strings."""

    banned_phrases: list[str] = Field(default_factory=list)
    prose_tells: list[str] = Field(default_factory=list)


class GlossaryConcept(BaseModel):
    """One SKOS/TBX-style terminology concept."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    preferred_label: str = Field(min_length=1)
    norwegian_label: str = ""
    alternative_labels: list[str] = Field(default_factory=list)
    scaffold: str = ""
    forbidden_phrases: list[ForbiddenPhrase] = Field(default_factory=list)
    scope: ConceptScope = "reference"
    cefr_note: str = ""
    guidance: str = ""
    rationale: str = ""


class TerminologyRegistry(BaseModel):
    """Validated terminology registry loaded from ``glossary.yaml``."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    prose_tells: list[ProseTell] = Field(default_factory=list)
    prose_tell_density: ProseTellDensity = Field(default_factory=ProseTellDensity)
    concepts: list[GlossaryConcept] = Field(min_length=1)

    @property
    def banned_phrases(self) -> list[str]:
        """Return all hard-banned phrases across every concept."""
        return [forbidden.phrase for concept in self.concepts for forbidden in concept.forbidden_phrases]

    @property
    def prose_tell_phrases(self) -> list[str]:
        """Return all literal prose-tell phrases."""
        return [tell.phrase for tell in self.prose_tells]

    def find_concept_by_id(self, concept_id: str) -> GlossaryConcept | None:
        """Resolve one concept by its stable machine identifier."""
        return next((concept for concept in self.concepts if concept.id == concept_id), None)

    def collect_active_concepts(self) -> list[GlossaryConcept]:
        """Return concepts with ``scope == 'active'``."""
        return [concept for concept in self.concepts if concept.scope == "active"]

    def collect_reference_concepts(self) -> list[GlossaryConcept]:
        """Return concepts with ``scope == 'reference'``."""
        return [concept for concept in self.concepts if concept.scope == "reference"]

    @model_validator(mode="after")
    def _validate_integrity(self) -> TerminologyRegistry:
        if self.schema_version != K_TERMINOLOGY_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported terminology registry schema_version {self.schema_version!r}; "
                f"expected {K_TERMINOLOGY_SCHEMA_VERSION!r}"
            )
        ids = [concept.id for concept in self.concepts]
        duplicates = _duplicates(ids)
        if duplicates:
            raise ValueError(f"glossary concept ids must be unique; duplicates: {sorted(duplicates)}")
        label_owners: dict[str, str] = {}
        ambiguous: set[str] = set()
        for concept in self.concepts:
            labels = [concept.preferred_label, *concept.alternative_labels]
            if concept.norwegian_label:
                labels.append(concept.norwegian_label)
            for label in labels:
                normalized = _normalize_label(label)
                owner = label_owners.get(normalized)
                if owner is not None and owner != concept.id:
                    ambiguous.add(normalized)
                else:
                    label_owners[normalized] = concept.id
        if ambiguous:
            raise ValueError(f"glossary labels must be unambiguous across concepts; ambiguous: {sorted(ambiguous)}")
        return self


def _duplicates(items: list[str]) -> set[str]:
    """Return the set of values that appear more than once."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in items:
        if item in seen:
            duplicates.add(item)
        seen.add(item)
    return duplicates


def _normalize_label(label: str) -> str:
    """Normalize a registry label for cross-concept collision checks."""
    return " ".join(label.casefold().split())


__all__ = [
    "ConceptScope",
    "ContextKind",
    "K_TERMINOLOGY_SCHEMA_VERSION",
    "ForbiddenPhrase",
    "GlossaryConcept",
    "LearnerText",
    "ProseTell",
    "ProseTellDensity",
    "TerminologyBans",
    "TerminologyRegistry",
]
