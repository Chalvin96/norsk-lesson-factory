"""Entry point: `create_workbench_server` serves the workbench app and its JSON API on loopback."""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from http.server import ThreadingHTTPServer
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any
from typing import ClassVar
from typing import cast
from urllib.parse import unquote
from urllib.parse import urlsplit

from pydantic import TypeAdapter
from pydantic import ValidationError

from lesson_builder.application.operations.author_preview import AuthorPreview
from lesson_builder.application.operations.workbench_audio import read_workbench_audio_asset
from lesson_builder.application.operations.workbench_audio import recover_workbench_audio_plans
from lesson_builder.application.operations.workbench_chat import WorkbenchChatBusy
from lesson_builder.application.operations.workbench_chat import recover_workbench_chat_turns
from lesson_builder.application.operations.workbench_curriculum import CurriculumFinding
from lesson_builder.application.operations.workbench_curriculum import save_workbench_curriculum_edits
from lesson_builder.application.operations.workbench_editing import WorkbenchSourceNotFound
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_editing import save_workbench_draft
from lesson_builder.application.operations.workbench_editing import write_workbench_block
from lesson_builder.application.operations.workbench_editing import write_workbench_file
from lesson_builder.application.operations.workbench_lessons import InvalidLessonPackageError
from lesson_builder.application.operations.workbench_lessons import resolve_lesson_package
from lesson_builder.application.operations.workbench_promotion import CanonicalChanged
from lesson_builder.application.operations.workbench_settings_operations import WorkbenchSettings
from lesson_builder.application.operations.workbench_settings_operations import WorkbenchSettingsResponse
from lesson_builder.application.operations.workbench_settings_operations import restore_default_workbench_settings
from lesson_builder.application.operations.workbench_settings_operations import workbench_settings_response
from lesson_builder.application.operations.workbench_settings_operations import write_workbench_settings
from lesson_builder.clients.codex.app_server import CodexAppServerClient
from lesson_builder.clients.codex.types import CodexChatClient
from lesson_builder.clients.llm.opencode_runtime import sweep_stale_opencode_directories
from lesson_builder.domain.lesson.validation.lesson_ids import validate_slug
from lesson_builder.web.authority import find_request_authority_error
from lesson_builder.web.authority import require_loopback_bind
from lesson_builder.web.responses import render_rebuild_error_document
from lesson_builder.web.responses import send_uncached_audio
from lesson_builder.web.responses import send_uncached_html
from lesson_builder.web.responses import send_uncached_json
from lesson_builder.web.settings import K_WEB_WORKBENCH_API_PREFIX
from lesson_builder.web.settings import K_WEB_WORKBENCH_ASSET_CACHE_CONTROL
from lesson_builder.web.settings import K_WEB_WORKBENCH_ASSET_CONTENT_TYPES
from lesson_builder.web.settings import K_WEB_WORKBENCH_ASSET_ROUTE_PREFIX
from lesson_builder.web.settings import K_WEB_WORKBENCH_MAX_BODY_BYTES
from lesson_builder.web.settings import K_WEB_WORKBENCH_SETTINGS_PAGE_PATH
from lesson_builder.web.settings import K_WEB_WORKBENCH_SETTINGS_PATH
from lesson_builder.web.settings import K_WEB_WORKBENCH_SETTINGS_RESET_PATH
from lesson_builder.web.settings import K_WEB_WORKBENCH_WRITE_ROUTE_PARTS
from lesson_builder.web.workbench_actions import dispatch_workbench_action
from lesson_builder.web.workbench_api import build_check_payload
from lesson_builder.web.workbench_api import build_curriculum_checks
from lesson_builder.web.workbench_api import build_curriculum_payload
from lesson_builder.web.workbench_api import build_lesson_payload
from lesson_builder.web.workbench_api import build_source_payload
from lesson_builder.web.workbench_api import serialize_json
from lesson_builder.web.workbench_jobs import WorkbenchJobs
from lesson_builder.web.workbench_wire import BlockFieldsWriteRequest
from lesson_builder.web.workbench_wire import BlockSourceWriteRequest
from lesson_builder.web.workbench_wire import CurriculumSaveRequest
from lesson_builder.web.workbench_wire import EmptyRequest
from lesson_builder.web.workbench_wire import FileWriteRequest
from lesson_builder.web.workbench_wire import WorkbenchCheckResult
from lesson_builder.web.workbench_wire import WorkbenchCurriculumChecks
from lesson_builder.web.workbench_wire import WorkbenchCurriculumPayload
from lesson_builder.web.workbench_wire import WorkbenchCurriculumSaveResult
from lesson_builder.web.workbench_wire import WorkbenchErrorResponse
from lesson_builder.web.workbench_wire import WorkbenchLessonPayload
from lesson_builder.web.workbench_wire import WorkbenchRegenerationResponse
from lesson_builder.web.workbench_wire import WorkbenchSaveResult
from lesson_builder.web.workbench_wire import WorkbenchSourcePayload
from lesson_builder.web.workbench_wire import WorkbenchWriteResult
from lesson_builder.workflow.workbench_regeneration.runner import execute_regeneration
from lesson_builder.workflow.workbench_regeneration.runner import read_regeneration
from lesson_builder.workflow.workbench_regeneration.runner import recoverable_regenerations
from lesson_builder.workspace.workbench_git import WorkbenchGitError

