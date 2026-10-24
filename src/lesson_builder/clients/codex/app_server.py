"""Entry point: `CodexAppServerClient.run_turn` supervises structured turns over stdio JSON-RPC."""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from time import monotonic

try:
    import fcntl
except ImportError:  # pragma: no cover - supported POSIX deployments have fcntl
    fcntl = None  # type: ignore[assignment]

from lesson_builder.clients.codex import settings as codex_settings
from lesson_builder.clients.codex.auth_profile import CodexAuthSyncState
from lesson_builder.clients.codex.auth_profile import _forget_local_codex_profile
from lesson_builder.clients.codex.auth_profile import prepare_isolated_profile
from lesson_builder.clients.codex.auth_profile import restore_parent_auth
from lesson_builder.clients.codex.auth_profile import stop_auth_sync
from lesson_builder.clients.codex.auth_profile import watch_profile_auth
from lesson_builder.clients.codex.chat_settings import CodexChatSettings
from lesson_builder.clients.codex.types import CodexAppServerError
from lesson_builder.clients.codex.types import CodexTurnInterrupted
from lesson_builder.clients.codex.types import CodexTurnResult
from lesson_builder.clients.codex.types import DeltaHandler
from lesson_builder.clients.codex.types import JsonObject
from lesson_builder.clients.codex.types import ThreadHandler
from lesson_builder.clients.codex.types import TurnHandler

K_CODEX_CLIENT_NAME = "norsk-lesson-data-workbench"
K_CODEX_CLIENT_TITLE = "Norsk Lesson Factory Workbench"
K_CODEX_CLIENT_VERSION = "1.0.0"
K_CODEX_DENIED_DECISION = "decline"
K_CODEX_UNSUPPORTED_REQUEST_CODE = -32601


@dataclass
class _PendingRequest:
    event: threading.Event
    generation_id: int
    result: JsonObject | None = None
    error: str | None = None


@dataclass
class _ActiveTurn:
    thread_id: str
    on_delta: DeltaHandler
    event: threading.Event
    turn_id: str | None = None
    completed: JsonObject | None = None
    callback_error: Exception | None = None
    generation_id: int = 0


@dataclass
class _ProcessGeneration:
    identity: int
    process: subprocess.Popen[str]
    scratch_dir: Path
    pending: dict[int, _PendingRequest]
    active: dict[str, _ActiveTurn]
    parent_auth: Path
    profile_auth: Path
    auth_sync_state: CodexAuthSyncState
    reader: threading.Thread | None = None
    closing: bool = False
    stale: bool = False
    retired: bool = False
    turn_count: int = 0
    retired_event: threading.Event = field(default_factory=threading.Event)
    auth_sync_stop: threading.Event = field(default_factory=threading.Event)
    auth_sync_thread: threading.Thread | None = None


