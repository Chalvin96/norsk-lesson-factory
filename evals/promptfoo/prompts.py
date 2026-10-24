"""Entry points: Promptfoo callbacks render production prompts from prepared eval inputs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from evals.promptfoo.lesson_generation.authoring_models import ExercisePackage
from evals.promptfoo.lesson_generation.authoring_models import NormalizedPackage
from evals.promptfoo.lesson_generation.review_edit import ReviewEditPackage
from evals.promptfoo.lesson_generation.review_edit import build_review_edit_prompt
from evals.promptfoo.lesson_generation.rich_authoring import resolve_plan_terminology_context
from lesson_builder.application.operations.review_judges import AnswerReview
from lesson_builder.application.operations.review_judges import AttemptReview
from lesson_builder.application.operations.review_judges import OpenRubricReview
from lesson_builder.application.operations.review_judges import OpenSemanticReview
from lesson_builder.application.operations.review_judges import build_answer_prompt
from lesson_builder.application.operations.review_judges import build_attempt_prompt
from lesson_builder.application.operations.review_judges import build_naturalness_prompt
from lesson_builder.application.operations.review_judges import build_objective_alignment_prompt
from lesson_builder.application.operations.review_judges import build_open_rubric_prompt
from lesson_builder.application.operations.review_judges import build_open_semantic_prompt
from lesson_builder.application.operations.review_judges import build_pedagogy_prompt
from lesson_builder.application.operations.review_standalone import StandaloneExerciseReview
from lesson_builder.application.operations.review_standalone import build_standalone_review_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_exercise_author_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_lesson_review_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_normalization_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_preservation_review_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_rich_draft_prompt
from lesson_builder.clients.llm.invocation import build_structured_json_prompt
from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from lesson_builder.domain.lesson.models.quality_review import NormalizationPreservationReview
from lesson_builder.domain.lesson.models.review_checks import NaturalnessReview
from lesson_builder.domain.lesson.models.review_checks import ObjectiveAlignmentReview
from lesson_builder.domain.lesson.models.review_checks import PedagogyReview
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from lesson_builder.domain.lesson.services.quality_review import preservation_review_schema
from lesson_builder.domain.lesson.services.quality_review import quality_axes_for_kind
from lesson_builder.domain.lesson.services.quality_review import quality_review_schema
from lesson_builder.formats.markdown.frontmatter import extract_frontmatter
from lesson_builder.workspace.paths import K_WORKSPACE_ROOT

K_PROMPTFOO_LITERAL_MARKER_RE = re.compile(
    r"(?:\{\{checkpoint\b.*?\}\}|\{\{exercise:[^{}]*\}\}|\{#[^{}]*\})",
    re.DOTALL,
)
K_PROMPTFOO_REVIEW_SCHEMAS: dict[str, type[BaseModel]] = {
    "lesson_review": LessonQualityReview,
    "preservation_review": NormalizationPreservationReview,
    "review_edit": ReviewEditPackage,
    "standalone_review": StandaloneExerciseReview,
    "pedagogy": PedagogyReview,
    "objective_alignment": ObjectiveAlignmentReview,
    "answer": AnswerReview,
    "attempt": AttemptReview,
    "open_rubric": OpenRubricReview,
    "open_semantic": OpenSemanticReview,
    "naturalness": NaturalnessReview,
}
K_PROMPTFOO_RESPONSE_SCHEMAS: dict[str, type[BaseModel]] = {
    "normalization": NormalizedPackage,
    "exercise_author": ExercisePackage,
    **K_PROMPTFOO_REVIEW_SCHEMAS,
}


def create_rich_draft_prompt(context: dict[str, Any]) -> str:
    """Render the production rich-draft prompt for one eval case."""
    variables = _variables(context)
    plan = _repository_file_text(variables, "plan_path")
    return _protect_promptfoo_literals(
        build_rich_draft_prompt(
            plan,
            plan_kind=_read_plan_kind(plan),
            terminology_context=resolve_plan_terminology_context(plan, repo_root=K_WORKSPACE_ROOT),
        )
    )


def create_exercise_author_prompt(context: dict[str, Any]) -> str:
    """Render the production exercise-author prompt for one eval case."""
    variables = _variables(context)
    plan = _text(variables, "plan")
    return _protect_promptfoo_literals(
        build_exercise_author_prompt(
            plan_text=plan,
            lesson_md=_text(variables, "lesson_md"),
            exercise_requests_yaml=_text(variables, "exercise_requests_yaml"),
            terminology_context=resolve_plan_terminology_context(plan, repo_root=K_WORKSPACE_ROOT),
        )
    )


def create_normalization_prompt(context: dict[str, Any]) -> str:
    """Render the production normalization prompt for one eval case."""
    variables = _variables(context)
    return _protect_promptfoo_literals(
        build_normalization_prompt(
            _text(variables, "plan"),
            _text(variables, "draft"),
            _text(variables, "checkpoint_intents_yaml"),
            plan_kind=_read_plan_kind(_text(variables, "plan")),
        )
    )


def create_review_prompt(context: dict[str, Any]) -> str:
    """Render one production reviewer or judge prompt for a labeled eval case."""
    variables = _variables(context)
    surface = _text(variables, "surface")
    prompt = _review_prompt(surface, variables)
    if surface == "lesson_review":
        plan = _text_or_repository_file(variables, "plan", "plan_path")
        kind = _read_plan_kind(plan)
        if kind is None:
            raise ValueError("lesson review plan must declare a catalog kind")
        prompt = f"{prompt}\n\n{quality_review_schema(quality_axes_for_kind(kind))}"
    elif surface == "preservation_review":
        prompt = f"{prompt}\n\n{preservation_review_schema()}"
    else:
        prompt = build_structured_json_prompt(prompt, _require_schema(K_PROMPTFOO_REVIEW_SCHEMAS, surface))
    return _protect_promptfoo_literals(prompt)


def _review_prompt(surface: str, variables: dict[str, Any]) -> str:
    if surface == "lesson_review":
        plan = _text_or_repository_file(variables, "plan", "plan_path")
        draft = _text_or_repository_file(variables, "draft", "draft_path")
        return build_lesson_review_prompt(
            plan,
            draft,
            plan_kind=_read_plan_kind(plan),
            terminology_context=resolve_plan_terminology_context(plan, repo_root=K_WORKSPACE_ROOT),
        )
    if surface == "preservation_review":
        return build_preservation_review_prompt(
            plan_text=_text(variables, "plan"),
            draft_text=_text(variables, "draft"),
            lesson_md=_text(variables, "lesson_md"),
            exercise_requests_yaml=_text(variables, "exercise_requests_yaml"),
        )
    if surface == "review_edit":
        audit = MechanicalAudit.model_validate(_json_mapping(variables, "mechanical_audit"))
        return build_review_edit_prompt(
            lesson_md=_text(variables, "lesson_md"),
            exercises_yaml=_text(variables, "exercises_yaml"),
            mechanical_audit=audit,
            review_context=variables.get("review_context"),
        )
    if surface == "standalone_review":
        payload = _json_value(variables, "payload")
        if not isinstance(payload, list):
            raise TypeError("payload must be a JSON list")
        return build_standalone_review_prompt(payload)
    lesson = _json_mapping(variables, "lesson")
    builders = {
        "pedagogy": build_pedagogy_prompt,
        "objective_alignment": build_objective_alignment_prompt,
        "answer": build_answer_prompt,
        "attempt": build_attempt_prompt,
        "open_rubric": build_open_rubric_prompt,
        "open_semantic": build_open_semantic_prompt,
        "naturalness": build_naturalness_prompt,
    }
    try:
        return builders[surface](lesson)
    except KeyError as exc:
        raise ValueError(f"unsupported review surface {surface!r}") from exc


def _variables(context: dict[str, Any]) -> dict[str, Any]:
    variables = context.get("vars")
    if not isinstance(variables, dict):
        raise TypeError("Promptfoo test variables must be a mapping")
    return variables


def _read_plan_kind(plan_text: str) -> str | None:
    """Read the catalog kind from one eval plan."""
    metadata = yaml.safe_load(extract_frontmatter(plan_text))
    kind = metadata.get("kind") if isinstance(metadata, dict) else None
    return kind if isinstance(kind, str) else None


def _text(variables: dict[str, Any], name: str) -> str:
    value = variables.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Promptfoo case variable {name!r} must be non-empty text")
    return value


def _protect_promptfoo_literals(prompt: str) -> str:
    """Keep literal authoring markers from being parsed as Promptfoo templates."""
    protected: list[str] = []
    cursor = 0
    for match in K_PROMPTFOO_LITERAL_MARKER_RE.finditer(prompt):
        protected.append(prompt[cursor : match.start()])
        before = prompt[max(0, match.start() - len("{% raw %}")) : match.start()]
        after = prompt[match.end() : match.end() + len("{% endraw %}")]
        if before == "{% raw %}" and after == "{% endraw %}":
            protected.append(match.group(0))
        else:
            protected.append(f"{{% raw %}}{match.group(0)}{{% endraw %}}")
        cursor = match.end()
    protected.append(prompt[cursor:])
    return "".join(protected)


def _json_value(variables: dict[str, Any], name: str) -> object:
    return json.loads(_text(variables, name))


def _json_mapping(variables: dict[str, Any], name: str) -> dict[str, Any]:
    value = _json_value(variables, name)
    if not isinstance(value, dict):
        raise TypeError(f"Promptfoo case variable {name!r} must contain a JSON object")
    return value


def _repository_file_text(variables: dict[str, Any], name: str) -> str:
    """Read one repository-relative case file without escaping the repository root."""
    relative = _text(variables, name)
    if Path(relative).is_absolute():
        raise ValueError(f"Promptfoo case variable {name!r} must be a repository-relative path")
    path = (K_WORKSPACE_ROOT / relative).resolve()
    if path != K_WORKSPACE_ROOT and not path.is_relative_to(K_WORKSPACE_ROOT):
        raise ValueError(f"Promptfoo case variable {name!r} must be a repository-relative path")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"Promptfoo case variable {name!r} points to an empty file")
    return text


def _text_or_repository_file(variables: dict[str, Any], text_name: str, path_name: str) -> str:
    """Read an optional repository fixture path or return the inline case text."""
    if path_name in variables:
        return _repository_file_text(variables, path_name)
    return _text(variables, text_name)


def _require_schema(schemas: dict[str, type[BaseModel]], surface: str) -> type[BaseModel]:
    try:
        return schemas[surface]
    except KeyError as exc:
        raise ValueError(f"unsupported prompt surface {surface!r}") from exc


__all__ = [
    "create_exercise_author_prompt",
    "create_normalization_prompt",
    "create_review_prompt",
    "create_rich_draft_prompt",
    "K_PROMPTFOO_RESPONSE_SCHEMAS",
]