K_WORKBENCH_SURFACE_NAME = "Workbench"
K_WORKBENCH_LESSON_ROUTE_PREFIX = "/lessons/"
K_WORKBENCH_API_LESSON_PREFIX = "/api/lessons/"
K_WORKBENCH_ASSET_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
K_WORKBENCH_AUDIO_ROUTE = re.compile(r"^/api/lessons/([a-z0-9_-]+)/audio/([a-f0-9]{64})$")
K_WORKBENCH_PAGE_PATH = re.compile(r"/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*/?")

WorkbenchRebuiltResponse = (
    WorkbenchCurriculumPayload
    | WorkbenchCurriculumChecks
    | WorkbenchSettingsResponse
    | WorkbenchLessonPayload
    | WorkbenchSourcePayload
)
WorkbenchMutationResponse = WorkbenchWriteResult | WorkbenchSaveResult


class WorkbenchServer(ThreadingHTTPServer):
    """Threaded loopback server bound to one workspace root."""

    allow_reuse_address = True
    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], handler: type[BaseHTTPRequestHandler], codex_client: CodexChatClient
    ) -> None:
        super().__init__(address, handler, bind_and_activate=False)
        self.jobs = WorkbenchJobs(codex_client)
        self.mutation_lock = threading.RLock()
        try:
            self.server_bind()
            self.server_activate()
        except BaseException:
            self.server_close()
            raise

    def server_close(self) -> None:
        """Release the job pool with the server socket."""
        super().server_close()
        self.jobs.close()


def create_workbench_server(
    repo_root: Path, host: str, port: int, *, codex_client: CodexChatClient | None = None
) -> WorkbenchServer:
    """Create a loopback-only workbench server; raise ``ValueError`` for any other bind."""
    require_loopback_bind(host, port)
    sweep_stale_opencode_directories()
    root = Path(repo_root).resolve()
    client = codex_client or CodexAppServerClient()
    server = WorkbenchServer((host, port), _build_workbench_handler(root), client)
    recover_workbench_chat_turns(root, server.mutation_lock)
    recover_workbench_audio_plans(root, server.mutation_lock)

    def resume(identity: str) -> None:
        server.jobs.submit_cancelable_progress(
            lambda _progress, cancellation: execute_regeneration(
                root, identity, server.mutation_lock, cancellation=cancellation
            ),
            run_id=identity,
        )

    for identity in recoverable_regenerations(root, server.mutation_lock):
        resume(identity)
    return server


