"""Entry point: `generate_lessons` runs approved slots in parallel."""

from __future__ import annotations

import re
from collections.abc import Sequence
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from pathlib import Path
from shutil import copytree
from typing import cast

import yaml

from lesson_builder.clients.llm.jobs import cancel_active_clients
from lesson_builder.clients.llm.jobs import reset_client_cancellation
from lesson_builder.domain.curriculum.models import CurriculumPlan
from lesson_builder.domain.curriculum.models import CurriculumPlanSlot
from lesson_builder.workflow.lesson_generation.artifacts import build_batch_output_root
from lesson_builder.workflow.lesson_generation.artifacts import build_run_output_root
from lesson_builder.workflow.lesson_generation.batch_artifacts import build_slot_run_id as _build_slot_run_id
from lesson_builder.workflow.lesson_generation.batch_artifacts import load_batch as _load_batch
from lesson_builder.workflow.lesson_generation.batch_artifacts import write_batch_result as _write_batch_result
from lesson_builder.workflow.lesson_generation.batch_plan import build_slot_plan_hash as _build_slot_plan_hash
from lesson_builder.workflow.lesson_generation.batch_plan import hash_text as _hash_text
from lesson_builder.workflow.lesson_generation.batch_plan import load_plan as _load_plan
from lesson_builder.workflow.lesson_generation.batch_plan import render_plan as _render_plan
from lesson_builder.workflow.lesson_generation.batch_plan import select_catalog_ids as _select_catalog_ids
from lesson_builder.workflow.lesson_generation.dependencies import LessonGenerationDeps
from lesson_builder.workflow.lesson_generation.dependencies import default_proposal_source_copier
from lesson_builder.workflow.lesson_generation.models import LessonBatchResult
from lesson_builder.workflow.lesson_generation.models import LessonQualityStatus
from lesson_builder.workflow.lesson_generation.models import LessonResult
from lesson_builder.workflow.lesson_generation.rich_authoring import ContentRemediationRequired
from lesson_builder.workflow.lesson_generation.runner import run_lesson_package
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DEFAULT_JOB
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_DEFAULT_WORKERS
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_PLAN_SCHEMAS
from lesson_builder.workflow.lesson_generation.settings import K_LESSON_GENERATION_SCRATCH_ROOT
from lesson_builder.workflow.lesson_generation.stage_attestations import verify_source_attestations

K_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def generate_lessons(
    *,
    repo_root: Path,
    plan_path: Path,
    batch_id: str,
    job: str = K_LESSON_GENERATION_DEFAULT_JOB,
    max_workers: int = K_LESSON_GENERATION_DEFAULT_WORKERS,
    catalog_ids: Sequence[str] | None = None,
) -> LessonBatchResult:
    """Generate one scratch lesson package per approved plan slot in parallel."""
    root = Path(repo_root)
    _validate_batch_inputs(batch_id, max_workers)
    plan = _prepare_generation_plan(root, plan_path, catalog_ids)
    output_root = build_batch_output_root(root, batch_id)
    results_by_id = _initial_batch_results(plan, batch_id)
    _write_running_batch(output_root, batch_id, plan_path, job, max_workers, list(results_by_id.values()))
    _generate_pending_slots(
        root=root,
        batch_id=batch_id,
        slots=plan.slots,
        job=job,
        max_workers=max_workers,
        output_root=output_root,
        results_by_id=results_by_id,
        plan_path=plan_path,
    )
    generated_results = sorted(results_by_id.values(), key=lambda item: item.catalog_id)
    _compile_batch_results(
        root=root,
        output_root=output_root,
        batch_id=batch_id,
        plan_path=plan_path,
        job=job,
        max_workers=max_workers,
        results=generated_results,
    )
    final_result = _batch_result(
        status="completed",
        batch_id=batch_id,
        plan_path=plan_path,
        job=job,
        max_workers=max_workers,
        results=generated_results,
    )
    _write_batch_result(output_root, final_result)
    return final_result