class CodexAppServerClient:
    """Own one Codex app-server process and multiplex its JSON-RPC stream."""

    def __init__(
        self,
        *,
        scratch_root: Path | None = None,
    ) -> None:
        self._scratch_root = scratch_root
        self._start_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._generation_counter = 0
        self._generation: _ProcessGeneration | None = None
        self._generations: dict[int, _ProcessGeneration] = {}
        self._request_id = 0
        self._thread_locks: dict[str, threading.Lock] = {}
        self._closed = False
        self._shutdown_deadline: float | None = None

    def run_turn(
        self,
        *,
        settings: CodexChatSettings,
        thread_id: str | None,
        prompt: str,
        output_schema: JsonObject,
        on_thread_id: ThreadHandler,
        on_turn_started: TurnHandler,
        on_delta: DeltaHandler,
    ) -> CodexTurnResult:
        """Start or resume a persistent thread and wait for its schema-constrained turn."""
        generation = self._acquire_generation()
        active_thread_id: str | None = None
        try:
            current_thread_id = (
                self._start_or_resume_thread(None, settings, generation) if thread_id is None else thread_id
            )
            lock = self._thread_lock(f"{generation.identity}:{current_thread_id}")
            with lock:
                current_thread_id = self._confirm_locked_thread_id(thread_id, current_thread_id, settings, generation)
                self._verify_thread_tool_isolation(generation, current_thread_id)
                on_thread_id(current_thread_id)
                active = _ActiveTurn(current_thread_id, on_delta, threading.Event(), generation_id=generation.identity)
                with self._state_lock:
                    if current_thread_id in generation.active:
                        raise CodexAppServerError("a Codex turn is already running in this thread")
                    generation.active[current_thread_id] = active
                    active_thread_id = current_thread_id
                return self._run_started_turn(
                    generation,
                    current_thread_id,
                    settings,
                    prompt,
                    output_schema,
                    active,
                    on_turn_started,
                )
        finally:
            with self._state_lock:
                if active_thread_id is not None:
                    generation.active.pop(active_thread_id, None)
                generation.turn_count -= 1
            self._retire_generation(generation)

    def interrupt(self, thread_id: str, turn_id: str, *, generation: _ProcessGeneration | None = None) -> None:
        """Interrupt one active Codex turn."""
        target = generation or self._generation_for_turn(thread_id, turn_id)
        self._request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, generation=target)

    def close(self, timeout_seconds: float | None = None) -> None:
        """Stop and reap the single app-server process owned by this client."""
        timeout = (
            codex_settings.K_CODEX_PROCESS_TERMINATION_TIMEOUT_SECONDS
            if timeout_seconds is None
            else max(0.0, timeout_seconds)
        )
        deadline = monotonic() + timeout
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._shutdown_deadline = deadline
            all_generations = tuple(self._generations.values())
            generations = tuple(generation for generation in all_generations if not generation.retired)
            retiring = tuple(generation for generation in all_generations if generation.retired)
            self._generation = None
            for generation in generations:
                generation.closing = True
                generation.retired = True
        for generation in generations:
            self._fail_pending(generation, "Codex app-server is shutting down")
            _stop_process(generation.process, deadline)
            stop_auth_sync(generation.auth_sync_stop, generation.auth_sync_thread)
            _restore_and_remove_auth_profile_after_process_exit(
                generation.process,
                generation.auth_sync_state.profile_dir,
                generation.parent_auth,
                generation.profile_auth,
                generation.auth_sync_state,
            )
            shutil.rmtree(generation.scratch_dir, ignore_errors=True)
            generation.retired_event.set()
        for generation in retiring:
            generation.retired_event.wait(timeout=max(0.0, deadline - monotonic()))

    def _run_started_turn(
        self,
        generation: _ProcessGeneration,
        thread_id: str,
        settings: CodexChatSettings,
        prompt: str,
        output_schema: JsonObject,
        active: _ActiveTurn,
        on_turn_started: TurnHandler,
    ) -> CodexTurnResult:
        try:
            response = self._request(
                "turn/start",
                {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt, "text_elements": []}],
                    "model": settings.model,
                    "effort": settings.effort,
                    "approvalPolicy": "never",
                    "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                    "outputSchema": _strict_output_schema(output_schema),
                },
                generation=generation,
            )
            turn = response.get("turn")
            if not isinstance(turn, dict) or not isinstance(turn.get("id"), str):
                raise CodexAppServerError("Codex did not return a turn id")
            active.turn_id = turn["id"]
            on_turn_started(thread_id, active.turn_id)
            if not active.event.wait(settings.timeout_seconds):
                raise CodexAppServerError("Codex turn timed out")
            if active.callback_error is not None:
                raise CodexAppServerError("could not persist streamed Codex output") from active.callback_error
            return _completed_turn_result(thread_id, active.turn_id, active, response)
        except BaseException:
            if active.turn_id is not None and active.completed is None:
                with suppress(Exception):
                    self.interrupt(thread_id, active.turn_id, generation=generation)
            raise

    def _confirm_locked_thread_id(
        self,
        requested_thread_id: str | None,
        lock_thread_id: str,
        settings: CodexChatSettings,
        generation: _ProcessGeneration,
    ) -> str:
        """Resume under the requested thread lock and reject a mismatched server identity."""
        if requested_thread_id is None:
            return lock_thread_id
        resumed_thread_id = self._start_or_resume_thread(requested_thread_id, settings, generation)
        if resumed_thread_id != lock_thread_id:
            raise CodexAppServerError("Codex resumed a different thread than requested")
        return resumed_thread_id

    def _acquire_generation(self) -> _ProcessGeneration:
        """Reserve one generation for a turn, waiting for stale turns to drain."""
        while True:
            generation = self._ensure_started()
            with self._state_lock:
                if self._generation is generation and not generation.stale and not generation.closing:
                    generation.turn_count += 1
                    return generation
            self._retire_generation(generation)
            generation.retired_event.wait()

    def _ensure_started(self) -> _ProcessGeneration:
        with self._start_lock:
            with self._state_lock:
                if self._closed:
                    raise CodexAppServerError("Codex app-server client is closed")
                generation = self._generation
            if (
                generation is not None
                and (generation.stale or generation.retired)
                and not generation.retired_event.is_set()
            ):
                return generation
            if generation is not None and generation.process.poll() is None:
                return generation
            previous_generation = generation
            scratch_dir = Path(tempfile.mkdtemp(prefix="nld-workbench-codex-", dir=self._scratch_root)).resolve()
            try:
                process, parent_auth, profile_auth, auth_sync_state = _start_codex_app_server_process(scratch_dir)
            except BaseException:
                shutil.rmtree(scratch_dir, ignore_errors=True)
                raise
            with self._state_lock:
                self._generation_counter += 1
                generation = _ProcessGeneration(
                    identity=self._generation_counter,
                    process=process,
                    scratch_dir=scratch_dir,
                    pending={},
                    active={},
                    parent_auth=parent_auth,
                    profile_auth=profile_auth,
                    auth_sync_state=auth_sync_state,
                )
                if self._closed:
                    generation.closing = True
                    shutdown_deadline = self._shutdown_deadline
                else:
                    self._generation = generation
                    self._generations[generation.identity] = generation
                    shutdown_deadline = None
            if generation.closing:
                deadline = shutdown_deadline or monotonic() + codex_settings.K_CODEX_PROCESS_TERMINATION_TIMEOUT_SECONDS
                _stop_process(process, deadline)
                _restore_and_remove_auth_profile_after_process_exit(
                    process,
                    generation.auth_sync_state.profile_dir,
                    generation.parent_auth,
                    generation.profile_auth,
                    generation.auth_sync_state,
                )
                shutil.rmtree(scratch_dir, ignore_errors=True)
                raise CodexAppServerError("Codex app-server client is closed")
            if previous_generation is not None:
                self._retire_generation(previous_generation)
            try:
                generation.reader = threading.Thread(
                    target=self._read_messages,
                    args=(generation,),
                    name=f"codex-app-server-reader-{generation.identity}",
                    daemon=True,
                )
                generation.reader.start()
                self._request(
                    "initialize",
                    {
                        "clientInfo": {
                            "name": K_CODEX_CLIENT_NAME,
                            "title": K_CODEX_CLIENT_TITLE,
                            "version": K_CODEX_CLIENT_VERSION,
                        },
                        "capabilities": {"experimentalApi": False, "requestAttestation": False},
                    },
                    generation=generation,
                )
                self._notify("initialized", generation=generation)
                self._verify_tool_isolation(generation)
                generation.auth_sync_thread = threading.Thread(
                    target=watch_profile_auth,
                    args=(
                        generation.parent_auth,
                        generation.profile_auth,
                        generation.auth_sync_stop,
                        generation.auth_sync_state,
                        lambda: self._mark_generation_stale(generation),
                        lambda: self._retire_generation(generation),
                    ),
                    name=f"codex-profile-auth-sync-{generation.identity}",
                    daemon=True,
                )
                generation.auth_sync_thread.start()
            except Exception:
                self._discard_generation(generation)
                raise
            return generation

    def _verify_tool_isolation(self, generation: _ProcessGeneration) -> None:
        config_response = self._request(
            "config/read",
            {"cwd": str(generation.scratch_dir), "includeLayers": True},
            generation=generation,
        )
        config = config_response.get("config")
        if not isinstance(config, dict):
            raise CodexAppServerError("Codex app-server did not report its effective configuration")
        mcp_servers = config.get("mcp_servers", {})
        if mcp_servers not in (None, {}):
            raise CodexAppServerError("Codex MCP servers are configured in the isolated profile")
        apps_config = config.get("apps")
        default_app_config = apps_config.get("_default") if isinstance(apps_config, dict) else None
        if not isinstance(default_app_config, dict) or default_app_config.get("enabled") is not False:
            raise CodexAppServerError("Codex apps are not disabled in the effective configuration")
        if _has_enabled_app_config(apps_config):
            raise CodexAppServerError("Codex apps are enabled in the effective configuration")

        self._verify_mcp_inventory(generation)
        self._verify_app_inventory(generation)
        self._verify_plugin_inventory(generation)

    def _verify_thread_tool_isolation(self, generation: _ProcessGeneration, thread_id: str) -> None:
        self._verify_mcp_inventory(generation, thread_id=thread_id)
        self._verify_app_inventory(generation, thread_id=thread_id)
        self._verify_plugin_inventory(generation)

    def _verify_app_inventory(self, generation: _ProcessGeneration, *, thread_id: str | None = None) -> None:
        params: JsonObject = {"forceRefresh": False}
        if thread_id is not None:
            params["threadId"] = thread_id
        apps_response = self._request("app/installed", params, generation=generation)
        apps = apps_response.get("apps")
        if not isinstance(apps, list) or any(
            not isinstance(app, dict)
            or not isinstance(app.get("enabled"), bool)
            or not isinstance(app.get("callable"), bool)
            or app["enabled"]
            or app["callable"]
            for app in apps
        ):
            raise CodexAppServerError("Codex app tools are available in the isolated profile")

    def _verify_plugin_inventory(self, generation: _ProcessGeneration) -> None:
        plugins_response = self._request(
            "plugin/installed",
            {"cwds": [str(generation.scratch_dir)]},
            generation=generation,
        )
        marketplaces = plugins_response.get("marketplaces")
        if not isinstance(marketplaces, list) or marketplaces:
            raise CodexAppServerError("Codex plugins are available in the isolated profile")

    def _verify_mcp_inventory(self, generation: _ProcessGeneration, *, thread_id: str | None = None) -> None:
        cursor: str | None = None
        seen_cursors: set[str] = set()
        while True:
            params: JsonObject = {"cursor": cursor, "detail": "full"}
            if thread_id is not None:
                params["threadId"] = thread_id
            response = self._request(
                "mcpServerStatus/list",
                params,
                generation=generation,
            )
            servers = response.get("data")
            if not isinstance(servers, list):
                raise CodexAppServerError("Codex app-server did not report its MCP inventory")
            if servers:
                raise CodexAppServerError("Codex MCP server tools are available in the isolated profile")
            next_cursor = response.get("nextCursor")
            if next_cursor is None:
                return
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor in seen_cursors:
                raise CodexAppServerError("Codex app-server returned an invalid MCP inventory cursor")
            seen_cursors.add(next_cursor)
            cursor = next_cursor

    def _discard_generation(self, generation: _ProcessGeneration) -> None:
        with self._state_lock:
            if self._generation is generation:
                self._generation = None
            self._generations.pop(generation.identity, None)
            generation.closing = True
            generation.retired = True
        _stop_process(generation.process, monotonic() + codex_settings.K_CODEX_PROCESS_TERMINATION_TIMEOUT_SECONDS)
        stop_auth_sync(generation.auth_sync_stop, generation.auth_sync_thread)
        _restore_and_remove_auth_profile_after_process_exit(
            generation.process,
            generation.auth_sync_state.profile_dir,
            generation.parent_auth,
            generation.profile_auth,
            generation.auth_sync_state,
        )
        shutil.rmtree(generation.scratch_dir, ignore_errors=True)
        generation.retired_event.set()

    def _start_or_resume_thread(
        self, thread_id: str | None, settings: CodexChatSettings, generation: _ProcessGeneration
    ) -> str:
        params: JsonObject = {
            "model": settings.model,
            "cwd": str(generation.scratch_dir),
            "approvalPolicy": "never",
            "sandbox": "read-only",
            "config": {"mcp_servers": {}},
        }
        if thread_id is None:
            params["ephemeral"] = False
            response = self._request("thread/start", params, generation=generation)
        else:
            params["threadId"] = thread_id
            response = self._request("thread/resume", params, generation=generation)
        thread = response.get("thread")
        identity = thread.get("id") if isinstance(thread, dict) else None
        if not isinstance(identity, str) or not identity:
            raise CodexAppServerError("Codex did not return a thread id")
        return identity

    def _request(self, method: str, params: JsonObject, *, generation: _ProcessGeneration | None = None) -> JsonObject:
        generation = generation or self._current_generation()
        process = generation.process
        if process is None or process.poll() is not None:
            raise CodexAppServerError("Codex app-server is not running")
        with self._state_lock:
            if generation.closing:
                raise CodexAppServerError("Codex app-server is shutting down")
            self._request_id += 1
            identity = self._request_id
            pending = _PendingRequest(threading.Event(), generation.identity)
            generation.pending[identity] = pending
        self._write({"jsonrpc": "2.0", "id": identity, "method": method, "params": params}, generation=generation)
        if not pending.event.wait(codex_settings.K_CODEX_RPC_TIMEOUT_SECONDS):
            with self._state_lock:
                if generation.pending.get(identity) is pending:
                    generation.pending.pop(identity, None)
            raise CodexAppServerError(f"Codex request timed out: {method}")
        if pending.error:
            raise CodexAppServerError(pending.error)
        return pending.result or {}

    def _notify(self, method: str, *, generation: _ProcessGeneration | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method}, generation=generation)

    def _write(self, message: JsonObject, *, generation: _ProcessGeneration | None = None) -> None:
        generation = generation or self._current_generation()
        process = generation.process
        if process is None or process.stdin is None:
            raise CodexAppServerError("Codex app-server stdin is unavailable")
        serialized = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        with self._write_lock:
            try:
                process.stdin.write(serialized + "\n")
                process.stdin.flush()
            except OSError as exc:
                raise CodexAppServerError("could not write to codex app-server") from exc

    def _read_messages(self, generation: _ProcessGeneration) -> None:
        process = generation.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._fail_pending(generation, "Codex app-server returned invalid JSON")
                continue
            if not isinstance(message, dict):
                continue
            self._handle_message(message, generation)
        with self._state_lock:
            generation.closing = True
            if self._generation is generation:
                self._generation = None
        self._fail_pending(generation, "Codex app-server exited")
        self._retire_generation(generation)

    def _handle_message(self, message: JsonObject, generation: _ProcessGeneration) -> None:
        if "id" in message and "method" in message:
            self._answer_server_request(generation, message)
            return
        if self._handle_response(generation, message):
            return
        self._handle_notification(generation, message)

    def _handle_response(self, generation: _ProcessGeneration, message: JsonObject) -> bool:
        identity = message.get("id")
        if not isinstance(identity, int):
            return False
        with self._state_lock:
            pending = generation.pending.pop(identity, None)
        if pending is not None and pending.generation_id == generation.identity:
            _resolve_pending_request(pending, message)
        return True

    def _handle_notification(self, generation: _ProcessGeneration, message: JsonObject) -> None:
        method = message.get("method")
        params = message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            return
        thread_id = params.get("threadId")
        if not isinstance(thread_id, str):
            return
        with self._state_lock:
            if generation.closing:
                return
            active = generation.active.get(thread_id)
        if active is None or active.generation_id != generation.identity:
            return
        if method == "item/agentMessage/delta":
            _handle_delta_notification(active, params)
        elif method == "turn/completed":
            _handle_completion_notification(active, params)

    def _answer_server_request(self, generation: _ProcessGeneration, request: JsonObject) -> None:
        identity = request.get("id")
        method = request.get("method")
        if not isinstance(identity, (int, str)) or not isinstance(method, str):
            return
        if method == "item/permissions/requestApproval":
            result: JsonObject = {
                "permissions": {"network": {"enabled": False}, "fileSystem": {"read": [], "write": [], "entries": []}},
                "scope": "turn",
            }
            self._write({"jsonrpc": "2.0", "id": identity, "result": result}, generation=generation)
        elif method.endswith("requestApproval") or method in {"applyPatchApproval", "execCommandApproval"}:
            self._write(
                {"jsonrpc": "2.0", "id": identity, "result": {"decision": K_CODEX_DENIED_DECISION}},
                generation=generation,
            )
        else:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": identity,
                    "error": {"code": K_CODEX_UNSUPPORTED_REQUEST_CODE, "message": "Unsupported server request"},
                },
                generation=generation,
            )

    def _fail_pending(self, generation: _ProcessGeneration, message: str) -> None:
        with self._state_lock:
            pending = tuple(generation.pending.values())
            generation.pending.clear()
            active = tuple(generation.active.values())
        for item in pending:
            item.error = message
            item.event.set()
        for turn in active:
            turn.completed = {"turn": {"id": turn.turn_id, "status": "failed", "error": {"message": message}}}
            turn.event.set()

    def _current_generation(self) -> _ProcessGeneration:
        with self._state_lock:
            generation = self._generation
        if generation is None:
            raise CodexAppServerError("Codex app-server is not running")
        return generation

    def _generation_for_turn(self, thread_id: str, turn_id: str) -> _ProcessGeneration:
        with self._state_lock:
            generation = next(
                (
                    item
                    for item in self._generations.values()
                    if (active := item.active.get(thread_id)) is not None and active.turn_id == turn_id
                ),
                self._generation,
            )
        if generation is None:
            raise CodexAppServerError("Codex app-server is not running")
        return generation

    def _mark_generation_stale(self, generation: _ProcessGeneration) -> None:
        """Mark a generation stale after syncing a new parent login into its profile."""
        with self._state_lock:
            if generation.retired:
                return
            generation.stale = True

    def _retire_generation(self, generation: _ProcessGeneration) -> None:
        retired = False
        with self._state_lock:
            has_work = bool(generation.pending or generation.active or generation.turn_count)
            should_retire = (
                generation is not self._generation
                or generation.stale
                or generation.closing
                or generation.process.poll() is not None
            )
            if not generation.retired and should_retire and not has_work:
                generation.closing = True
                generation.retired = True
                retired = True
        if retired:
            _stop_process(
                generation.process,
                monotonic() + codex_settings.K_CODEX_PROCESS_TERMINATION_TIMEOUT_SECONDS,
            )
            stop_auth_sync(generation.auth_sync_stop, generation.auth_sync_thread)
            _restore_and_remove_auth_profile_after_process_exit(
                generation.process,
                generation.auth_sync_state.profile_dir,
                generation.parent_auth,
                generation.profile_auth,
                generation.auth_sync_state,
            )
            shutil.rmtree(generation.scratch_dir, ignore_errors=True)
            with self._state_lock:
                if self._generation is generation:
                    self._generation = None
                self._generations.pop(generation.identity, None)
            generation.retired_event.set()

    def _thread_lock(self, identity: str) -> threading.Lock:
        with self._state_lock:
            return self._thread_locks.setdefault(identity, threading.Lock())