def build_workbench_url(server: WorkbenchServer) -> str:
    """Return the Curriculum tab URL for a running workbench server."""
    host = str(server.server_address[0])
    return f"http://{host}:{server.server_port}/"


def _build_workbench_handler(repo_root: Path) -> type[BaseHTTPRequestHandler]:
    """Build a request handler closed over the workspace root."""

    class Handler(BaseHTTPRequestHandler):
        _repo_root: ClassVar[Path] = repo_root
        server: WorkbenchServer

        def do_GET(self) -> None:
            """Route one GET after the Host/Origin guard."""
            with self.server.mutation_lock:
                _route_get(self, self._repo_root)

        def do_PUT(self) -> None:
            """Write draft source after all loopback mutation guards."""
            self._mutate()

        def do_POST(self) -> None:
            """Commit a draft after all loopback mutation guards."""
            self._mutate()

        def do_DELETE(self) -> None:
            self._mutate()

        def do_PATCH(self) -> None:
            self._mutate()

        def do_HEAD(self) -> None:
            self._mutate()

        def do_OPTIONS(self) -> None:
            self._mutate()

        def log_message(self, format: str, *args: object) -> None:
            """Keep authored content and request details out of terminal logs."""

        def _mutate(self) -> None:
            with self.server.mutation_lock:
                _guard_and_send_mutation(self, self._repo_root, self.server.mutation_lock)

    return Handler


def _route_get(handler: BaseHTTPRequestHandler, root: Path) -> None:
    path = _request_path(handler)
    if path is None:
        return
    audio_route = K_WORKBENCH_AUDIO_ROUTE.fullmatch(path)
    if audio_route is not None:
        _send_workbench_audio_asset(handler, root, *audio_route.groups())
    elif path.startswith(K_WEB_WORKBENCH_API_PREFIX):
        _send_api(handler, root, path)
    elif path.startswith(K_WEB_WORKBENCH_ASSET_ROUTE_PREFIX):
        _send_asset(handler, path.removeprefix(K_WEB_WORKBENCH_ASSET_ROUTE_PREFIX))
    elif path in {"/", K_WEB_WORKBENCH_SETTINGS_PAGE_PATH} or _is_lesson_page(path):
        _send_app_page(handler)
    elif _is_unknown_app_page(path):
        _send_app_page(handler, status=404)
    else:
        handler.send_error(404, "workbench resource not found")


def _request_path(handler: BaseHTTPRequestHandler) -> str | None:
    """Validate request authority and parse the path before route selection."""
    authority_status = find_request_authority_error(handler)
    if authority_status is not None:
        handler.send_error(*authority_status)
        return None
    try:
        return urlsplit(handler.path).path
    except ValueError:
        handler.send_error(400, "malformed request target")
        return None