def resume_lessons(
    *,
    repo_root: Path,
    source_batch_path: Path,
    batch_id: str,
    plan_path: Path | None = None,
    job: str | None = None,
    max_workers: int | None = None,
    catalog_ids: Sequence[str] | None = None,
) -> LessonBatchResult:
    """Resume a batch without regenerating completed packages."""
    root = Path(repo_root)
    _validate_batch_id(batch_id)
    source_batch = _load_batch(root / source_batch_path)
    selected_plan_path = plan_path or Path(source_batch.plan_path)
    plan = _load_plan(root / selected_plan_path)
    selected_ids = catalog_ids if catalog_ids is not None else [item.catalog_id for item in source_batch.results]
    plan = _select_catalog_ids(plan, selected_ids)
    selected_job = job or source_batch.job
    selected_workers = max_workers or source_batch.max_workers
    _validate_worker_count(selected_workers)
    results_by_id, pending_slots, pending_context = _resume_entries(root, source_batch, plan, batch_id)
    output_root = build_batch_output_root(root, batch_id)
    _write_running_batch(
        output_root,
        batch_id,
        selected_plan_path,
        selected_job,
        selected_workers,
        list(results_by_id.values()),
    )
    _compile_batch_results(
        root=root,
        output_root=output_root,
        batch_id=batch_id,
        plan_path=selected_plan_path,
        job=selected_job,
        max_workers=selected_workers,
        results=list(results_by_id.values()),
        statuses=("generated",),
    )
    _generate_pending_slots(
        root=root,
        batch_id=batch_id,
        slots=pending_slots,
        job=selected_job,
        max_workers=selected_workers,
        output_root=output_root,
        results_by_id=results_by_id,
        pending_context=pending_context,
        plan_path=selected_plan_path,
    )
    results = sorted(results_by_id.values(), key=lambda item: item.catalog_id)
    _compile_batch_results(
        root=root,
        output_root=output_root,
        batch_id=batch_id,
        plan_path=selected_plan_path,
        job=selected_job,
        max_workers=selected_workers,
        results=results,
    )
    final = _batch_result(
        status="completed",
        batch_id=batch_id,
        plan_path=selected_plan_path,
        job=selected_job,
        max_workers=selected_workers,
        results=results,
    )
    _write_batch_result(output_root, final)
    return final


def recompile_lessons(
    *,
    repo_root: Path,
    plan_path: Path,
    source_batch_path: Path,
    batch_id: str,
    catalog_ids: Sequence[str] | None = None,
) -> LessonBatchResult:
    """Recompile existing model packages without making duplicate LLM calls."""
    root = Path(repo_root)
    _validate_batch_id(batch_id)
    plan = _load_plan(root / plan_path)
    if catalog_ids is not None:
        plan = _select_catalog_ids(plan, catalog_ids)
    source_batch = _load_batch(root / source_batch_path)
    prior_by_id = {item.catalog_id: item for item in source_batch.results}
    results = [_recompile_slot(root, batch_id, slot, prior_by_id.get(slot.catalog_id)) for slot in plan.slots]
    results.sort(key=lambda item: item.catalog_id)
    final_result = _batch_result(
        status="completed",
        batch_id=batch_id,
        plan_path=plan_path,
        job=source_batch.job,
        max_workers=source_batch.max_workers,
        results=results,
    )
    _write_batch_result(build_batch_output_root(root, batch_id), final_result)
    return final_result


def _validate_batch_id(batch_id: str) -> None:
    """Require a safe aggregate batch identifier."""
    if not K_RUN_ID_RE.fullmatch(batch_id):
        raise ValueError("batch_id must be a single safe identifier of at most 64 characters")


def _validate_worker_count(max_workers: int) -> None:
    """Require at least one generation worker."""
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1")


def _validate_batch_inputs(batch_id: str, max_workers: int) -> None:
    """Validate the shared generate-lessons request parameters."""
    _validate_batch_id(batch_id)
    _validate_worker_count(max_workers)


