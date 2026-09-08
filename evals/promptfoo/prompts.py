"""Entry points: Promptfoo callbacks render production prompts from prepared eval inputs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from lesson_builder.application.operations.design_dependencies import build_dependency_prompt
from lesson_builder.application.operations.design_dependencies import build_second_opinion_prompt
from lesson_builder.application.operations.review_lesson import AnswerReview
from lesson_builder.application.operations.review_lesson import AttemptReview
from lesson_builder.application.operations.review_lesson import OpenRubricReview
from lesson_builder.application.operations.review_lesson import build_answer_prompt
from lesson_builder.application.operations.review_lesson import build_attempt_prompt
from lesson_builder.application.operations.review_lesson import build_naturalness_prompt
from lesson_builder.application.operations.review_lesson import build_objective_alignment_prompt
from lesson_builder.application.operations.review_lesson import build_open_rubric_prompt
from lesson_builder.application.operations.review_lesson import build_pedagogy_prompt
from lesson_builder.application.operations.review_standalone import StandaloneExerciseReview
from lesson_builder.application.operations.review_standalone import build_standalone_review_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_exercise_author_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_exercise_repair_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_lesson_repair_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_lesson_review_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_normalization_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_preservation_repair_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_preservation_review_prompt
from lesson_builder.application.operations.rich_authoring_prompts import build_rich_draft_prompt
from lesson_builder.clients.llm.invocation import build_structured_json_prompt
from lesson_builder.domain.catalog.models import DependencyProposal
from lesson_builder.domain.catalog.models import DependencyProposalRequest
from lesson_builder.domain.catalog.models import DependencySecondOpinion
from lesson_builder.domain.catalog.models import DependencySecondOpinionRequest
from lesson_builder.domain.lesson.models.quality_review import LessonQualityReview
from lesson_builder.domain.lesson.models.quality_review import NormalizationPreservationReview
from lesson_builder.domain.lesson.models.review_checks import NaturalnessReview
from lesson_builder.domain.lesson.models.review_checks import ObjectiveAlignmentReview
from lesson_builder.domain.lesson.models.review_checks import PedagogyReview
from lesson_builder.domain.lesson.models.rich_authoring import ExercisePackage
from lesson_builder.domain.lesson.models.rich_authoring import LessonDraftEditResponse
from lesson_builder.domain.lesson.models.rich_authoring import NormalizationEditResponse
from lesson_builder.domain.lesson.models.rich_authoring import NormalizedPackage
from lesson_builder.domain.lesson.models.source_audit import MechanicalAudit
from lesson_builder.domain.lesson.services.quality_review import preservation_review_schema
from lesson_builder.domain.lesson.services.quality_review import quality_axes_for_kind
from lesson_builder.domain.lesson.services.quality_review import quality_review_schema
from lesson_builder.formats.markdown.frontmatter import extract_frontmatter
from lesson_builder.workflow.catalog_design.models import AdviceResult
from lesson_builder.workflow.catalog_design.models import CandidateBatch
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.models import EvaluationBatch
from lesson_builder.workflow.catalog_design.models import ResolutionBatch
from lesson_builder.workflow.catalog_design.nodes.discover_explorer import build_discover_explorer_prompt
from lesson_builder.workflow.catalog_design.nodes.discover_reviewer import build_discover_reviewer_prompt
from lesson_builder.workflow.catalog_design.nodes.evaluate_candidates import build_evaluate_candidates_prompt
from lesson_builder.workflow.catalog_design.nodes.request_advice import build_request_advice_prompt
from lesson_builder.workflow.catalog_design.nodes.resolve_candidates import build_resolve_candidates_prompt
from lesson_builder.workflow.lesson_generation.review_edit import ReviewEditPackage
from lesson_builder.workflow.lesson_generation.review_edit import build_review_edit_prompt
from lesson_builder.workflow.lesson_generation.rich_authoring import resolve_plan_terminology_context
from lesson_builder.workspace.paths import K_WORKSPACE_ROOT

K_PROMPTFOO_LITERAL_MARKER_RE = re.compile(
    r"(?:\{\{checkpoint\b.*?\}\}|\{\{exercise:[^{}]*\}\}|\{#[^{}]*\})",
    re.DOTALL,
)
K_PROMPTFOO_CATALOG_SCHEMAS: dict[str, type[BaseModel]] = {
    "discover_explorer": CandidateBatch,
    "discover_reviewer": CandidateBatch,
    "resolve_candidates": ResolutionBatch,
    "evaluate_candidates": EvaluationBatch,
    "request_advice": AdviceResult,
    "dependency_proposal": DependencyProposal,
    "dependency_second_opinion": DependencySecondOpinion,
}
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
    "naturalness": NaturalnessReview,
}
K_PROMPTFOO_RESPONSE_SCHEMAS: dict[str, type[BaseModel]] = {
    "normalization": NormalizedPackage,
    "lesson_repair": LessonDraftEditResponse,
    "preservation_repair": NormalizationEditResponse,
    "exercise_author": ExercisePackage,
    "exercise_repair": ExercisePackage,
    **K_PROMPTFOO_CATALOG_SCHEMAS,
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


def create_lesson_repair_prompt(context: dict[str, Any]) -> str:
    """Render the production lesson-repair prompt for one eval case."""
    variables = _variables(context)
    return _protect_promptfoo_literals(
        build_lesson_repair_prompt(
            _text(variables, "draft_prompt"),
            _text(variables, "draft"),
            LessonQualityReview.model_validate(_json_mapping(variables, "review")),
        )
    )


def create_preservation_repair_prompt(context: dict[str, Any]) -> str:
    """Render the production preservation-repair prompt for one eval case."""
    variables = _variables(context)
    return _protect_promptfoo_literals(
        build_preservation_repair_prompt(
            _text(variables, "normalization_prompt"),
            NormalizedPackage.model_validate(_json_mapping(variables, "package")),
            NormalizationPreservationReview.model_validate(_json_mapping(variables, "review")),
        )
    )


def create_exercise_repair_prompt(context: dict[str, Any]) -> str:
    """Render the production exercise-repair prompt for one eval case."""
    variables = _variables(context)
    failed_handles = _text_list(variables, "failed_handles")
    learner_visible_payload = _json_value(variables, "learner_visible_payload")
    if not isinstance(learner_visible_payload, list):
        raise TypeError("learner_visible_payload must be a JSON list")
    return _protect_promptfoo_literals(
        build_exercise_repair_prompt(
            plan_text=_text(variables, "plan"),
            lesson_md=_text(variables, "lesson_md"),
            exercise_requests_yaml=_text(variables, "exercise_requests_yaml"),
            failed_handles=failed_handles,
            verification=_json_mapping(variables, "verification"),
            learner_visible_payload=learner_visible_payload,
        )
    )


def create_review_prompt(context: dict[str, Any]) -> str:
    """Render one production reviewer or judge prompt for a labeled eval case."""
    variables = _variables(context)
    surface = _text(variables, "surface")
    prompt = _review_prompt(surface, variables)
    if surface == "lesson_review":
        kind = _read_plan_kind(_text(variables, "plan"))
        if kind is None:
            raise ValueError("lesson review plan must declare a catalog kind")
        prompt = f"{prompt}\n\n{quality_review_schema(quality_axes_for_kind(kind))}"
    elif surface == "preservation_review":
        prompt = f"{prompt}\n\n{preservation_review_schema()}"
    else:
        prompt = build_structured_json_prompt(prompt, _require_schema(K_PROMPTFOO_REVIEW_SCHEMAS, surface))
    return _protect_promptfoo_literals(prompt)


def create_catalog_prompt(context: dict[str, Any]) -> str:
    """Render one production catalog prompt for a labeled eval case."""
    variables = _variables(context)
    surface = _text(variables, "surface")
    prompt = _catalog_prompt(surface, variables)
    return _protect_promptfoo_literals(
        build_structured_json_prompt(prompt, _require_schema(K_PROMPTFOO_CATALOG_SCHEMAS, surface))
    )


def _review_prompt(surface: str, variables: dict[str, Any]) -> str:
    if surface == "lesson_review":
        plan = _text(variables, "plan")
        return build_lesson_review_prompt(plan, _text(variables, "draft"), plan_kind=_read_plan_kind(plan))
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
        "naturalness": build_naturalness_prompt,
    }
    try:
        return builders[surface](lesson)
    except KeyError as exc:
        raise ValueError(f"unsupported review surface {surface!r}") from exc


def _catalog_prompt(surface: str, variables: dict[str, Any]) -> str:
    if surface == "dependency_proposal":
        request = DependencyProposalRequest.model_validate(_json_mapping(variables, "request"))
        return build_dependency_prompt(request)
    if surface == "dependency_second_opinion":
        request = DependencySecondOpinionRequest.model_validate(_json_mapping(variables, "request"))
        return build_second_opinion_prompt(request)
    request = CatalogRequest.model_validate(_json_mapping(variables, "request"))
    if surface == "discover_explorer":
        return build_discover_explorer_prompt(
            request,
            existing_snapshot=_text(variables, "existing_snapshot"),
            quality_contract=_text(variables, "quality_contract"),
        )
    if surface == "discover_reviewer":
        return build_discover_reviewer_prompt(
            request,
            existing_snapshot=_text(variables, "existing_snapshot"),
            quality_contract=_text(variables, "quality_contract"),
        )
    if surface == "resolve_candidates":
        return build_resolve_candidates_prompt(
            request,
            variables.get("advice"),
            existing_lessons_payload=_text(variables, "existing_lessons_payload"),
            candidates_payload=_text(variables, "candidates_payload"),
        )
    if surface == "evaluate_candidates":
        return build_evaluate_candidates_prompt(
            request,
            candidates_payload=_text(variables, "candidates_payload"),
            resolutions_payload=_text(variables, "resolutions_payload"),
        )
    if surface == "request_advice":
        return build_request_advice_prompt(
            request,
            _text(variables, "reason"),
            candidates_payload=_text(variables, "candidates_payload"),
            resolutions_payload=_text(variables, "resolutions_payload"),
            evaluations_payload=_text(variables, "evaluations_payload"),
        )
    raise ValueError(f"unsupported catalog surface {surface!r}")


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


def _text_list(variables: dict[str, Any], name: str) -> list[str]:
    value = variables.get(name)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Promptfoo case variable {name!r} must be a JSON list of non-empty text") from exc
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"Promptfoo case variable {name!r} must be a list of non-empty text")
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


def _require_schema(schemas: dict[str, type[BaseModel]], surface: str) -> type[BaseModel]:
    try:
        return schemas[surface]
    except KeyError as exc:
        raise ValueError(f"unsupported prompt surface {surface!r}") from exc


__all__ = [
    "create_catalog_prompt",
    "create_exercise_author_prompt",
    "create_exercise_repair_prompt",
    "create_lesson_repair_prompt",
    "create_normalization_prompt",
    "create_preservation_repair_prompt",
    "create_review_prompt",
    "create_rich_draft_prompt",
    "K_PROMPTFOO_RESPONSE_SCHEMAS",
]