def _send_api(handler: BaseHTTPRequestHandler, repo_root: Path, path: str) -> None:
    """Send the curriculum or one lesson as JSON, 404 for an unknown resource, 503 for an unusable source."""
    if path.startswith("/api/runs/") and isinstance(handler.server, WorkbenchServer):
        _send_regeneration_state(handler, repo_root, path, handler.server.jobs, handler.server.mutation_lock)
        return
    if path.startswith("/api/jobs/") and isinstance(handler.server, WorkbenchServer):
        job = handler.server.jobs.find(path.removeprefix("/api/jobs/"))
        if job is None:
            _send_error_json(handler, 404, {"error": "job_not_found"})
        else:
            send_uncached_json(handler, 200, job)
        return
    static_builders: dict[str, Callable[[Path], WorkbenchRebuiltResponse]] = {
        f"{K_WEB_WORKBENCH_API_PREFIX}curriculum": build_curriculum_payload,
        f"{K_WEB_WORKBENCH_API_PREFIX}curriculum/checks": build_curriculum_checks,
        K_WEB_WORKBENCH_SETTINGS_PATH: workbench_settings_response,
    }
    if path in static_builders:
        _send_rebuilt_json(handler, lambda: static_builders[path](repo_root))
        return
    if not path.startswith(K_WORKBENCH_API_LESSON_PREFIX):
        _send_error_json(handler, 404, {"error": "not_found"})
        return
    source_route = path.endswith("/source")
    lesson_id = (
        path.removeprefix(K_WORKBENCH_API_LESSON_PREFIX).removesuffix("/source")
        if source_route
        else path.removeprefix(K_WORKBENCH_API_LESSON_PREFIX)
    )
    try:
        package = resolve_lesson_package(repo_root, lesson_id)
    except (InvalidLessonPackageError, OSError, RuntimeError):
        _send_error_json(handler, 503, {"error": "rebuild_failed"})
        return
    except ValueError:
        _send_error_json(handler, 404, {"error": "lesson_not_found"})
        return
    _send_rebuilt_json(
        handler,
        lambda: (
            build_source_payload(repo_root, lesson_id) if source_route else build_lesson_payload(repo_root, package)
        ),
    )


def _send_workbench_audio_asset(handler: BaseHTTPRequestHandler, root: Path, lesson_id: str, fingerprint: str) -> None:
    """Send a current, validated shared-cache WAV for author-only playback."""
    try:
        audio_path = read_workbench_audio_asset(root, lesson_id, fingerprint)
        send_uncached_audio(handler, audio_path.read_bytes())
    except (OSError, ValueError):
        _send_error_json(handler, 404, {"error": "audio_not_found"})


def _send_regeneration_state(
    handler: BaseHTTPRequestHandler, root: Path, path: str, jobs: WorkbenchJobs, lock: threading.RLock
) -> None:
    try:
        result = read_regeneration(root, path.removeprefix("/api/runs/"), lock)
        payload: WorkbenchRegenerationResponse = cast(WorkbenchRegenerationResponse, {**result})
        run_id = payload["run_id"]
        if isinstance(run_id, str) and (job_id := jobs.find_active_job_for_run(run_id)) is not None:
            payload["job_id"] = job_id
        send_uncached_json(handler, 200, payload)
    except ValueError as exc:
        _send_error_json(handler, 404, {"error": str(exc)})


def _guard_and_send_mutation(handler: BaseHTTPRequestHandler, root: Path, lock: threading.RLock) -> None:
    authority = find_request_authority_error(handler)
    if authority is not None:
        handler.send_error(*authority)
        return
    if handler.headers.get("X-Workbench-Request") != "1":
        _send_error_json(handler, 403, {"error": "workbench_request_header_required"})
        return
    if handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
        _send_error_json(handler, 415, {"error": "application_json_required"})
        return
    try:
        size = int(handler.headers.get("Content-Length", "0"))
        if size < 0 or size > K_WEB_WORKBENCH_MAX_BODY_BYTES:
            _send_error_json(handler, 413, {"error": "request_too_large"})
            return
        body = json.loads(handler.rfile.read(size))
        path = urlsplit(handler.path).path
        if not isinstance(body, dict):
            raise TypeError("JSON object required")
    except (ValueError, TypeError, UnicodeError):
        _send_error_json(handler, 400, {"error": "invalid_json_request"})
        return
    if _handle_workbench_settings_mutation(handler, root, path, body):
        return
    if handler.command == "POST" and (path == "/api/curriculum/chat" or not path.endswith("/save")):
        _send_action(handler, root, path, body, lock)
        return
    if path.startswith("/api/curriculum/"):
        _send_curriculum_mutation(handler, root, path, body)
        return
    _send_mutation(handler, root, path, body)