def _restore_and_remove_auth_profile(
    profile_dir: Path,
    parent_auth: Path,
    profile_auth: Path,
    state: CodexAuthSyncState,
) -> None:
    """Restore stopped-generation auth and remove its profile only after durable sync."""
    if restore_parent_auth(parent_auth, profile_auth, state):
        shutil.rmtree(profile_dir, ignore_errors=True)
    _forget_local_codex_profile(profile_dir)


def _restore_and_remove_auth_profile_after_process_exit(
    process: subprocess.Popen[str],
    profile_dir: Path,
    parent_auth: Path,
    profile_auth: Path,
    state: CodexAuthSyncState,
) -> None:
    """Wait for a killed process before restoring and removing its auth profile."""
    if process.poll() is not None:
        _restore_and_remove_auth_profile(profile_dir, parent_auth, profile_auth, state)
        return

    def wait_then_cleanup() -> None:
        process.wait()
        _restore_and_remove_auth_profile(profile_dir, parent_auth, profile_auth, state)

    threading.Thread(target=wait_then_cleanup, name="codex-auth-profile-cleanup", daemon=True).start()


def _stop_process(process: subprocess.Popen[str], deadline: float) -> None:
    if process.poll() is not None:
        return
    with suppress(ProcessLookupError):
        process.terminate()
    if _wait_for_process(process, deadline):
        return
    with suppress(ProcessLookupError):
        process.kill()
    if not _wait_for_process(process, deadline):
        threading.Thread(target=process.wait, name="codex-app-server-reaper", daemon=True).start()


