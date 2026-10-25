"""Entry point: `dispatch_workbench_action` delegates guarded HTTP actions to application use cases."""

from __future__ import annotations

from _thread import RLock
from pathlib import Path
from typing import Any
from typing import Literal
from urllib.parse import unquote

from pydantic import TypeAdapter

from lesson_builder.application.operations.workbench_audio import plan_workbench_audio
from lesson_builder.application.operations.workbench_audio import synthesize_workbench_audio
from lesson_builder.application.operations.workbench_chat import begin_workbench_chat_turn
from lesson_builder.application.operations.workbench_chat import begin_workbench_explanation_turn
from lesson_builder.application.operations.workbench_chat import build_workbench_finding_fix_request
from lesson_builder.application.operations.workbench_chat import run_workbench_chat_turn
from lesson_builder.application.operations.workbench_chat import start_new_workbench_chat
from lesson_builder.application.operations.workbench_curriculum import preview_workbench_curriculum_edits
from lesson_builder.application.operations.workbench_editing import WorkbenchSourceNotFound
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_exercise_alignment import ExerciseAlignment
from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from lesson_builder.application.operations.workbench_new_lesson import NewLessonRequest
from lesson_builder.application.operations.workbench_new_lesson import create_workbench_lesson
from lesson_builder.application.operations.workbench_new_lesson import validate_new_lesson
from lesson_builder.application.operations.workbench_promotion import discard_workbench_draft
from lesson_builder.application.operations.workbench_promotion import promote_workbench_lesson
from lesson_builder.application.operations.workbench_promotion import pull_canonical_proposal
from lesson_builder.application.operations.workbench_proposals import decide_proposal
from lesson_builder.application.operations.workbench_records import ChatTranscript
from lesson_builder.application.operations.workbench_review import resolve_workbench_findings
from lesson_builder.application.operations.workbench_review import review_uncovered_blocks
from lesson_builder.application.operations.workbench_review import review_workbench_lesson
from lesson_builder.web.settings import K_WEB_WORKBENCH_ACTION_PARTS
from lesson_builder.web.settings import K_WEB_WORKBENCH_BLOCK_ACTION_PARTS
from lesson_builder.web.settings import K_WEB_WORKBENCH_CHAT_LIFECYCLE_ROUTE_PARTS
from lesson_builder.web.workbench_api import build_lesson_payload
from lesson_builder.web.workbench_jobs import ChatJobControl
from lesson_builder.web.workbench_jobs import WorkbenchJobs
from lesson_builder.web.workbench_wire import AudioPlanRequest
from lesson_builder.web.workbench_wire import AudioSynthesizeRequest
from lesson_builder.web.workbench_wire import CurriculumChatRequest
from lesson_builder.web.workbench_wire import CurriculumPreviewRequest
from lesson_builder.web.workbench_wire import DiscardRequest
from lesson_builder.web.workbench_wire import EmptyRequest
from lesson_builder.web.workbench_wire import ExplainRequest
from lesson_builder.web.workbench_wire import FindingResolveRequest
from lesson_builder.web.workbench_wire import KeepRequest
from lesson_builder.web.workbench_wire import LessonChatRequest
from lesson_builder.web.workbench_wire import ReviewRequest
from lesson_builder.web.workbench_wire import WorkbenchActionResponse
from lesson_builder.web.workbench_wire import WorkbenchAudioPlanActionResponse
from lesson_builder.web.workbench_wire import WorkbenchBooleanResult
from lesson_builder.web.workbench_wire import WorkbenchChatJobTicket
from lesson_builder.web.workbench_wire import WorkbenchJobTicket
from lesson_builder.web.workbench_wire import WorkbenchLessonActionResult
from lesson_builder.web.workbench_wire import WorkbenchRegenerationRequest
from lesson_builder.web.workbench_wire import WorkbenchRegenerationTicket
from lesson_builder.web.workbench_wire import WorkbenchResetResult
from lesson_builder.workflow.workbench_regeneration.runner import execute_regeneration
from lesson_builder.workflow.workbench_regeneration.runner import resume_regeneration
from lesson_builder.workflow.workbench_regeneration.runner import start_regeneration
from lesson_builder.workspace.workbench_store import read_workbench_record