def _handle_workbench_settings_mutation(
    handler: BaseHTTPRequestHandler, root: Path, path: str, body: dict[str, Any]
) -> bool:
    if path == K_WEB_WORKBENCH_SETTINGS_PATH and handler.command == "PUT":
        _send_workbench_settings(handler, root, body)
        return True
    if path == K_WEB_WORKBENCH_SETTINGS_RESET_PATH and handler.command == "POST":
        try:
            _parse_empty_request(body)
            settings = restore_default_workbench_settings(root)
            payload: WorkbenchSettingsResponse = {
                **workbench_settings_response(root),
                "settings": cast(WorkbenchSettings, settings.model_dump(mode="json")),
            }
            send_uncached_json(handler, 200, payload)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            _send_error_json(handler, 400, {"error": str(exc)})
        return True
    return False


def _send_workbench_settings(handler: BaseHTTPRequestHandler, root: Path, body: dict[str, Any]) -> None:
    try:
        settings = write_workbench_settings(root, WorkbenchSettings.model_validate(body))
        payload: WorkbenchSettingsResponse = {
            **workbench_settings_response(root),
            "settings": cast(WorkbenchSettings, settings.model_dump(mode="json")),
        }
    except (TypeError, ValueError) as exc:
        _send_error_json(handler, 400, {"error": str(exc)})
        return
    except (OSError, RuntimeError) as exc:
        _send_error_json(handler, 503, {"error": str(exc)})
        return
    send_uncached_json(handler, 200, payload)


def _send_curriculum_mutation(handler: BaseHTTPRequestHandler, root: Path, path: str, body: dict[str, Any]) -> None:
    if handler.command != "PUT":
        _send_error_json(handler, 404, {"error": "not_found"})
        return
    try:
        if path == "/api/curriculum/save":
            request: CurriculumSaveRequest = TypeAdapter(CurriculumSaveRequest).validate_python(body)
            commit, findings = save_workbench_curriculum_edits(
                root,
                request["edits"],
                review_digest=request["review_digest"],
                finding_decisions=request["finding_decisions"],
                force=request["force"],
            )
        else:
            _send_error_json(handler, 404, {"error": "not_found"})
            return
        response: WorkbenchCurriculumSaveResult = {
            "commit": commit,
            "findings": cast(list[CurriculumFinding], serialize_json(findings)),
            "curriculum": build_curriculum_payload(root),
        }
        send_uncached_json(handler, 200, response)
    except ValueError as exc:
        _send_error_json(handler, 409, {"error": str(exc)})
    except (OSError, RuntimeError, TypeError) as exc:
        _send_error_json(handler, 503, {"error": str(exc)})


def _send_action(
    handler: BaseHTTPRequestHandler, root: Path, path: str, body: dict[str, Any], lock: threading.RLock
) -> None:
    try:
        if not isinstance(handler.server, WorkbenchServer):
            raise TypeError("workbench jobs unavailable")
        payload = dispatch_workbench_action(root, path, body, handler.server.jobs, lock)
    except WorkbenchSourceNotFound as exc:
        _send_error_json(handler, 404, {"error": str(exc)})
    except WorkbenchChatBusy as exc:
        _send_error_json(handler, 409, {"error": str(exc)})
    except InvalidLessonPackageError:
        _send_error_json(handler, 503, {"error": "rebuild_failed"})
    except CanonicalChanged as exc:
        _send_error_json(handler, 409, {"error": "canonical_changed", "diff": exc.diff})
    except ValidationError as exc:
        _send_action_validation_error(handler, exc)
    except ValueError as exc:
        status = 404 if "lesson package not found" in str(exc) or "slug" in str(exc) else 409
        _send_error_json(handler, status, {"error": str(exc)})
    except (OSError, RuntimeError) as exc:
        _send_error_json(handler, 503, {"error": str(exc)})
    else:
        status = 202 if isinstance(payload, dict) and "job_id" in payload else 200
        send_uncached_json(handler, status, serialize_json(payload))