def _prepare_generation_plan(
    root: Path,
    plan_path: Path,
    catalog_ids: Sequence[str] | None,
) -> CurriculumPlan:
    """Load a supported plan and optionally restrict it to requested IDs."""
    plan = _load_plan(root / plan_path)
    if plan.schema_version not in K_LESSON_GENERATION_PLAN_SCHEMAS:
        raise ValueError(f"unsupported catalog plan schema: {plan.schema_version!r}")
    return _select_catalog_ids(plan, catalog_ids) if catalog_ids is not None else plan


def _initial_batch_results(plan: CurriculumPlan, batch_id: str) -> dict[str, LessonResult]:
    """Create pending result rows for every selected plan slot."""
    return {
        slot.catalog_id: LessonResult(
            catalog_id=slot.catalog_id,
            run_id=_build_slot_run_id(batch_id, slot.catalog_id),
            status="pending",
            human_gate="not_reached",
            output_root=str(K_LESSON_GENERATION_SCRATCH_ROOT / _build_slot_run_id(batch_id, slot.catalog_id)),
            slot_plan_hash=_build_slot_plan_hash(slot),
        )
        for slot in plan.slots
    }


def _write_running_batch(
    output_root: Path,
    batch_id: str,
    plan_path: Path,
    job: str,
    max_workers: int,
    results: Sequence[LessonResult],
    *,
    status: str = "running",
) -> None:
    """Persist one aggregate checkpoint for a batch operation."""
    _write_batch_result(
        output_root,
        _batch_result(
            status=status,
            batch_id=batch_id,
            plan_path=plan_path,
            job=job,
            max_workers=max_workers,
            results=results,
        ),
    )


def _generate_pending_slots(
    *,
    root: Path,
    batch_id: str,
    slots: Sequence[CurriculumPlanSlot],
    job: str,
    max_workers: int,
    output_root: Path,
    results_by_id: dict[str, LessonResult],
    pending_context: dict[str, tuple[str, Path]] | None = None,
    plan_path: Path,
) -> None:
    """Run pending owners in parallel and checkpoint each completed owner."""
    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="catalog-lesson")
    futures: dict[Future[LessonResult], CurriculumPlanSlot] = {}
    cancel_workers = False
    try:
        for slot in slots:
            context = pending_context.get(slot.catalog_id) if pending_context is not None else None
            if context is None:
                future = pool.submit(_generate_one, root, batch_id, slot, job)
            else:
                future = pool.submit(
                    _generate_one,
                    root,
                    batch_id,
                    slot,
                    job,
                    run_id=context[0],
                    output_root=context[1],
                )
            futures[future] = slot
        for future in as_completed(futures):
            generated = future.result()
            results_by_id[generated.catalog_id] = generated
            _write_running_batch(
                output_root,
                batch_id,
                plan_path,
                job,
                max_workers,
                list(results_by_id.values()),
            )
    except KeyboardInterrupt:
        cancel_workers = True
        _write_running_batch(
            output_root,
            batch_id,
            plan_path,
            job,
            max_workers,
            list(results_by_id.values()),
            status="interrupted",
        )
        raise
    except BaseException:
        cancel_workers = True
        raise
    finally:
        if cancel_workers:
            for future in futures:
                future.cancel()
            try:
                cancel_active_clients()
            finally:
                try:
                    pool.shutdown(wait=True, cancel_futures=True)
                finally:
                    reset_client_cancellation()
        else:
            pool.shutdown(wait=True)


def _compile_batch_results(
    *,
    root: Path,
    output_root: Path,
    batch_id: str,
    plan_path: Path,
    job: str,
    max_workers: int,
    results: Sequence[LessonResult],
    statuses: tuple[str, ...] = ("generated", "parked"),
) -> None:
    """Compile eligible results and checkpoint each catalog transition."""
    try:
        for result in results:
            if result.status not in statuses or result.generated_source_dir is None:
                continue
            _compile_catalog_result(root, result)
            _write_running_batch(output_root, batch_id, plan_path, job, max_workers, results)
    except KeyboardInterrupt:
        _write_running_batch(
            output_root,
            batch_id,
            plan_path,
            job,
            max_workers,
            results,
            status="interrupted",
        )
        raise