def dispatch_workbench_action(
    root: Path, path: str, body: dict[str, Any], jobs: WorkbenchJobs, lock: RLock
) -> WorkbenchActionResponse:
    """Validate transport arguments and schedule slow jobs or return fresh mutation state."""
    if path.startswith("/api/jobs/") and path.endswith("/cancel"):
        identity = path.removeprefix("/api/jobs/").removesuffix("/cancel").rstrip("/")
        if not identity or "/" in identity:
            raise WorkbenchSourceNotFound("job not found")
        _parse_empty_request(body)
        result: WorkbenchBooleanResult = {"cancelled": jobs.cancel_job(identity)}
        return result
    if path.startswith("/api/curriculum/"):
        return _dispatch_curriculum_action(root, path.removeprefix("/api/curriculum/"), body, jobs, lock)
    if path.startswith("/api/lessons/"):
        return _dispatch_lesson_action(root, path, body, jobs, lock)
    raise WorkbenchSourceNotFound("route not found")


def _dispatch_curriculum_action(
    root: Path, route: str, body: dict[str, Any], jobs: WorkbenchJobs, lock: RLock
) -> WorkbenchActionResponse:
    """Route curriculum authoring and chat operations."""
    if route == "lessons":
        create_request = NewLessonRequest.model_validate(body)
        validate_new_lesson(root, create_request)
        ticket: WorkbenchJobTicket = {
            "job_id": jobs.submit_progress(
                lambda progress: create_workbench_lesson(root, create_request, lock, progress)
            )
        }
        return ticket
    if route == "preview":
        preview_request: CurriculumPreviewRequest = TypeAdapter(CurriculumPreviewRequest).validate_python(body)
        return preview_workbench_curriculum_edits(root, preview_request["edits"])
    if route == "chat":
        chat_request: CurriculumChatRequest = TypeAdapter(CurriculumChatRequest).validate_python(body)
        message = _require_text(chat_request["message"], "message")
        turn_id = begin_workbench_chat_turn(root, "curriculum", message, [], lock, kind="curriculum")
        return _submit_chat_turn(root, "curriculum", turn_id, jobs, lock)
    if route == "chat/new":
        _parse_empty_request(body)
        start_new_workbench_chat(root, "curriculum", lock)
        result: WorkbenchResetResult = {"reset": True}
        return result
    if route.startswith("chat/"):
        return _chat_lifecycle_action(root, "curriculum", route.removeprefix("chat/"), body, jobs, lock)
    raise WorkbenchSourceNotFound("route not found")


