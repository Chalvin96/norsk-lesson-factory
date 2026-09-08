"""Entry point: ``default_catalog_design_dependencies`` builds collaborators.

The graph owns orchestration and deterministic policy. These collaborators own
the two model calls: the explorer may research the web, while the catalog reviewer is explicitly told to
work from model knowledge only. Catalog-review prompt text is assembled by the
``prompt.py`` module beside each model-backed node under ``nodes/``; the shared
snapshot and payload rendering stay here. Tests replace these functions through
``CatalogDesignDependencies`` and never invoke a live backend or network.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from collections.abc import Sequence
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Any
from typing import Protocol

from pydantic import BaseModel

from lesson_builder.clients.llm.invocation import JobRunner
from lesson_builder.clients.llm.jobs import catalog_explorer
from lesson_builder.clients.llm.jobs import catalog_reviewer
from lesson_builder.domain.catalog.services.normalization import normalize_text
from lesson_builder.workflow.catalog_design.context import load_existing_lessons
from lesson_builder.workflow.catalog_design.models import AdviceResult
from lesson_builder.workflow.catalog_design.models import CandidateBatch
from lesson_builder.workflow.catalog_design.models import CandidateEvaluation
from lesson_builder.workflow.catalog_design.models import CandidateResolution
from lesson_builder.workflow.catalog_design.models import CatalogCandidate
from lesson_builder.workflow.catalog_design.models import CatalogRequest
from lesson_builder.workflow.catalog_design.models import EvaluationBatch
from lesson_builder.workflow.catalog_design.models import ExistingLesson
from lesson_builder.workflow.catalog_design.models import ResolutionBatch
from lesson_builder.workflow.catalog_design.nodes.discover_explorer import build_discover_explorer_prompt
from lesson_builder.workflow.catalog_design.nodes.discover_reviewer import build_discover_reviewer_prompt
from lesson_builder.workflow.catalog_design.nodes.evaluate_candidates import build_evaluate_candidates_prompt
from lesson_builder.workflow.catalog_design.nodes.request_advice import build_request_advice_prompt
from lesson_builder.workflow.catalog_design.nodes.resolve_candidates import build_resolve_candidates_prompt


class DiscoveryService(Protocol):
    """One independent candidate-discovery branch."""

    def __call__(
        self,
        request: CatalogRequest,
        existing_lessons: Sequence[ExistingLesson] = (),
    ) -> CandidateBatch: ...


class ResolutionService(Protocol):
    """Reconcile candidate relationships and canonical labels."""

    def __call__(
        self,
        request: CatalogRequest,
        candidates: list[CatalogCandidate],
        existing_lessons: list[ExistingLesson],
        advice: str | None,
    ) -> ResolutionBatch: ...


class EvaluationService(Protocol):
    """Score the proposed canonical lesson owners."""

    def __call__(
        self,
        request: CatalogRequest,
        candidates: list[CatalogCandidate],
        resolutions: list[CandidateResolution],
    ) -> EvaluationBatch: ...


class AdviceService(Protocol):
    """Diagnose architectural or taxonomy stagnation."""

    def __call__(
        self,
        request: CatalogRequest,
        candidates: list[CatalogCandidate],
        resolutions: list[CandidateResolution],
        evaluations: list[CandidateEvaluation],
        reason: str,
    ) -> AdviceResult: ...


@dataclass(frozen=True)
class CatalogDesignDependencies:
    """All external collaborators used by the catalog graph."""

    discover_explorer: DiscoveryService
    discover_reviewer: DiscoveryService
    resolve: ResolutionService
    evaluate: EvaluationService
    advise: AdviceService
    provenance: list[dict[str, Any]] = field(default_factory=list)


def default_catalog_design_dependencies(*, repo_root: Path | None = None) -> CatalogDesignDependencies:
    """Construct live catalog collaborators for the CLI."""
    provenance: list[dict[str, Any]] = []
    existing_snapshot = load_existing_lessons(repo_root or Path.cwd())
    return CatalogDesignDependencies(
        discover_explorer=lambda request, existing_lessons=existing_snapshot: default_discover_explorer(
            request,
            existing_lessons=existing_lessons,
            repo_root=repo_root,
            provenance_sink=provenance,
        ),
        discover_reviewer=lambda request, existing_lessons=existing_snapshot: default_discover_reviewer(
            request,
            existing_lessons=existing_lessons,
            repo_root=repo_root,
            provenance_sink=provenance,
        ),
        resolve=lambda request, candidates, existing_lessons, advice: default_resolve_candidates(
            request,
            candidates,
            existing_lessons,
            advice,
            repo_root=repo_root,
            provenance_sink=provenance,
        ),
        evaluate=lambda request, candidates, resolutions: default_evaluate_candidates(
            request,
            candidates,
            resolutions,
            repo_root=repo_root,
            provenance_sink=provenance,
        ),
        advise=lambda request, candidates, resolutions, evaluations, reason: default_request_advice(
            request,
            candidates,
            resolutions,
            evaluations,
            reason,
            repo_root=repo_root,
            provenance_sink=provenance,
        ),
        provenance=provenance,
    )


def default_discover_explorer(
    request: CatalogRequest,
    *,
    existing_lessons: Sequence[ExistingLesson] = (),
    repo_root: Path | None = None,
    provenance_sink: list[dict[str, Any]] | None = None,
) -> CandidateBatch:
    """Ask the explorer for web-informed candidates without returning citations."""
    prompt = build_discover_explorer_prompt(
        request,
        existing_snapshot=_existing_snapshot(existing_lessons),
        quality_contract=_discovery_quality_contract(request.category),
    )
    return _invoke_structured(
        catalog_explorer,
        CandidateBatch,
        prompt,
        stage="discover_explorer",
        repo_root=repo_root,
        provenance_sink=provenance_sink,
    )


def default_discover_reviewer(
    request: CatalogRequest,
    *,
    existing_lessons: Sequence[ExistingLesson] = (),
    repo_root: Path | None = None,
    provenance_sink: list[dict[str, Any]] | None = None,
) -> CandidateBatch:
    """Ask the catalog reviewer for an independent offline candidate set."""
    prompt = build_discover_reviewer_prompt(
        request,
        existing_snapshot=_existing_snapshot(existing_lessons),
        quality_contract=_discovery_quality_contract(request.category),
    )
    return _invoke_structured(
        catalog_reviewer,
        CandidateBatch,
        prompt,
        stage="discover_reviewer",
        repo_root=repo_root,
        provenance_sink=provenance_sink,
    )


def default_resolve_candidates(
    request: CatalogRequest,
    candidates: list[CatalogCandidate],
    existing_lessons: list[ExistingLesson],
    advice: str | None,
    *,
    repo_root: Path | None = None,
    provenance_sink: list[dict[str, Any]] | None = None,
) -> ResolutionBatch:
    """Have the catalog reviewer merge, separate, or relate candidates."""
    prompt = build_resolve_candidates_prompt(
        request,
        advice,
        existing_lessons_payload=_dump([lesson.model_dump(mode="json") for lesson in existing_lessons]),
        candidates_payload=_dump([candidate.model_dump(mode="json") for candidate in candidates]),
    )
    return _invoke_structured(
        catalog_reviewer,
        ResolutionBatch,
        prompt,
        stage="resolve_candidates",
        repo_root=repo_root,
        provenance_sink=provenance_sink,
    )


def default_evaluate_candidates(
    request: CatalogRequest,
    candidates: list[CatalogCandidate],
    resolutions: list[CandidateResolution],
    *,
    repo_root: Path | None = None,
    provenance_sink: list[dict[str, Any]] | None = None,
) -> EvaluationBatch:
    """Ask the catalog reviewer for multi-axis quality scores."""
    prompt = build_evaluate_candidates_prompt(
        request,
        candidates_payload=_dump([candidate.model_dump(mode="json") for candidate in candidates]),
        resolutions_payload=_dump([resolution.model_dump(mode="json") for resolution in resolutions]),
    )
    return _invoke_structured(
        catalog_reviewer,
        EvaluationBatch,
        prompt,
        stage="evaluate_candidates",
        repo_root=repo_root,
        provenance_sink=provenance_sink,
    )


def default_request_advice(
    request: CatalogRequest,
    candidates: list[CatalogCandidate],
    resolutions: list[CandidateResolution],
    evaluations: list[CandidateEvaluation],
    reason: str,
    *,
    repo_root: Path | None = None,
    provenance_sink: list[dict[str, Any]] | None = None,
) -> AdviceResult:
    """Ask the catalog reviewer for one bounded architecture diagnosis."""
    prompt = build_request_advice_prompt(
        request,
        reason,
        candidates_payload=_dump([candidate.model_dump(mode="json") for candidate in candidates]),
        resolutions_payload=_dump([resolution.model_dump(mode="json") for resolution in resolutions]),
        evaluations_payload=_dump([evaluation.model_dump(mode="json") for evaluation in evaluations]),
    )
    return _invoke_structured(
        catalog_reviewer,
        AdviceResult,
        prompt,
        stage="request_advice",
        repo_root=repo_root,
        provenance_sink=provenance_sink,
    )


def _invoke_structured[T: BaseModel](
    factory: Callable[..., JobRunner],
    schema: type[T],
    prompt: str,
    *,
    stage: str,
    repo_root: Path | None,
    provenance_sink: list[dict[str, Any]] | None,
) -> T:
    """Invoke one catalog agent and record response metadata or failure."""
    agent = factory() if repo_root is None else factory(repo_root=repo_root)
    try:
        result = agent.structured(schema).invoke(prompt)
    except Exception as exc:  # noqa: BLE001 - caller records graph-visible failure
        _record_provenance(stage, agent, provenance_sink, error=exc)
        raise
    _record_provenance(stage, agent, provenance_sink)
    return result


def _record_provenance(
    stage: str,
    agent: JobRunner,
    sink: list[dict[str, Any]] | None,
    *,
    error: Exception | None = None,
) -> None:
    """Append effective backend metadata to the optional scratch sink."""
    if sink is None:
        return
    response = agent.last_response
    client = response.client if response is not None else agent.client.name
    model = response.model if response is not None else (agent.model or getattr(agent.client, "default_model", ""))
    agent_name = response.agent if response is not None else agent.agent
    variant = response.variant if response is not None else agent.variant
    attempts = response.attempts if response is not None else getattr(error, "attempts", ())
    sink.append(
        {
            "stage": stage,
            "status": "error" if error is not None else "ok",
            "client": client,
            "model": model,
            "agent": agent_name,
            "variant": variant,
            "latency_ms": response.latency_ms if response is not None else None,
            "attempts": [asdict(attempt) for attempt in attempts],
            "error": f"{type(error).__name__}: {error}"[:500] if error is not None else None,
        }
    )


def _existing_snapshot(existing_lessons: Sequence[ExistingLesson]) -> str:
    """Render only the catalog facts needed for novelty comparison."""
    snapshot = [
        {
            "catalog_id": lesson.slug,
            "title": lesson.title,
            "aliases": lesson.aliases[:4],
            "objective_summary": _compact_objective(lesson.objective_summary),
            "notes": _compact_objective(lesson.notes),
            "teaching_point_ids": lesson.teaching_point_ids[:8],
            "chapter": lesson.chapter,
        }
        for lesson in existing_lessons
    ]
    return _dump(snapshot) if snapshot else "[] (no existing entries supplied)"


def _compact_objective(value: str, *, limit: int = 240) -> str:
    """Keep enough objective text for novelty matching without bloating prompts."""
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    truncated = normalized[: limit - 1].rsplit(" ", 1)[0]
    return truncated + "…"


def _discovery_quality_contract(category: str) -> str:
    """Return shared novelty rules plus the category-specific boundary card."""
    normalized = normalize_text(category)
    if "phrase" in normalized:
        card = (
            "Phraseology: propose one reusable communicative move with stable "
            "pragmatic conditions and an interaction boundary. Do not propose a "
            "phrase list, one isolated expression, or a whole encounter."
        )
    elif "pronunc" in normalized:
        card = (
            "Pronunciation: propose one sound, stress, rhythm, intonation, "
            "phonotactic, or sound-to-spelling decision with a clear listening "
            "or speaking boundary. Do not propose grammar, vocabulary lists, "
            "or orthography without a sound target."
        )
    elif "communic" in normalized:
        card = (
            "Communicative: propose one bounded real-world task or learner "
            "decision requiring reusable language work. Do not propose a broad "
            "domain, scenario, or vocabulary theme."
        )
    else:
        card = (
            "Grammar: propose one form–meaning–use decision with its relevant "
            "constraints and nearest contrasts. An incomplete paradigm or a "
            "narrower facet of an existing lesson is not a new owner."
        )
    return f"""Shared novelty contract:
1. Derive one recurring learner need and one independently teachable outcome.
2. Compare the learner decision and teachable core with the full snapshot.
3. Emit a candidate only when it has a defensible independent difference.
4. Different wording, audience, setting, difficulty, or examples alone never
   establish novelty. Do not repair an umbrella by inventing sibling owners.
5. If the core claim cannot be stated as one stable learner decision, omit the
   candidate.

For every emitted candidate, populate teachable_core, nearest_existing, and
independent_difference as well as the ordinary learner question and scope.

Title rule: write a short learner-facing title in ASD-STE100-style controlled
English. Use simple words, active voice, and one clear action or contrast. Keep
the title concrete and preferably under ten words. Include the Norwegian form
when it helps the learner, followed by a short English meaning when needed.
Avoid internal labels and noun stacks such as dummy, expletive, presentational,
agentless, correlative, interrogative, constituent, threshold, or scope unless
the title also gives a plain learner action. Prefer titles such as “Use det in
weather sentences”, “Talk about past habits with pleide å”, or “Say either/or
with enten ... eller”. This is a title-writing rule, not a claim of full
ASD-STE100 certification.

Category card: {card}"""


def _dump(value: Sequence[object]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


__all__ = [
    "AdviceService",
    "CatalogDesignDependencies",
    "DiscoveryService",
    "EvaluationService",
    "ResolutionService",
    "default_request_advice",
    "default_catalog_design_dependencies",
    "default_discover_explorer",
    "default_discover_reviewer",
    "default_evaluate_candidates",
    "default_resolve_candidates",
]