def _resume_entries(
    root: Path,
    source_batch: LessonBatchResult,
    plan: CurriculumPlan,
    batch_id: str,
) -> tuple[dict[str, LessonResult], list[CurriculumPlanSlot], dict[str, tuple[str, Path]]]:
    """Classify every selected slot into reusable, pending, or fresh work."""
    prior_by_id = {item.catalog_id: item for item in source_batch.results}
    results_by_id: dict[str, LessonResult] = {}
    pending_slots: list[CurriculumPlanSlot] = []
    pending_context: dict[str, tuple[str, Path]] = {}
    for slot in plan.slots:
        result, pending, context = _resume_slot(root, prior_by_id.get(slot.catalog_id), slot, batch_id)
        results_by_id[slot.catalog_id] = result
        if pending:
            pending_slots.append(slot)
        if context is not None:
            pending_context[slot.catalog_id] = context
    return results_by_id, pending_slots, pending_context


def _resume_slot(
    root: Path,
    prior: LessonResult | None,
    slot: CurriculumPlanSlot,
    batch_id: str,
) -> tuple[LessonResult, bool, tuple[str, Path] | None]:
    """Classify one prior result while preserving its reusable run identity."""
    current_plan_hash = _build_slot_plan_hash(slot)
    if prior is not None and prior.status == "parked" and prior.slot_plan_hash == current_plan_hash:
        if prior.generated_source_dir is not None:
            prior_source = root / prior.generated_source_dir
            if prior_source.is_dir() and not verify_source_attestations(prior_source):
                return prior, False, None
        prior = None
    elif prior is not None and prior.status == "parked":
        prior = None
    if prior is not None and _is_resume_remediation(prior, root):
        return _pending_resume_result(root, prior, slot, current_plan_hash)
    if prior is not None and prior.generated_source_dir is not None and _has_generated_source(prior, root):
        generated_source_dir = prior.generated_source_dir
        if verify_source_attestations(root / generated_source_dir):
            return _pending_resume_result(root, prior, slot, current_plan_hash)
        return (
            prior.model_copy(update={"status": "generated", "human_gate": "not_reached", "error": None}),
            False,
            None,
        )
    return _new_resume_result(root, prior, slot, batch_id, current_plan_hash)


def _is_resume_remediation(prior: LessonResult | None, root: Path) -> bool:
    """Return whether a prior remediation result can re-enter generation."""
    return (
        prior is not None
        and prior.status == "needs_human_remediation"
        and prior.generated_source_dir is not None
        and (root / prior.generated_source_dir).is_dir()
    )


def _has_generated_source(prior: LessonResult | None, root: Path) -> bool:
    """Return whether a prior result has an existing generated source directory."""
    return prior is not None and prior.generated_source_dir is not None and (root / prior.generated_source_dir).is_dir()


def _pending_resume_result(
    root: Path,
    prior: LessonResult,
    slot: CurriculumPlanSlot,
    current_plan_hash: str,
) -> tuple[LessonResult, bool, tuple[str, Path]]:
    """Build a pending result and its reused run context."""
    if prior.generated_source_dir is None:
        raise ValueError(f"{slot.catalog_id}: pending resume requires a generated source directory")
    reused_output_root = root / prior.output_root
    if not reused_output_root.is_dir():
        reused_output_root = (root / prior.generated_source_dir).parent
    result = LessonResult(
        catalog_id=slot.catalog_id,
        run_id=prior.run_id,
        status="pending",
        human_gate="not_reached",
        output_root=str(reused_output_root.relative_to(root)),
        slot_plan_hash=current_plan_hash,
    )
    return result, True, (prior.run_id, reused_output_root)