def _wait_for_process(process: subprocess.Popen[str], deadline: float) -> bool:
    try:
        process.wait(timeout=max(0.0, deadline - monotonic()))
    except subprocess.TimeoutExpired:
        return False
    return True


def _resolve_pending_request(pending: _PendingRequest, message: JsonObject) -> None:
    error = message.get("error")
    if isinstance(error, dict):
        pending.error = str(error.get("message", "Codex request failed"))
    else:
        result = message.get("result")
        pending.result = result if isinstance(result, dict) else {}
    pending.event.set()


def _app_server_command() -> list[str]:
    command = ["codex", "app-server", "--stdio"]
    for feature in codex_settings.K_CODEX_APP_SERVER_DISABLED_FEATURES:
        command.extend(("--disable", feature))
    for override in codex_settings.K_CODEX_APP_SERVER_CONFIG_OVERRIDES:
        command.extend(("-c", override))
    return command


def _app_server_environment(profile_dir: Path) -> dict[str, str]:
    environment = {
        key: os.environ[key] for key in codex_settings.K_CODEX_APP_SERVER_ENVIRONMENT_KEYS if key in os.environ
    }
    environment.setdefault("PATH", os.defpath)
    environment["HOME"] = str(profile_dir)
    environment["CODEX_HOME"] = str(profile_dir)
    return environment