def _dispatch_lesson_action(
    root: Path, path: str, body: dict[str, Any], jobs: WorkbenchJobs, lock: RLock
) -> WorkbenchActionResponse:
    """Route one lesson's chat, review, proposal, and lifecycle actions."""
    parts = path.removeprefix("/api/lessons/").split("/")
    if len(parts) < K_WEB_WORKBENCH_ACTION_PARTS - 1:
        raise WorkbenchSourceNotFound("route not found")
    lesson_id = parts[0]
    resolve_lesson_package(root, lesson_id)
    route = parts[1:]
    if route[:1] == ["audio"]:
        return _dispatch_lesson_audio_action(root, lesson_id, route, body, jobs, lock)
    if route[:1] == ["chat"]:
        return _lesson_chat_action(root, lesson_id, route, body, jobs, lock)
    if route == ["explain"]:
        explain_request: ExplainRequest = TypeAdapter(ExplainRequest).validate_python(body)
        block_id = _require_text(explain_request["block_id"], "block_id")
        turn_id = begin_workbench_explanation_turn(root, lesson_id, block_id, lock)
        return _submit_chat_turn(root, lesson_id, turn_id, jobs, lock)
    if route[:1] in (["promote"], ["pull"], ["discard"], ["regenerate"]):
        return _lifecycle_action(root, lesson_id, route, body, jobs, lock)
    if len(route) == K_WEB_WORKBENCH_ACTION_PARTS and route[0] == "exercises" and route[2] == "keep":
        keep_request: KeepRequest = TypeAdapter(KeepRequest).validate_python(body)
        ExerciseAlignment(root, lesson_id).keep_current(
            read_workbench_source(root, lesson_id),
            unquote(route[1]),
            keep_request.get("note", ""),
            reason=keep_request["reason"],
            comment=keep_request.get("comment"),
        )
        return _lesson_result(root, lesson_id)
    if route == ["review", "uncovered"]:
        _parse_empty_request(body)
        ticket: WorkbenchJobTicket = {
            "job_id": jobs.submit_cancelable_progress(
                lambda progress, cancellation: review_uncovered_blocks(
                    root,
                    lesson_id,
                    lock,
                    progress=progress,
                    cancellation=cancellation,
                )
            )
        }
        return ticket
    if route == ["review"]:
        return _schedule_review(root, lesson_id, body, jobs, lock)
    if route[:1] == ["findings"]:
        return _finding_action(root, lesson_id, route, body, jobs, lock)
    return _decide_proposal_route(root, lesson_id, route, body, lock)


def _dispatch_lesson_audio_action(
    root: Path, lesson_id: str, route: list[str], body: dict[str, Any], jobs: WorkbenchJobs, lock: RLock
) -> WorkbenchActionResponse:
    """Route confirmation planning and confirmed audio synthesis."""
    if route == ["audio", "plan"]:
        plan_request: AudioPlanRequest = TypeAdapter(AudioPlanRequest).validate_python(body)
        result = plan_workbench_audio(
            root,
            lesson_id,
            plan_request.get("scope", "missing_and_stale"),
            selection=plan_request.get("selection", "missing_and_stale"),
            block_key=plan_request.get("block_key"),
            lock=lock,
        )
        response: WorkbenchAudioPlanActionResponse = {"plan": result}
        return response
    if route == ["audio", "synthesize"]:
        synthesis_request: AudioSynthesizeRequest = TypeAdapter(AudioSynthesizeRequest).validate_python(body)
        token = _require_text(synthesis_request["plan_token"], "plan_token")
        ticket: WorkbenchJobTicket = {
            "job_id": jobs.submit_progress(
                lambda progress: synthesize_workbench_audio(root, token, lock=lock, on_progress=progress),
                provider_name="Google Cloud Text-to-Speech",
            )
        }
        return ticket
    raise WorkbenchSourceNotFound("route not found")


def _lesson_chat_action(
    root: Path, lesson_id: str, route: list[str], body: dict[str, Any], jobs: WorkbenchJobs, lock: RLock
) -> WorkbenchActionResponse:
    """Start, retry, cancel, or reset a lesson chat turn."""
    if route == ["chat", "new"]:
        _parse_empty_request(body)
        start_new_workbench_chat(root, lesson_id, lock)
        result: WorkbenchResetResult = {"reset": True}
        return result
    if len(route) == K_WEB_WORKBENCH_CHAT_LIFECYCLE_ROUTE_PARTS and route[2] in ("retry", "cancel"):
        return _chat_lifecycle_action(root, lesson_id, f"{route[1]}/{route[2]}", body, jobs, lock)
    if route == ["chat"]:
        chat_request: LessonChatRequest = TypeAdapter(LessonChatRequest).validate_python(body)
        message = _require_text(chat_request["message"], "message")
        chips = chat_request.get("chips", [])
        turn_id = begin_workbench_chat_turn(root, lesson_id, message, chips, lock)
        return _submit_chat_turn(root, lesson_id, turn_id, jobs, lock)
    raise WorkbenchSourceNotFound("route not found")