def _new_resume_result(
    root: Path,
    prior: LessonResult | None,
    slot: CurriculumPlanSlot,
    batch_id: str,
    current_plan_hash: str,
) -> tuple[LessonResult, bool, tuple[str, Path] | None]:
    """Build a pending result for a new or partially preserved run."""
    if prior is not None and (root / prior.output_root).is_dir():
        run_id = prior.run_id
        output_root = root / prior.output_root
        context = (run_id, output_root)
    else:
        run_id = _build_slot_run_id(batch_id, slot.catalog_id)
        output_root = build_run_output_root(root, run_id)
        context = None
    result = LessonResult(
        catalog_id=slot.catalog_id,
        run_id=run_id,
        status="pending",
        human_gate="not_reached",
        output_root=str(output_root.relative_to(root)),
        slot_plan_hash=current_plan_hash,
    )
    return result, True, context


def _recompile_slot(
    root: Path,
    batch_id: str,
    slot: CurriculumPlanSlot,
    prior: LessonResult | None,
) -> LessonResult:
    """Replay compiler stages for one prior generated source package."""
    run_id = _build_slot_run_id(batch_id, slot.catalog_id)
    if prior is None or prior.generated_source_dir is None:
        return LessonResult(
            catalog_id=slot.catalog_id,
            run_id=run_id,
            status="failed",
            human_gate="not_reached",
            generated_source_dir=None,
            output_root=str(K_LESSON_GENERATION_SCRATCH_ROOT / run_id),
            slot_plan_hash=_build_slot_plan_hash(slot),
            error=(
                f"source batch has no generated package for {slot.catalog_id!r}; retry this owner with generate-lessons"
            ),
        )
    output_root = build_run_output_root(root, run_id)
    result = LessonResult(
        catalog_id=slot.catalog_id,
        run_id=run_id,
        status="parked",
        human_gate="pending",
        generated_source_dir=None,
        output_root=str(output_root.relative_to(root)),
        quality_status="not_freshly_reviewed",
        coverage_status="not_freshly_reviewed",
        slot_plan_hash=_build_slot_plan_hash(slot),
    )
    try:
        replay_source = output_root / "generated"
        copytree(root / prior.generated_source_dir, replay_source, dirs_exist_ok=True)
        attestation_issues = verify_source_attestations(replay_source)
        if attestation_issues:
            raise ValueError(
                "recompile cannot park a package without fresh stage evidence: " + "; ".join(attestation_issues)
            )
        if prior.slot_plan_hash != _build_slot_plan_hash(slot):
            raise ValueError(
                "source package was built from a stale catalog slot plan; regenerate this owner before recompiling"
            )
        summary = run_lesson_package(
            repo_root=root,
            run_id=run_id,
            fixture_source=replay_source,
            deps=LessonGenerationDeps(copier=default_proposal_source_copier),
            generate=False,
            curriculum_slot_sha256=result.slot_plan_hash,
            catalog_id=result.catalog_id,
        )
        diagnostics = summary.get("exercise_diagnostics")
        if isinstance(diagnostics, dict):
            result.exercise_diagnostics = diagnostics
        attestation_issues = verify_source_attestations(replay_source)
        if attestation_issues:
            raise ValueError("recompiled source lost or invalidated stage evidence: " + "; ".join(attestation_issues))
        result.generated_source_dir = str(replay_source.relative_to(root))
    except Exception as exc:  # noqa: BLE001 - preserve per-entry outcome
        result.status = "failed"
        result.human_gate = "not_reached"
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _generate_one(
    root: Path,
    batch_id: str,
    slot: CurriculumPlanSlot,
    job: str,
    *,
    run_id: str | None = None,
    output_root: Path | None = None,
) -> LessonResult:
    """Generate one package; retain diagnostics instead of failing the batch."""
    run_id = run_id or _build_slot_run_id(batch_id, slot.catalog_id)
    output_root = output_root or build_run_output_root(root, run_id)
    rendered_plan = _render_plan(slot)
    slot_plan_hash = _hash_text(rendered_plan)
    try:
        plan_dir = output_root / "approved-plan"
        # A resumed batch may already contain an approved plan and partial
        # draft/normalization stages. Reusing the same run directory lets the
        # graph checkpoint resume the failed boundary without regenerating the
        # expensive prose stages.
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plan_dir / "plan.md"
        plan_path.write_text(rendered_plan, encoding="utf-8")
        summary = run_lesson_package(
            repo_root=root,
            run_id=run_id,
            fixture_source=plan_dir,
            generate=True,
            job=job,
            stop_after_generation=True,
            curriculum_slot_sha256=slot_plan_hash,
            catalog_id=slot.catalog_id,
        )
    except ContentRemediationRequired as exc:
        quality_status, coverage_status = _run_review_statuses(output_root)
        generated_dir = output_root / "generated"
        return LessonResult(
            catalog_id=slot.catalog_id,
            run_id=run_id,
            status="needs_human_remediation",
            human_gate="not_reached",
            generated_source_dir=(str(generated_dir.relative_to(root)) if generated_dir.is_dir() else None),
            output_root=str(output_root.relative_to(root)),
            slot_plan_hash=slot_plan_hash,
            quality_status=quality_status or "content_needs_human",
            coverage_status=coverage_status,
            # Keep the failing stage identity now that one exception type
            # carries it instead of a subclass per stage.
            error=f"{type(exc).__name__}[{exc.stage}]: {exc}",
        )
    except Exception as exc:  # noqa: BLE001 - one bad owner must not cancel siblings
        quality_status, coverage_status = _run_review_statuses(output_root)
        return LessonResult(
            catalog_id=slot.catalog_id,
            run_id=run_id,
            status="failed",
            human_gate="not_reached",
            generated_source_dir=None,
            output_root=str(output_root.relative_to(root)),
            slot_plan_hash=slot_plan_hash,
            quality_status=quality_status,
            coverage_status=coverage_status,
            error=f"{type(exc).__name__}: {exc}",
        )
    quality_status, coverage_status = _run_review_statuses(output_root)
    generated_source_dir = summary.get("generated_source_dir")
    if not isinstance(generated_source_dir, str):
        raise TypeError("generation graph completed without a generated source package")
    return LessonResult(
        catalog_id=slot.catalog_id,
        run_id=run_id,
        status="generated",
        human_gate="not_reached",
        generated_source_dir=str(Path(generated_source_dir).relative_to(root)),
        output_root=str(output_root.relative_to(root)),
        slot_plan_hash=slot_plan_hash,
        quality_status=quality_status,
        coverage_status=coverage_status,
    )