def _start_codex_app_server_process(
    scratch_dir: Path,
) -> tuple[subprocess.Popen[str], Path, Path, CodexAuthSyncState]:
    """Prepare one isolated profile and start its app-server process."""
    profile_dir: Path | None = None
    auth_sync_state: CodexAuthSyncState | None = None
    try:
        profile_dir, auth_sync_state = prepare_isolated_profile()
        parent_auth = auth_sync_state.parent_auth
        profile_auth = profile_dir / codex_settings.K_CODEX_AUTH_FILENAME
        process = subprocess.Popen(
            _app_server_command(),
            cwd=scratch_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=_app_server_environment(profile_dir),
        )
    except OSError as exc:
        if profile_dir is not None and auth_sync_state is not None:
            _restore_and_remove_auth_profile(
                profile_dir,
                auth_sync_state.parent_auth,
                profile_dir / codex_settings.K_CODEX_AUTH_FILENAME,
                auth_sync_state,
            )
        raise CodexAppServerError("could not start codex app-server") from exc
    except BaseException:
        if profile_dir is not None and auth_sync_state is not None:
            _restore_and_remove_auth_profile(
                profile_dir,
                auth_sync_state.parent_auth,
                profile_dir / codex_settings.K_CODEX_AUTH_FILENAME,
                auth_sync_state,
            )
        raise
    return process, parent_auth, profile_auth, auth_sync_state