def _send_action_validation_error(handler: BaseHTTPRequestHandler, error: ValidationError) -> None:
    """Explain missing owner reasons while retaining other action error routing."""
    missing_reason = any(item["loc"] == ("reason",) and item["type"] == "missing" for item in error.errors())
    _send_error_json(
        handler,
        400 if missing_reason else 409,
        {"error": "Choose a reason for this owner decision." if missing_reason else str(error)},
    )


def _send_mutation(handler: BaseHTTPRequestHandler, root: Path, path: str, body: dict[str, Any]) -> None:
    """Validate mutation transport values and delegate filesystem and git effects."""
    parts = path.removeprefix(K_WORKBENCH_API_LESSON_PREFIX).split("/")
    if not path.startswith(K_WORKBENCH_API_LESSON_PREFIX) or len(parts) not in (2, 3):
        _send_error_json(handler, 404, {"error": "not_found"})
        return
    lesson_id = parts[0]
    try:
        payload = _build_mutation_payload(handler, root, lesson_id, parts, body)
    except (InvalidLessonPackageError, OSError, RuntimeError, ValueError, WorkbenchGitError) as exc:
        status, response = _mutation_error_response(root, lesson_id, exc)
        send_uncached_json(handler, status, response)
        return
    send_uncached_json(handler, 200, payload)


def _build_mutation_payload(
    handler: BaseHTTPRequestHandler, root: Path, lesson_id: str, parts: list[str], body: dict[str, Any]
) -> WorkbenchMutationResponse:
    package = resolve_lesson_package(root, lesson_id)
    if handler.command == "POST" and parts[1:] == ["save"]:
        _parse_empty_request(body)
        commit, view = save_workbench_draft(root, lesson_id)
        check = build_check_payload(view, read_workbench_source(root, lesson_id))
        save_response: WorkbenchSaveResult = {
            "commit": commit,
            "check": cast(WorkbenchCheckResult, serialize_json(check)),
        }
        return save_response
    if handler.command == "PUT" and len(parts) == K_WEB_WORKBENCH_WRITE_ROUTE_PARTS:
        view = _write_source(root, lesson_id, parts[1], unquote(parts[2]), body)
        package = resolve_lesson_package(root, lesson_id)
        write_response: WorkbenchWriteResult = {
            "lesson": build_lesson_payload(root, package),
            "check": cast(
                WorkbenchCheckResult, serialize_json(build_check_payload(view, read_workbench_source(root, lesson_id)))
            ),
        }
        return write_response
    raise WorkbenchSourceNotFound("route not found")


def _mutation_error_response(root: Path, lesson_id: str, error: Exception) -> tuple[int, WorkbenchErrorResponse]:
    if isinstance(error, WorkbenchSourceNotFound):
        return 404, {"error": str(error)}
    if isinstance(error, InvalidLessonPackageError):
        return 503, {"error": "rebuild_failed"}
    if isinstance(error, WorkbenchGitError):
        return 409, {"error": _friendly_save_error(str(error)), "details": str(error)}
    if isinstance(error, ValueError):
        return _mutation_value_error_response(root, lesson_id, error)
    return 503, {"error": str(error)}


def _mutation_value_error_response(root: Path, lesson_id: str, error: ValueError) -> tuple[int, WorkbenchErrorResponse]:
    try:
        validate_slug(lesson_id)
    except ValueError:
        return 404, {"error": "lesson_not_found"}
    status = 404 if "lesson package not found" in str(error) else 409
    return status, {"error": str(error)}


def _friendly_save_error(details: str) -> str:
    """Translate known Git refusals into author-facing next steps."""
    if "detached HEAD" in details or "not a symbolic ref" in details or "symbolic-ref" in details:
        return "Saving drafts needs a checked-out branch; this workspace is on a detached HEAD."
    if "working branch" in details or "protected" in details:
        return "The workbench needs a working branch checked out; protected branches cannot be saved."
    if "repository root" in details:
        return "Open the workbench from the authoring repository root."
    if "unrelated paths are already staged" in details:
        return "Commit or unstage unrelated changes before saving this draft."
    if "HEAD moved" in details:
        return "The repository changed while saving. Reload the lesson and try again."
    if "no draft changes" in details:
        return "There are no draft changes to save."
    return "The draft could not be saved because Git rejected the operation."