def _compile_catalog_result(root: Path, result: LessonResult) -> None:
    """Compile one generated source and convert its state to parked or failed."""
    try:
        generated_source = root / result.generated_source_dir if result.generated_source_dir is not None else root
        attestation_issues = verify_source_attestations(generated_source)
        if attestation_issues:
            raise ValueError("generated catalog source cannot reach the human gate: " + "; ".join(attestation_issues))
        summary = run_lesson_package(
            repo_root=root,
            run_id=result.run_id,
            fixture_source=generated_source,
            deps=LessonGenerationDeps(copier=default_proposal_source_copier),
            generate=False,
            curriculum_slot_sha256=result.slot_plan_hash,
            catalog_id=result.catalog_id,
        )
        diagnostics = summary.get("exercise_diagnostics")
        if isinstance(diagnostics, dict):
            result.exercise_diagnostics = diagnostics
    except Exception as exc:  # noqa: BLE001 - preserve per-entry batch outcome
        result.status = "failed"
        result.human_gate = "not_reached"
        result.error = f"{type(exc).__name__}: {exc}"
    else:
        result.status = "parked"
        result.human_gate = "pending"


def _batch_result(
    *,
    status: str,
    batch_id: str,
    plan_path: Path,
    job: str,
    max_workers: int,
    results: Sequence[LessonResult],
) -> LessonBatchResult:
    """Build a checkpoint aggregate from the latest per-owner states."""
    result_list = sorted(results, key=lambda item: item.catalog_id)
    parked = sum(item.status == "parked" for item in result_list)
    failed = sum(item.status == "failed" for item in result_list)
    quality_pass = sum(item.quality_status == "pass" for item in result_list)
    quality_needs_human = sum(item.quality_status == "needs_human" for item in result_list)
    quality_content_needs_human = sum(item.quality_status == "content_needs_human" for item in result_list)
    quality_reviewer_unavailable = sum(item.quality_status == "reviewer_unavailable" for item in result_list)
    quality_reviewer_invalid = sum(item.quality_status == "reviewer_invalid" for item in result_list)
    quality_content_repair_invalid = sum(item.quality_status == "content_repair_invalid" for item in result_list)
    quality_not_freshly_reviewed = sum(item.quality_status == "not_freshly_reviewed" for item in result_list)
    diagnostics_needs_human = sum(
        isinstance(item.exercise_diagnostics, dict) and item.exercise_diagnostics.get("status") == "needs_human"
        for item in result_list
    )
    remediation_pending = sum(item.status == "needs_human_remediation" for item in result_list)
    return LessonBatchResult(
        status=status,  # type: ignore[arg-type]
        batch_id=batch_id,
        plan_path=str(plan_path),
        job=job,
        max_workers=max_workers,
        total=len(result_list),
        parked=parked,
        failed=failed,
        human_gates_pending=parked,
        quality_pass=quality_pass,
        quality_needs_human=quality_needs_human,
        quality_content_needs_human=quality_content_needs_human,
        quality_reviewer_unavailable=quality_reviewer_unavailable,
        quality_reviewer_invalid=quality_reviewer_invalid,
        quality_content_repair_invalid=quality_content_repair_invalid,
        quality_not_freshly_reviewed=quality_not_freshly_reviewed,
        diagnostics_needs_human=diagnostics_needs_human,
        remediation_pending=remediation_pending,
        results=result_list,
    )


