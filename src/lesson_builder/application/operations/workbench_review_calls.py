"""Entry point: ``run_workbench_review_calls`` runs and collects scoped reviewer calls."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from contextvars import copy_context
from pathlib import Path
from typing import Any
from typing import Literal

from lesson_builder.application.operations.review_exercises import build_exercise_package_report
from lesson_builder.application.operations.review_exercises import verify_attempt_surface
from lesson_builder.application.operations.review_exercises import verify_exercises
from lesson_builder.application.operations.review_exercises import verify_open_rubrics
from lesson_builder.application.operations.review_exercises import verify_open_semantics
from lesson_builder.application.operations.review_judges import build_naturalness_prompt
from lesson_builder.application.operations.review_judges import build_objective_alignment_prompt
from lesson_builder.application.operations.review_judges import build_pedagogy_prompt
from lesson_builder.application.operations.review_standalone import review_standalone_exercises
from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_records import ReviewFinding
from lesson_builder.application.operations.workbench_review_evidence import populate_exercise_finding
from lesson_builder.application.operations.workbench_settings_operations import WorkbenchSettings
from lesson_builder.application.operations.workbench_settings_operations import workbench_runner_for_review_model
from lesson_builder.application.operations.workbench_settings_operations import workbench_runner_for_role
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_REVIEW_MAX_CONCURRENT_CALLS
from lesson_builder.application.settings import K_APPLICATION_WORKBENCH_REVIEW_REASKS
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.llm.invocation import JobRunner
from lesson_builder.domain.lesson.models.review_checks import NaturalnessReview
from lesson_builder.domain.lesson.models.review_checks import ObjectiveAlignmentReview
from lesson_builder.domain.lesson.models.review_checks import PedagogyReview
from lesson_builder.formats.workbench_source import SourceBlock
from lesson_builder.workspace.paths import WorkspacePaths


def map_review_finding(
    blocks: tuple[SourceBlock, ...],
    criterion: str,
    excerpt: str,
    explanation: str,
    *,
    unit: str = "",
    severity: str = "P2",
    message: str = "",
    quote_english: str = "",
    suggested_change: str = "",
    details: str = "",
) -> ReviewFinding:
    """Locate exact evidence, mentioned keys, or a named section; otherwise attach at the scope top."""
    unit_blocks = tuple(block for block in blocks if block.key == unit or block.key.startswith(unit + "#"))
    cited = excerpt or _source_evidence(unit_blocks, explanation) or _source_evidence(blocks, explanation)
    target = _find_cited_block(blocks, unit_blocks, cited) or _find_named_block(blocks, unit, explanation)
    evidence = cited or (target.source.strip() if target else "lesson")
    identity = hashlib.sha256(
        (
            criterion
            + "\0"
            + (target.key if target else "lesson-top")
            + "\0"
            + evidence
            + ("" if cited else "\0" + explanation)
        ).encode()
    ).hexdigest()[:24]
    return ReviewFinding(
        finding_id=f"ai-{identity}",
        key=target.key if target else "lesson-top",
        criterion=criterion,
        excerpt=evidence,
        explanation=explanation,
        message=message or explanation,
        cited_excerpt=cited,
        citation_exact=bool(cited),
        advisory=True,
        quote_english=quote_english,
        suggested_change=suggested_change,
        details=details,
        severity="major"
        if severity in ("P0", "P1", "blocker", "blocking", "warning", "major", "error", "material")
        else "minor",
    )


def run_workbench_review_calls(
    root: Path,
    source: WorkbenchSource,
    lesson: dict[str, Any],
    selected: tuple[SourceBlock, ...],
    scoped: dict[str, Any],
    settings: WorkbenchSettings,
    context: str,
    cancellation: CancellationToken,
) -> tuple[list[ReviewFinding], list[str], JobRunner | None, JobRunner | None]:
    """Run scoped reviewers concurrently and collect their source-bound findings."""

    def check_cancelled() -> None:
        cancellation.raise_if_cancelled()

    has_lesson = any(block.kind != "exercise" for block in selected)
    exercises = [element for element in scoped.get("elements", []) if element.get("element_kind") == "exercise"]
    tasks: list[tuple[str, Callable[[], Any]]] = []
    lesson_runner: JobRunner | None = None
    primary_lesson_order: tuple[str, ...] = ()
    if has_lesson:
        lesson_runner, lesson_tasks = _lesson_review_task_specs(
            root, source, selected, lesson, context, check_cancelled
        )
        tasks.extend(lesson_tasks)
        if lesson_tasks:
            primary_lesson_order = ("pedagogy", "naturalness")

    exercise_runner: JobRunner | None = None
    if exercises:
        exercise_runner, exercise_tasks = _exercise_review_task_specs(root, scoped, "", check_cancelled)
        tasks.extend(exercise_tasks)

    second_opinion_model = settings.second_opinion.model if settings.second_opinion.enabled else None
    second_opinion_lesson_order: tuple[str, ...] = ()
    if second_opinion_model and has_lesson:
        second_lesson_runner, second_lesson_tasks = _lesson_review_task_specs(
            root, source, selected, lesson, context, check_cancelled, prefix="second_", model=second_opinion_model
        )
        tasks.extend(second_lesson_tasks)
        if second_lesson_runner is not None:
            second_opinion_lesson_order = ("second_pedagogy", "second_naturalness")

    second_exercise_runner: JobRunner | None = None
    if second_opinion_model and exercises:
        second_exercise_runner, second_exercise_tasks = _exercise_review_task_specs(
            root, scoped, "second_", check_cancelled, model=second_opinion_model
        )
        tasks.extend(second_exercise_tasks)

    results = _run_review_tasks(tasks, cancellation)
    findings, summaries = _collect_review_results(
        source,
        selected,
        scoped,
        results,
        primary_lesson_order,
        second_opinion_lesson_order,
        has_exercise_review=exercise_runner is not None,
        has_second_exercise_review=second_exercise_runner is not None,
    )
    return findings, summaries, lesson_runner, exercise_runner


def _collect_review_results(
    source: WorkbenchSource,
    selected: tuple[SourceBlock, ...],
    scoped: dict[str, Any],
    results: dict[str, Any],
    primary_lesson_order: tuple[str, ...],
    second_opinion_lesson_order: tuple[str, ...],
    *,
    has_exercise_review: bool,
    has_second_exercise_review: bool,
) -> tuple[list[ReviewFinding], list[str]]:
    findings: list[ReviewFinding] = []
    summaries: list[str] = []
    for task_name in primary_lesson_order:
        task_findings, summary = results[task_name]
        findings.extend(task_findings)
        if summary:
            summaries.append(summary)

    if has_exercise_review:
        exercise_findings, exercise_summary = _review_exercise_quality(
            source.blocks,
            selected,
            results["exercise_alignment"],
            _exercise_package_report(scoped, results, "exercise_"),
            results["exercise_standalone"],
        )
        findings.extend(exercise_findings)
        summaries.append(exercise_summary)

    second_summaries: list[str] = []
    for task_name in second_opinion_lesson_order:
        task_findings, summary = results[task_name]
        findings.extend(task_findings)
        if summary:
            second_summaries.append(summary)
    if has_second_exercise_review:
        second_findings, second_summary = _review_exercise_quality(
            source.blocks,
            selected,
            results["second_exercise_alignment"],
            _exercise_package_report(scoped, results, "second_exercise_"),
            results["second_exercise_standalone"],
        )
        _mark_second_opinion_findings(second_findings)
        findings.extend(second_findings)
        second_summaries.append(second_summary)
    if second_summaries:
        summaries.append("Second opinion: " + " ".join(second_summaries))
    return findings, summaries


def _lesson_review_task_specs(
    root: Path,
    source: WorkbenchSource,
    selected: tuple[SourceBlock, ...],
    lesson: dict[str, Any],
    context: str,
    check_cancelled: Callable[[], None],
    *,
    prefix: str = "",
    model: str | None = None,
) -> tuple[JobRunner | None, list[tuple[str, Callable[[], Any]]]]:
    pedagogy_runner = _new_review_runner(root, "lesson_review", model)
    naturalness_runner = _new_review_runner(root, "lesson_review", model)
    if pedagogy_runner is None or naturalness_runner is None:
        return None, []
    tasks: list[tuple[str, Callable[[], Any]]] = [
        (
            f"{prefix}pedagogy",
            lambda: _review_lesson_pedagogy(root, source, selected, lesson, pedagogy_runner, context, check_cancelled),
        ),
        (
            f"{prefix}naturalness",
            lambda: _review_lesson_naturalness(
                root, source, selected, lesson, naturalness_runner, context, check_cancelled
            ),
        ),
    ]
    if prefix:
        tasks = [(name, _second_opinion_task(task)) for name, task in tasks]
    return pedagogy_runner, tasks


def _exercise_review_task_specs(
    root: Path,
    scoped: dict[str, Any],
    prefix: str,
    check_cancelled: Callable[[], None],
    *,
    model: str | None = None,
) -> tuple[JobRunner | None, list[tuple[str, Callable[[], Any]]]]:
    alignment_runner = _new_review_runner(root, "exercise_review", model)
    if alignment_runner is None:
        return None, []
    report_runners = (
        ("answer", _new_review_runner(root, "exercise_review", model), verify_exercises),
        ("attempt", _new_review_runner(root, "exercise_review", model), verify_attempt_surface),
        ("rubric", _new_review_runner(root, "exercise_review", model), verify_open_rubrics),
        ("semantic", _new_review_runner(root, "exercise_review", model), verify_open_semantics),
        ("standalone", _new_review_runner(root, "exercise_review", model), review_standalone_exercises),
    )

    def run_alignment() -> ObjectiveAlignmentReview:
        check_cancelled()
        alignment = alignment_runner.structured(ObjectiveAlignmentReview).invoke(
            build_objective_alignment_prompt(scoped)
        )
        check_cancelled()
        return alignment

    tasks: list[tuple[str, Callable[[], Any]]] = [(f"{prefix}exercise_alignment", run_alignment)]
    for name, runner, operation in report_runners:
        if runner is None:
            return None, []

        def run_report(
            runner: JobRunner = runner, operation: Callable[..., dict[str, Any]] = operation
        ) -> dict[str, Any]:
            check_cancelled()
            report = operation(scoped, reviewer_agent=runner, strict=False)
            check_cancelled()
            if report.get("status") in ("reviewer_unavailable", "reviewer_invalid"):
                reviewer_name = "standalone" if operation is review_standalone_exercises else "exercises"
                raise ValueError(f"{reviewer_name} reviewer returned no valid report")
            return report

        tasks.append((f"{prefix}exercise_{name}", run_report))
    return alignment_runner, tasks


def _exercise_package_report(scoped: dict[str, Any], results: dict[str, Any], prefix: str) -> dict[str, Any]:
    return build_exercise_package_report(
        scoped,
        answer_report=results[f"{prefix}answer"],
        attempt_report=results[f"{prefix}attempt"],
        rubric_report=results[f"{prefix}rubric"],
        semantic_report=results[f"{prefix}semantic"],
    )


def _mark_second_opinion_findings(findings: list[ReviewFinding]) -> None:
    for finding in findings:
        finding.finding_id = f"second-{finding.finding_id}"
        finding.reviewer = "second_opinion"


def _new_review_runner(
    root: Path,
    role: Literal["lesson_review", "exercise_review"],
    model: str | None = None,
) -> JobRunner | None:
    runner = (
        workbench_runner_for_role(root, role) if model is None else workbench_runner_for_review_model(root, role, model)
    )
    if runner is not None:
        runner.max_reasks = K_APPLICATION_WORKBENCH_REVIEW_REASKS
    return runner


def _second_opinion_task(
    task: Callable[[], tuple[list[ReviewFinding], str | None]],
) -> Callable[[], tuple[list[ReviewFinding], str | None]]:
    def run() -> tuple[list[ReviewFinding], str | None]:
        findings, summary = task()
        _mark_second_opinion_findings(findings)
        return findings, summary

    return run


def _run_review_tasks(
    tasks: list[tuple[str, Callable[[], Any]]],
    cancellation: CancellationToken,
) -> dict[str, Any]:
    if not tasks:
        return {}
    results: dict[str, Any] = {}
    with ThreadPoolExecutor(
        max_workers=min(K_APPLICATION_WORKBENCH_REVIEW_MAX_CONCURRENT_CALLS, len(tasks)),
        thread_name_prefix="workbench-review",
    ) as executor:
        futures = {}
        for name, task in tasks:
            # Provider cancellation is held in a ContextVar and is not copied by the executor.
            future = executor.submit(copy_context().run, task)
            futures[future] = name
        try:
            for future in as_completed(futures):
                results[futures[future]] = future.result()
        except BaseException:
            cancellation.cancel("shutdown", "A concurrent workbench review call failed.")
            for future in futures:
                future.cancel()
            raise
    return results


def _review_lesson_pedagogy(
    root: Path,
    source: WorkbenchSource,
    selected: tuple[SourceBlock, ...],
    lesson: dict[str, Any],
    runner: JobRunner,
    context: str,
    check_cancelled: Callable[[], None],
) -> tuple[list[ReviewFinding], str]:
    pedagogy = runner.structured(PedagogyReview).invoke(build_pedagogy_prompt(lesson) + context)
    check_cancelled()
    findings: list[ReviewFinding] = []
    for issue in pedagogy.issues:
        if _outside_scope(source.blocks, selected, issue.evidence, issue.title + ": " + issue.fix):
            continue
        findings.append(
            map_review_finding(
                selected,
                issue.category,
                issue.evidence,
                issue.title + ": " + issue.fix,
                severity=issue.severity,
                message=issue.title,
                quote_english=issue.quote_english,
                suggested_change=issue.fix,
                details=json.dumps(issue.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
            )
        )
    return findings, pedagogy.summary


def _review_lesson_naturalness(
    root: Path,
    source: WorkbenchSource,
    selected: tuple[SourceBlock, ...],
    lesson: dict[str, Any],
    runner: JobRunner,
    context: str,
    check_cancelled: Callable[[], None],
) -> tuple[list[ReviewFinding], None]:
    naturalness = runner.structured(NaturalnessReview).invoke(
        build_naturalness_prompt(lesson, terminology_path=WorkspacePaths(root).terminology_registry) + context
    )
    check_cancelled()
    findings: list[ReviewFinding] = []
    for naturalness_issue in naturalness.issues:
        if _outside_scope(
            source.blocks,
            selected,
            naturalness_issue.evidence,
            naturalness_issue.message,
            naturalness_issue.unit_id,
        ):
            continue
        findings.append(
            map_review_finding(
                selected,
                "naturalness",
                naturalness_issue.evidence,
                naturalness_issue.message,
                unit=naturalness_issue.unit_id,
                severity=naturalness_issue.severity,
                message=naturalness_issue.message,
                quote_english=naturalness_issue.quote_english,
                suggested_change=naturalness_issue.suggested_change,
                details=json.dumps(naturalness_issue.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
            )
        )
    return findings, None


def _source_evidence(blocks: tuple[SourceBlock, ...], message: str) -> str:
    for opening, closing in (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")):
        pieces = message.split(opening)
        for piece in pieces[1::2] if opening == closing else pieces[1:]:
            quote = piece.partition(closing)[0]
            if quote and any(quote in block.source for block in blocks):
                return quote
    candidates = [
        value
        for block in blocks
        for field, value in block.fields.items()
        if field in ("body", "text", "statement") and value and value in message
    ]
    return max(candidates, key=len, default="")


def _find_cited_block(
    blocks: tuple[SourceBlock, ...], unit_blocks: tuple[SourceBlock, ...], cited: str
) -> SourceBlock | None:
    matches = [block for block in blocks if cited and cited in block.source]
    return next((block for block in matches if block in unit_blocks), matches[0] if matches else None)


def _find_named_block(blocks: tuple[SourceBlock, ...], unit: str, explanation: str) -> SourceBlock | None:
    named = next(
        (
            block
            for block in blocks
            if block.kind != "heading" and (block.key in explanation or block.source.strip() in explanation)
        ),
        None,
    )
    if named is not None:
        return named
    unit_block = next((block for block in blocks if block.key == unit), None)
    if unit_block is not None:
        return unit_block
    return next(
        (block for block in blocks if block.kind == "heading" and block.fields.get("title", "") in explanation),
        blocks[0] if blocks else None,
    )


def _review_exercise_quality(
    all_blocks: tuple[SourceBlock, ...],
    selected: tuple[SourceBlock, ...],
    alignment: ObjectiveAlignmentReview,
    package_report: dict[str, Any],
    standalone_report: dict[str, Any],
) -> tuple[list[ReviewFinding], str]:
    findings: list[ReviewFinding] = []
    for issue in alignment.issues:
        if _outside_scope(all_blocks, selected, issue.evidence, issue.message):
            continue
        findings.append(
            map_review_finding(
                selected,
                "objective_alignment",
                issue.evidence,
                issue.message + " " + issue.fix,
                unit=issue.objective_id,
                severity=issue.severity,
                message=issue.message,
                quote_english=issue.quote_english,
                suggested_change=issue.fix,
                details=json.dumps(issue.model_dump(mode="json"), ensure_ascii=False, sort_keys=True),
            )
        )
    reports = (("exercises", package_report), ("standalone", standalone_report))
    for name, report in reports:
        if report.get("status") in ("reviewer_unavailable", "reviewer_invalid"):
            raise ValueError(f"{name} reviewer returned no valid report")
        findings.extend(_map_exercise_report(selected, name, report))
    return findings, alignment.summary


def _outside_scope(
    all_blocks: tuple[SourceBlock, ...],
    selected: tuple[SourceBlock, ...],
    excerpt: str,
    explanation: str,
    unit: str = "",
) -> bool:
    unit_keys = {block.key for block in all_blocks if block.key == unit or block.key.startswith(unit + "#")}
    selected_keys = {block.key for block in selected}
    cited = (
        excerpt
        or _source_evidence(tuple(block for block in all_blocks if block.key in unit_keys), explanation)
        or _source_evidence(all_blocks, explanation)
    )
    matches = {block.key for block in all_blocks if cited and cited in block.source}
    if matches:
        candidates = matches & unit_keys or matches
        return not bool(candidates & selected_keys)
    return bool(unit_keys) and not bool(unit_keys & selected_keys)


def _map_exercise_report(blocks: tuple[SourceBlock, ...], name: str, report: dict[str, Any]) -> list[ReviewFinding]:
    findings = []
    if name == "exercises":
        for surface in ("answer_review", "attempt_surface", "open_rubrics", "open_semantics"):
            child = report.get(surface)
            if isinstance(child, dict):
                findings.extend(_map_exercise_report(blocks, surface, child))
        return findings
    for category in (
        "missing",
        "mismatches",
        "ambiguous",
        "unanswerable",
        "extra",
        "duplicates",
        "semantic_issues",
        "deterministic_findings",
    ):
        for issue in report.get(category, []):
            findings.append(_map_exercise_issue(blocks, name, category, issue, report))
    for issue in report.get("standalone_review", []):
        if issue.get("verdict") == "context_dependent":
            findings.append(
                map_review_finding(
                    blocks,
                    "standalone",
                    str(issue.get("evidence", "")),
                    str(issue.get("reason", "")),
                    unit=issue["id"],
                    severity="P1",
                    quote_english=str(issue.get("quote_english", "")),
                    suggested_change=str(issue.get("suggested_change", "")),
                    details=json.dumps(issue, ensure_ascii=False, sort_keys=True, default=str),
                )
            )
    return findings


def _map_exercise_issue(
    blocks: tuple[SourceBlock, ...], name: str, category: str, issue: object, report: dict[str, Any]
) -> ReviewFinding:
    if isinstance(issue, dict):
        unit = str(issue.get("id", ""))
        evidence = str(issue.get("evidence", ""))
        reason = str(issue.get("reviewer_reason") or issue.get("explanation") or issue.get("reason") or "")
        suggestion = str(issue.get("suggested_change") or issue.get("fix") or "")
        quote_english = str(issue.get("quote_english") or "")
        details = json.dumps(issue, ensure_ascii=False, sort_keys=True, default=str)
    else:
        unit, evidence = str(issue), ""
        reason = str(report.get("reasons", {}).get(unit) or f"{category}: {unit}")
        suggestion = ""
        quote_english = ""
        details = json.dumps({"category": category, "id": unit, "report": report}, ensure_ascii=False, sort_keys=True)
    finding = map_review_finding(
        blocks,
        f"{name}.{category}",
        evidence,
        reason or "The reviewer flagged a possible exercise issue.",
        unit=unit,
        severity="P1",
        message=reason or "The reviewer flagged a possible exercise issue.",
        quote_english=quote_english,
        suggested_change=suggestion,
        details=details,
    )
    return populate_exercise_finding(finding, blocks, from_report=True)