def _write_source(root: Path, lesson_id: str, route: str, target: str, body: dict[str, Any]) -> AuthorPreview:
    if route == "files":
        file_request: FileWriteRequest = TypeAdapter(FileWriteRequest).validate_python(body)
        return write_workbench_file(root, lesson_id, target, file_request["text"])
    if route != "blocks":
        raise WorkbenchSourceNotFound("route not found")
    if "source" in body:
        source_request: BlockSourceWriteRequest = TypeAdapter(BlockSourceWriteRequest).validate_python(body)
        return write_workbench_block(root, lesson_id, target, source=source_request["source"])
    fields_request: BlockFieldsWriteRequest = TypeAdapter(BlockFieldsWriteRequest).validate_python(body)
    return write_workbench_block(root, lesson_id, target, fields=fields_request["fields"])


def _send_rebuilt_json(handler: BaseHTTPRequestHandler, build: Callable[[], WorkbenchRebuiltResponse]) -> None:
    """Send a freshly built payload, or a 503 error body if any part of the rebuild fails."""
    try:
        payload = build()
    except Exception:
        _send_error_json(handler, 503, {"error": "rebuild_failed"})
        return
    send_uncached_json(handler, 200, payload)


def _send_error_json(handler: BaseHTTPRequestHandler, status: int, payload: WorkbenchErrorResponse) -> None:
    """Send one error response covered by the workbench wire schema."""
    send_uncached_json(handler, status, payload)


def _parse_empty_request(body: object) -> EmptyRequest:
    """Validate a lifecycle request whose SPA body is empty."""
    return TypeAdapter(EmptyRequest).validate_python(body)


def _send_app_page(handler: BaseHTTPRequestHandler, *, status: int = 200) -> None:
    """Send the app's HTML shell, or the error page when the app build is missing."""
    try:
        body = _app_root().joinpath("index.html").read_bytes()
    except OSError:
        send_uncached_html(handler, 503, render_rebuild_error_document(K_WORKBENCH_SURFACE_NAME))
        return
    send_uncached_html(handler, status, body)


def _send_asset(handler: BaseHTTPRequestHandler, name: str) -> None:
    """Send one hashed build asset with immutable caching; 404 for anything else."""
    content_type = K_WEB_WORKBENCH_ASSET_CONTENT_TYPES.get(Path(name).suffix)
    if content_type is None or not K_WORKBENCH_ASSET_NAME.match(name):
        handler.send_error(404, "workbench asset not found")
        return
    try:
        body = _app_root().joinpath("assets", name).read_bytes()
    except OSError:
        handler.send_error(404, "workbench asset not found")
        return
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", K_WEB_WORKBENCH_ASSET_CACHE_CONTROL)
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)


def _is_lesson_page(path: str) -> bool:
    """Return whether ``path`` is a lesson page with a valid slug."""
    if not path.startswith(K_WORKBENCH_LESSON_ROUTE_PREFIX):
        return False
    try:
        validate_slug(path.removeprefix(K_WORKBENCH_LESSON_ROUTE_PREFIX))
    except ValueError:
        return False
    return True


def _is_unknown_app_page(path: str) -> bool:
    if path.removeprefix("/").partition("/")[0] in {"api", "assets", "static", "lessons"}:
        return False
    return K_WORKBENCH_PAGE_PATH.fullmatch(path) is not None


def _app_root() -> Traversable:
    """Return the packaged build of the workbench app."""
    return files("lesson_builder.web").joinpath("app")


__all__ = ["WorkbenchServer", "build_workbench_url", "create_workbench_server"]