def _decide_proposal_route(
    root: Path, lesson_id: str, route: list[str], body: dict[str, Any], lock: RLock
) -> WorkbenchLessonActionResult:
    if len(route) in (K_WEB_WORKBENCH_ACTION_PARTS, K_WEB_WORKBENCH_BLOCK_ACTION_PARTS) and route[0] == "proposals":
        decision = route[-1]
        if decision not in ("accept", "reject", "keep") or (
            len(route) == K_WEB_WORKBENCH_BLOCK_ACTION_PARTS and route[2] != "blocks"
        ):
            raise WorkbenchSourceNotFound("route not found")
        if decision == "keep":
            request: KeepRequest = TypeAdapter(KeepRequest).validate_python(body)
            note = request.get("note", "")
            reason = request["reason"]
            comment = request.get("comment")
        else:
            _parse_empty_request(body)
            note = None
            reason = None
            comment = None
        proposal = decide_proposal(
            root,
            lesson_id,
            unquote(route[1]),
            "keep" if decision == "keep" else "accept" if decision == "accept" else "reject",
            key=unquote(route[3]) if len(route) == K_WEB_WORKBENCH_BLOCK_ACTION_PARTS else None,
            note=note,
            reason=reason,
            comment=comment,
        )
        resume_regeneration(root, proposal, lock)
        return {**_lesson_result(root, lesson_id), "proposal": proposal}
    raise WorkbenchSourceNotFound("route not found")


def _schedule_review(
    root: Path, lesson_id: str, body: dict[str, Any], jobs: WorkbenchJobs, lock: RLock
) -> WorkbenchJobTicket:
    request: ReviewRequest = TypeAdapter(ReviewRequest).validate_python(body)
    scope = request["scope"]
    key = request.get("key")
    if scope != "lesson" and key is None:
        raise ValueError("scoped review requires a block key")
    ticket: WorkbenchJobTicket = {
        "job_id": jobs.submit_cancelable_progress(
            lambda progress, cancellation: review_workbench_lesson(
                root,
                lesson_id,
                scope,
                key,
                lock,
                progress=progress,
                cancellation=cancellation,
            )
        )
    }
    return ticket