def _run_review_statuses(
    output_root: Path,
) -> tuple[LessonQualityStatus | None, str | None]:
    """Read quality and coverage status from one content-addressed run receipt."""
    receipt_path = Path(output_root) / "llm_receipt.json"
    try:
        payload = yaml.safe_load(receipt_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    quality = payload.get("quality")
    coverage = payload.get("coverage")
    stage_receipts = payload.get("stage_receipts")
    lesson_review = stage_receipts.get("lesson_review") if isinstance(stage_receipts, dict) else None
    quality_status = quality.get("status") if isinstance(quality, dict) else None
    if quality_status is None and isinstance(lesson_review, dict):
        quality_status = lesson_review.get("status")
    coverage_status = coverage.get("status") if isinstance(coverage, dict) else None
    allowed_quality = {
        "pass",
        "needs_human",
        "content_needs_human",
        "reviewer_unavailable",
        "reviewer_invalid",
        "content_repair_invalid",
        "unconfigured",
        "not_freshly_reviewed",
    }
    # Stage receipts use the small machine vocabulary (pass/fail/invalid/
    # unavailable); batch results expose the catalog-facing status vocabulary.
    # Preserve these failures instead of silently dropping them as ``None``.
    if isinstance(quality_status, str):
        quality_status = {
            "fail": "needs_human",
            "invalid": "reviewer_invalid",
            "unavailable": "reviewer_unavailable",
        }.get(quality_status, quality_status)
    typed_quality = cast(LessonQualityStatus, quality_status) if quality_status in allowed_quality else None
    return typed_quality, str(coverage_status) if isinstance(coverage_status, str) else None


__all__ = [
    "generate_lessons",
    "recompile_lessons",
    "resume_lessons",
]