def _has_enabled_app_config(value: object) -> bool:
    if isinstance(value, dict):
        return value.get("enabled") is True or any(_has_enabled_app_config(item) for item in value.values())
    if isinstance(value, list):
        return any(_has_enabled_app_config(item) for item in value)
    return False


def _handle_delta_notification(active: _ActiveTurn, params: JsonObject) -> None:
    delta = params.get("delta")
    if not isinstance(delta, str) or (active.turn_id is not None and params.get("turnId") != active.turn_id):
        return
    try:
        active.on_delta(delta)
    except Exception as exc:
        active.callback_error = exc
        active.event.set()


def _handle_completion_notification(active: _ActiveTurn, params: JsonObject) -> None:
    turn = params.get("turn")
    if isinstance(turn, dict) and (active.turn_id is None or turn.get("id") == active.turn_id):
        active.completed = params
        active.event.set()


def _completed_turn_result(thread_id: str, turn_id: str, active: _ActiveTurn, response: JsonObject) -> CodexTurnResult:
    completed = active.completed
    if completed is None:
        raise CodexAppServerError("Codex turn ended without a completion notification")
    final_turn = completed.get("turn")
    if not isinstance(final_turn, dict):
        raise CodexAppServerError("Codex completion notification omitted its turn")
    status = final_turn.get("status")
    if status == "interrupted":
        raise CodexTurnInterrupted("Codex turn interrupted")
    if status != "completed":
        detail = _turn_error_message(final_turn.get("error"))
        raise CodexAppServerError(detail or "Codex turn failed")
    text = _completed_text(final_turn) or _completed_text(response.get("turn"))
    if not text:
        raise CodexAppServerError("Codex completed without an assistant message")
    return CodexTurnResult(thread_id, turn_id, text)