def _require_text(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _parse_empty_request(body: object) -> EmptyRequest:
    return TypeAdapter(EmptyRequest).validate_python(body)


def _lesson_result(root: Path, lesson_id: str) -> WorkbenchLessonActionResult:
    return {"lesson": build_lesson_payload(root, resolve_lesson_package(root, lesson_id))}


def _submit_chat_turn(
    root: Path, identity: str, turn_id: str, jobs: WorkbenchJobs, lock: RLock
) -> WorkbenchChatJobTicket:
    job_id = jobs.submit_chat_turn(
        turn_id,
        lambda control: _chat_action(root, identity, turn_id, jobs, lock, control),
    )
    return {"job_id": job_id, "turn_id": turn_id}


def _chat_action(
    root: Path, identity: str, turn_id: str, jobs: WorkbenchJobs, lock: RLock, control: ChatJobControl
) -> dict[str, object]:
    result = run_workbench_chat_turn(
        root,
        identity,
        turn_id,
        lock,
        jobs.codex_client,
        on_partial=control.report_partial,
        on_turn_started=lambda thread_id, codex_turn_id: control.set_interrupt(
            thread_id, codex_turn_id, jobs.codex_client.interrupt
        ),
        on_turn_finished=control.clear_interrupt,
        cancellation=control.cancellation,
    )
    key = result.get("regenerate")
    if isinstance(key, str):
        if identity == "curriculum":
            raise ValueError("curriculum chat cannot start lesson regeneration")
        run_id = start_regeneration(root, identity, key, lock)
        control.link_run(run_id)
        result["run"] = execute_regeneration(root, run_id, lock, cancellation=control.cancellation)
    return result


def _chat_lifecycle_action(
    root: Path, identity: str, route: str, body: dict[str, Any], jobs: WorkbenchJobs, lock: RLock
) -> WorkbenchActionResponse:
    _parse_empty_request(body)
    turn_id, action = route.rsplit("/", 1)
    if action == "cancel":
        result: WorkbenchBooleanResult = {"cancelled": jobs.cancel_chat_turn(turn_id)}
        return result
    if action == "retry":
        transcript = read_workbench_record(root, "chat", identity, ChatTranscript)
        turn = next(
            (item for item in reversed(transcript.turns) if item.role == "author" and item.turn_id == turn_id), None
        )
        if turn is None:
            raise WorkbenchSourceNotFound("chat turn not found")
        retried_id = begin_workbench_chat_turn(
            root,
            identity,
            turn.text,
            turn.chips,
            lock,
            kind=turn.kind,
            origin=turn.origin,
            retry_turn_id=turn_id,
        )
        return _submit_chat_turn(root, identity, retried_id, jobs, lock)
    raise WorkbenchSourceNotFound("route not found")


def _lifecycle_action(
    root: Path, lesson_id: str, route: list[str], body: dict[str, Any], jobs: WorkbenchJobs, lock: RLock
) -> WorkbenchActionResponse:
    if route == ["promote"]:
        _parse_empty_request(body)
        return {**promote_workbench_lesson(root, lesson_id), **_lesson_result(root, lesson_id)}
    if route == ["pull"]:
        _parse_empty_request(body)
        proposal = pull_canonical_proposal(root, lesson_id)
        if proposal is None:
            return {
                **_lesson_result(root, lesson_id),
                "message": "Canonical already matches; provenance updated.",
            }
        return {**_lesson_result(root, lesson_id), "proposal": proposal}
    if route == ["discard"]:
        discard_request: DiscardRequest = TypeAdapter(DiscardRequest).validate_python(body)
        commit = discard_workbench_draft(root, lesson_id, confirmed=discard_request.get("confirmed") is True)
        return {"commit": commit}
    if route == ["regenerate"]:
        regeneration_request: WorkbenchRegenerationRequest = TypeAdapter(WorkbenchRegenerationRequest).validate_python(
            body
        )
        mode, key, exercise_keys = _regeneration_request(regeneration_request)
        identity = start_regeneration(root, lesson_id, key, lock, mode=mode, exercise_keys=exercise_keys)
        ticket: WorkbenchRegenerationTicket = {
            "run_id": identity,
            "job_id": jobs.submit_cancelable_progress(
                lambda _progress, cancellation: execute_regeneration(root, identity, lock, cancellation=cancellation),
                run_id=identity,
            ),
        }
        return ticket
    raise WorkbenchSourceNotFound("route not found")


def _regeneration_request(
    request: WorkbenchRegenerationRequest,
) -> tuple[Literal["scope", "exercises"], str | None, list[str] | None]:
    """Parse the explicit regeneration mode and its mode-specific request fields."""
    if request["mode"] == "scope":
        return "scope", _require_text(request["key"], "key"), None
    return "exercises", None, request["exercise_keys"]


def _finding_action(
    root: Path, lesson_id: str, route: list[str], body: dict[str, Any], jobs: WorkbenchJobs, lock: RLock
) -> WorkbenchActionResponse:
    if len(route) == K_WEB_WORKBENCH_ACTION_PARTS and route[0] == "findings":
        identity = unquote(route[1])
        if route[2] == "fix":
            _parse_empty_request(body)
            message, chips = build_workbench_finding_fix_request(root, lesson_id, identity, lock)
            turn_id = begin_workbench_chat_turn(root, lesson_id, message, chips, lock, origin="review_fix")
            return _submit_chat_turn(root, lesson_id, turn_id, jobs, lock)
        if route[2] == "resolve":
            request: FindingResolveRequest = TypeAdapter(FindingResolveRequest).validate_python(body)
            note = request.get("note", "")
            finding_ids = request.get("finding_ids", [identity])
            if identity not in finding_ids:
                raise ValueError("finding_ids must include the route finding")
            resolve_workbench_findings(
                root,
                lesson_id,
                finding_ids,
                note,
                reason=request["reason"],
                comment=request.get("comment"),
            )
            return _lesson_result(root, lesson_id)
    raise WorkbenchSourceNotFound("route not found")