def _completed_text(turn: object) -> str:
    if not isinstance(turn, dict) or not isinstance(turn.get("items"), list):
        return ""
    messages = [
        item.get("text", "") for item in turn["items"] if isinstance(item, dict) and item.get("type") == "agentMessage"
    ]
    return "\n".join(value for value in messages if isinstance(value, str))


def _strict_output_schema(schema: JsonObject) -> JsonObject:
    """Copy a Pydantic schema into the complete, closed form Codex structured output accepts."""
    result = copy.deepcopy(schema)
    _rewrite_one_of_unions(result)
    _close_schema_object_nodes(result)
    return result


def _rewrite_one_of_unions(value: object) -> None:
    """Emit unions with ``anyOf`` and without ``discriminator``; strict output rejects both."""
    if isinstance(value, dict):
        one_of = value.pop("oneOf", None)
        if isinstance(one_of, list):
            branches = value.get("anyOf")
            value["anyOf"] = branches + one_of if isinstance(branches, list) else one_of
        value.pop("discriminator", None)
        for nested in value.values():
            _rewrite_one_of_unions(nested)
    elif isinstance(value, list):
        for nested in value:
            _rewrite_one_of_unions(nested)


def _close_schema_object_nodes(value: object) -> None:
    """Require every declared property and forbid undeclared ones on every object node."""
    if isinstance(value, dict):
        properties = value.get("properties")
        if isinstance(properties, dict):
            value["required"] = list(properties)
            value["additionalProperties"] = False
        for nested in value.values():
            _close_schema_object_nodes(nested)
    elif isinstance(value, list):
        for nested in value:
            _close_schema_object_nodes(nested)


def _turn_error_message(error: object) -> str:
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str):
            return message
    return ""


__all__ = ["CodexAppServerClient"]
