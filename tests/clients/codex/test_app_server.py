"""Entry point: public Codex app-server client behavior tests."""

from __future__ import annotations

import errno
import json
import os
from base64 import urlsafe_b64encode
from pathlib import Path
from threading import Barrier
from threading import Event
from threading import Lock
from threading import Thread
from threading import current_thread

import pytest
from pydantic import BaseModel

from lesson_builder.application.operations.workbench_records import BlockExplanationResponse
from lesson_builder.application.operations.workbench_records import ChatResponse
from lesson_builder.application.operations.workbench_records import CurriculumResponse
from lesson_builder.clients.codex import app_server
from lesson_builder.clients.codex import auth_profile
from lesson_builder.clients.codex import settings as codex_settings
from lesson_builder.clients.codex.app_server import CodexAppServerClient
from lesson_builder.clients.codex.chat_settings import CodexChatSettings
from lesson_builder.clients.codex.types import CodexAppServerError
from lesson_builder.clients.codex.types import CodexTurnResult
from tests.clients.codex.fakes import FakeCodexAppServerProcess
from tests.settings import K_TEST_HANG_GUARD_SECONDS
from tests.settings import wait_for_test_condition


@pytest.fixture(autouse=True)
def isolated_codex_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))


@pytest.fixture
def codex_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    process = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process)
    client = CodexAppServerClient(scratch_root=tmp_path)
    yield client, process
    client.close()


def test_codex_app_server_given_parent_profile_with_tools_expect_isolated_profile_and_turn(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "parent-codex-home"
    parent_home.mkdir()
    (parent_home / "config.toml").write_text('[mcp_servers.leak]\ncommand = "leak"\n', encoding="utf-8")
    (parent_home / "auth.json").write_text(_codex_auth_content("private"), encoding="utf-8")
    (parent_home / "plugins").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(parent_home))

    process = FakeCodexAppServerProcess()
    launch_calls: list[tuple[list[str], dict[str, object]]] = []

    def popen(args: list[str], **kwargs: object) -> FakeCodexAppServerProcess:
        launch_calls.append((args, kwargs))
        return process

    monkeypatch.setattr(app_server.subprocess, "Popen", popen)
    client = CodexAppServerClient(scratch_root=tmp_path)

    result = client.run_turn(
        settings=CodexChatSettings(model="gpt-6-luna", effort="low"),
        thread_id=None,
        prompt="Reply",
        output_schema={},
        on_thread_id=lambda _thread_id: None,
        on_turn_started=lambda thread_id, turn_id: process.complete_turn(thread_id, turn_id, "{}"),
        on_delta=lambda _delta: None,
    )

    args, kwargs = launch_calls[0]
    environment = kwargs.get("env")
    assert isinstance(environment, dict)
    child_home = Path(str(environment["CODEX_HOME"]))
    methods = [request["method"] for request in process.requests if isinstance(request.get("method"), str)]
    assert args[-4:] == ["-c", "mcp_servers={}", "-c", "apps._default.enabled=false"]
    assert args[args.index("--disable") + 1] == "apps"
    assert args[args.index("--disable", args.index("--disable") + 1) + 1] == "plugins"
    assert Path(str(environment["HOME"])) != parent_home
    assert set(environment) <= set(codex_settings.K_CODEX_APP_SERVER_ENVIRONMENT_KEYS) | {"HOME", "CODEX_HOME"}
    assert child_home != parent_home
    assert sorted(path.name for path in child_home.iterdir()) == ["auth.json"]
    assert json.loads((child_home / "auth.json").read_text(encoding="utf-8")) == {"tokens": {"access_token": "private"}}
    assert methods[:9] == [
        "initialize",
        "initialized",
        "config/read",
        "mcpServerStatus/list",
        "app/installed",
        "plugin/installed",
        "thread/start",
        "mcpServerStatus/list",
        "app/installed",
    ]
    assert "turn/start" in methods
    assert result.text == "{}"
    client.close()
    assert not child_home.exists()


@pytest.mark.parametrize(
    ("inventory_method", "inventory_response", "error"),
    [
        (
            "mcpServerStatus/list",
            {"data": [{"name": "leaked-server", "tools": {"write_file": {}}}], "nextCursor": None},
            "MCP server tools are available",
        ),
        (
            "app/installed",
            {"apps": [{"id": "connector", "enabled": True, "callable": True}]},
            "Codex app tools are available",
        ),
        (
            "plugin/installed",
            {"marketplaces": [{"name": "marketplace", "plugins": [{"installed": True, "enabled": True}]}]},
            "Codex plugins are available",
        ),
    ],
)
def test_codex_app_server_given_startup_reports_available_tools_expect_turn_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    inventory_method: str,
    inventory_response: dict[str, object],
    error: str,
) -> None:
    process = FakeCodexAppServerProcess(
        response_handler=lambda method, _params: inventory_response if method == inventory_method else None
    )
    _install_app_server_process(monkeypatch, process)
    client = CodexAppServerClient(scratch_root=tmp_path)

    with pytest.raises(CodexAppServerError, match=error):
        _run_turn(client, process)

    assert "turn/start" not in _request_methods(process)
    client.close()


def test_codex_turn_given_thread_has_mcp_tools_expect_turn_refused(codex_client) -> None:
    client, process = codex_client

    def response(method: str, params: dict[str, object]) -> dict[str, object] | None:
        if method == "mcpServerStatus/list" and params.get("threadId") == "thread-1":
            return {"data": [{"name": "thread-server", "tools": {"write_file": {}}}], "nextCursor": None}
        return None

    process.response_handler = response

    with pytest.raises(CodexAppServerError, match="MCP server tools are available"):
        _run_turn(client, process)

    assert _request_methods(process).count("turn/start") == 0


def test_codex_turn_given_delta_and_completion_notifications_expect_public_result_and_stream(
    codex_client,
) -> None:
    client, process = codex_client
    streamed: list[str] = []

    result = client.run_turn(
        settings=CodexChatSettings(model="gpt-6-luna", effort="low"),
        thread_id=None,
        prompt="Reply",
        output_schema={},
        on_thread_id=lambda _thread_id: None,
        on_turn_started=lambda thread_id, turn_id: _send_codex_completion(process, thread_id, turn_id),
        on_delta=streamed.append,
    )

    assert result == CodexTurnResult("thread-1", "turn-1", "{}")
    assert streamed == ['{"reply":"Hello']


def test_codex_turn_given_turn_start_callback_fails_expect_interrupt_before_retry(codex_client) -> None:
    client, process = codex_client

    with pytest.raises(RuntimeError, match="could not persist turn start"):
        client.run_turn(
            settings=CodexChatSettings(model="gpt-6-luna", effort="low", timeout_seconds=1),
            thread_id=None,
            prompt="Reply",
            output_schema={},
            on_thread_id=lambda _thread_id: None,
            on_turn_started=_raise_codex_callback_error,
            on_delta=lambda _delta: None,
        )

    result = _run_turn(client, process)

    interrupts = [request for request in process.requests if request.get("method") == "turn/interrupt"]
    assert result.text == "{}"
    assert [request["params"] for request in interrupts] == [{"threadId": "thread-1", "turnId": "turn-1"}]


def test_codex_turn_given_delta_callback_fails_expect_interrupt_before_retry(codex_client) -> None:
    client, process = codex_client

    with pytest.raises(CodexAppServerError, match="could not persist streamed Codex output"):
        client.run_turn(
            settings=CodexChatSettings(model="gpt-6-luna", effort="low", timeout_seconds=1),
            thread_id=None,
            prompt="Reply",
            output_schema={},
            on_thread_id=lambda _thread_id: None,
            on_turn_started=lambda thread_id, turn_id: process.send_message(
                {
                    "method": "item/agentMessage/delta",
                    "params": {"threadId": thread_id, "turnId": turn_id, "delta": "partial"},
                }
            ),
            on_delta=lambda _delta: (_ for _ in ()).throw(RuntimeError("could not persist delta")),
        )

    result = _run_turn(client, process)

    interrupts = [request for request in process.requests if request.get("method") == "turn/interrupt"]
    assert result.text == "{}"
    assert [request["params"] for request in interrupts] == [{"threadId": "thread-1", "turnId": "turn-1"}]


@pytest.mark.parametrize(
    ("request_method", "expected_result"),
    [
        (
            "item/permissions/requestApproval",
            {
                "permissions": {"network": {"enabled": False}, "fileSystem": {"read": [], "write": [], "entries": []}},
                "scope": "turn",
            },
        ),
        ("item/commandExecution/requestApproval", {"decision": "decline"}),
    ],
)
def test_codex_turn_given_server_requests_additional_permissions_expect_permissions_denied(
    codex_client,
    request_method: str,
    expected_result: dict[str, object],
) -> None:
    client, process = codex_client

    def on_turn_started(thread_id: str, turn_id: str) -> None:
        process.send_server_request(7, request_method, {})
        assert process.client_message_received.wait(K_TEST_HANG_GUARD_SECONDS)
        process.complete_turn(thread_id, turn_id, "{}")

    _run_turn(client, process, on_turn_started=on_turn_started)

    assert process.client_messages == [{"jsonrpc": "2.0", "id": 7, "result": expected_result}]


def test_codex_turn_given_rpc_startup_exceeds_configured_timeout_expect_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
    codex_client,
) -> None:
    client, process = codex_client
    process.suppressed_methods.add("initialize")
    monkeypatch.setattr(codex_settings, "K_CODEX_RPC_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(CodexAppServerError, match="Codex request timed out: initialize"):
        _run_turn(client, process)


def test_codex_client_given_initialization_rpc_pending_expect_close_stops_process(
    codex_client,
) -> None:
    client, process = codex_client
    process.suppressed_methods.add("initialize")
    failures: list[Exception] = []

    def start_turn() -> None:
        try:
            _run_turn(client, process)
        except Exception as exc:
            failures.append(exc)

    worker = Thread(target=start_turn)
    worker.start()
    assert process.request_received.wait(K_TEST_HANG_GUARD_SECONDS)

    client.close()
    worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert not worker.is_alive()
    assert process.termination_requested.is_set()
    assert len(failures) == 1
    assert str(failures[0]) == "Codex app-server is shutting down"


def test_codex_client_given_process_ignores_terminate_expect_kill_within_budget(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = FakeCodexAppServerProcess(ignore_terminate=True)
    _install_app_server_process(monkeypatch, process)
    client = CodexAppServerClient(scratch_root=tmp_path)

    _run_turn(client, process)
    client.close(timeout_seconds=0.05)

    assert process.termination_requested.is_set()
    assert process.killed.is_set()


def test_codex_shutdown_given_writeback_failure_expect_profile_recovery_copy_retained(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("baseline"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setattr(codex_settings, "K_CODEX_AUTH_SYNC_POLL_SECONDS", 30)
    process = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process)
    client = CodexAppServerClient(scratch_root=tmp_path)
    original_replace = os.replace

    def fail_parent_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
        if Path(destination) == parent_auth:
            raise OSError(errno.ENOSPC, "synthetic auth write failure")
        original_replace(source, destination)

    monkeypatch.setattr(auth_profile.os, "replace", fail_parent_replace)
    _run_turn(client, process)
    generation = client._generation
    assert generation is not None
    generation.profile_auth.write_text(_codex_auth_content("valid-child-refresh"), encoding="utf-8")
    profile_auth = generation.profile_auth

    client.close()

    assert profile_auth.exists()
    assert profile_auth.read_text(encoding="utf-8") == _codex_auth_content("valid-child-refresh")


def test_codex_client_given_replacement_process_and_old_reader_eof_expect_new_turn_survives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process_a = FakeCodexAppServerProcess()
    process_b = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process_a, process_b)
    client = CodexAppServerClient(scratch_root=tmp_path)
    callback_entered = Event()
    release_callback = Event()
    old_turn_started = Event()
    old_failures: list[Exception] = []

    def hold_callback(_delta: str) -> None:
        callback_entered.set()
        release_callback.wait(K_TEST_HANG_GUARD_SECONDS)

    def run_old_turn() -> None:
        try:
            client.run_turn(
                settings=CodexChatSettings(model="gpt-6-luna", effort="low"),
                thread_id=None,
                prompt="Old turn",
                output_schema={},
                on_thread_id=lambda _thread_id: None,
                on_turn_started=lambda _thread_id, _turn_id: old_turn_started.set(),
                on_delta=hold_callback,
            )
        except Exception as exc:
            old_failures.append(exc)

    old_worker = Thread(target=run_old_turn)
    old_worker.start()
    assert old_turn_started.wait(K_TEST_HANG_GUARD_SECONDS)
    process_a.send_message(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": "thread-1", "turnId": "turn-1", "delta": "partial"},
        }
    )
    assert callback_entered.wait(K_TEST_HANG_GUARD_SECONDS)
    process_a.exit()

    new_result = client.run_turn(
        settings=CodexChatSettings(model="gpt-6-luna", effort="low"),
        thread_id=None,
        prompt="New turn",
        output_schema={},
        on_thread_id=lambda _thread_id: None,
        on_turn_started=lambda thread_id, turn_id: process_b.complete_turn(thread_id, turn_id, "new"),
        on_delta=lambda _delta: None,
    )
    release_callback.set()
    old_worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)
    client.close()

    assert not old_worker.is_alive()
    assert new_result.text == "new"
    assert len(old_failures) == 1
    assert isinstance(old_failures[0], CodexAppServerError)


def test_codex_client_given_restart_after_profile_baseline_and_parent_refresh_expect_parent_auth_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("A"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    process_a = FakeCodexAppServerProcess()
    process_b = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process_a, process_b)
    client = CodexAppServerClient(scratch_root=tmp_path)

    _run_turn(client, process_a)
    profile_auth = _current_profile_auth(parent_home)
    restarted_profile_auths: list[Path] = []
    parent_auth.write_text(_codex_auth_content("B"), encoding="utf-8")
    baseline_mtime_ns = profile_auth.stat().st_mtime_ns + 2_000_000_000
    os.utime(parent_auth, ns=(baseline_mtime_ns, baseline_mtime_ns))
    process_a.exit()

    parent_refresh = _codex_auth_content("C")

    def refresh_parent_during_restarted_turn(thread_id: str, turn_id: str) -> None:
        restarted_profile_auth = _current_profile_auth(parent_home)
        restarted_profile_auths.append(restarted_profile_auth)
        tied_mtime_ns = restarted_profile_auth.stat().st_mtime_ns
        parent_auth.write_text(parent_refresh, encoding="utf-8")
        os.utime(parent_auth, ns=(tied_mtime_ns, tied_mtime_ns))
        assert wait_for_test_condition(lambda: restarted_profile_auth.read_text(encoding="utf-8") == parent_refresh)
        process_b.complete_turn(thread_id, turn_id, "{}")

    _run_turn(client, process_b, on_turn_started=refresh_parent_during_restarted_turn)
    client.close()

    assert parent_auth.read_text(encoding="utf-8") == parent_refresh
    assert not profile_auth.exists()
    assert not restarted_profile_auths[0].exists()
    assert _generation_profile_auths(parent_home) == []


def test_codex_turn_given_selected_model_effort_and_schema_expect_turn_payload_constrained(
    codex_client,
) -> None:
    client, process = codex_client
    settings = CodexChatSettings(model="gpt-6-luna", effort="low")

    _run_turn(client, process, settings=settings, output_schema=ChatResponse.model_json_schema())

    payload = process.turn_start_payloads[0]
    schema = payload["outputSchema"]
    assert payload["model"] == "gpt-6-luna"
    assert payload["effort"] == "low"
    assert payload["approvalPolicy"] == "never"
    assert payload["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert isinstance(schema, dict)
    assert schema["required"] == ["reply", "edits", "regenerate"]
    assert schema["$defs"]["ProposedEdit"]["required"] == [
        "key",
        "after_source",
        "note",
        "change_summary",
        "translations",
        "answer_impact",
        "uncertainty",
    ]


@pytest.mark.parametrize(
    ("response_type", "expected_keys"),
    [
        (BlockExplanationResponse, ["item_tested", "translations"]),
        (ChatResponse, ["edits", "regenerate", "reply"]),
        (CurriculumResponse, ["proposed_edit", "reply"]),
    ],
)
def test_codex_turn_given_workbench_response_models_expect_closed_output_schema(
    codex_client,
    response_type: type[BaseModel],
    expected_keys: list[str],
) -> None:
    client, process = codex_client

    _run_turn(client, process, output_schema=response_type.model_json_schema())

    schema = process.turn_start_payloads[0]["outputSchema"]
    object_nodes = _object_nodes_with_properties(schema)
    assert isinstance(schema, dict)
    assert sorted(schema["required"]) == sorted(expected_keys)
    assert object_nodes
    for node in object_nodes:
        assert node["additionalProperties"] is False
        assert sorted(node["required"]) == sorted(node["properties"])
    assert _schema_keyword_values(schema, "oneOf") == []
    assert _schema_keyword_values(schema, "discriminator") == []


def test_codex_turn_given_discriminated_union_schema_expect_any_of_in_start_payload(codex_client) -> None:
    client, process = codex_client
    source: dict[str, object] = {
        "$defs": {
            "Reorder": {
                "type": "object",
                "properties": {
                    "kind": {"const": "reorder"},
                    "lesson_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "lesson_ids"],
            },
            "Remove": {
                "type": "object",
                "properties": {
                    "kind": {"const": "remove"},
                    "lesson_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "lesson_ids"],
            },
        },
        "oneOf": [{"$ref": "#/$defs/Reorder"}, {"$ref": "#/$defs/Remove"}],
        "discriminator": {
            "propertyName": "kind",
            "mapping": {"reorder": "#/$defs/Reorder", "remove": "#/$defs/Remove"},
        },
    }

    _run_turn(client, process, output_schema=source)

    schema = process.turn_start_payloads[0]["outputSchema"]
    assert schema["anyOf"] == [{"$ref": "#/$defs/Reorder"}, {"$ref": "#/$defs/Remove"}]
    assert _schema_keyword_values(schema, "oneOf") == []
    assert _schema_keyword_values(schema, "discriminator") == []
    assert "oneOf" in source
    assert "discriminator" in source


def test_codex_client_given_fresh_threads_start_concurrently_expect_independent_turns(codex_client) -> None:
    client, process = codex_client
    both_turns_started = Barrier(2)
    results: list[CodexTurnResult] = []
    failures: list[Exception] = []

    def run_turn() -> None:
        try:
            result = client.run_turn(
                settings=CodexChatSettings(model="gpt-6-luna", effort="low"),
                thread_id=None,
                prompt="Reply",
                output_schema={},
                on_thread_id=lambda _thread_id: None,
                on_turn_started=lambda thread_id, turn_id: _complete_after_barrier(
                    process,
                    thread_id,
                    turn_id,
                    both_turns_started,
                ),
                on_delta=lambda _delta: None,
            )
            results.append(result)
        except Exception as exc:
            failures.append(exc)

    workers = [Thread(target=run_turn) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert all(not worker.is_alive() for worker in workers)
    assert failures == []
    assert len(results) == 2
    assert _request_methods(process).count("turn/start") == 2


def test_codex_client_given_same_thread_turns_overlap_expect_turns_serialized(
    monkeypatch: pytest.MonkeyPatch,
    codex_client,
) -> None:
    client, process = codex_client
    first_turn_started = Event()
    release_first_turn = Event()
    second_turn_started = Event()
    second_turn_blocked_on_lock = Event()
    failures: list[Exception] = []
    results: list[CodexTurnResult] = []

    def response(method: str, params: dict[str, object]) -> dict[str, object] | None:
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        return None

    process.response_handler = response

    turn_lock = _TrackingThreadLock(second_turn_blocked_on_lock, "serialized-second-turn")
    monkeypatch.setattr(client, "_thread_lock", lambda _identity: turn_lock)

    def first_started(thread_id: str, turn_id: str) -> None:
        first_turn_started.set()
        release_first_turn.wait(K_TEST_HANG_GUARD_SECONDS)
        process.complete_turn(thread_id, turn_id, "first")

    def run_turn(on_start) -> None:
        try:
            results.append(
                client.run_turn(
                    settings=CodexChatSettings(model="gpt-6-luna", effort="low"),
                    thread_id="shared-thread",
                    prompt="Reply",
                    output_schema={},
                    on_thread_id=lambda _thread_id: None,
                    on_turn_started=on_start,
                    on_delta=lambda _delta: None,
                )
            )
        except Exception as exc:
            failures.append(exc)

    first_worker = Thread(target=run_turn, args=(first_started,))
    first_worker.start()
    assert first_turn_started.wait(K_TEST_HANG_GUARD_SECONDS)

    def second_started(thread_id: str, turn_id: str) -> None:
        second_turn_started.set()
        process.complete_turn(thread_id, turn_id, "second")

    second_worker = Thread(target=run_turn, args=(second_started,), name="serialized-second-turn")
    second_worker.start()
    assert second_turn_blocked_on_lock.wait(K_TEST_HANG_GUARD_SECONDS)
    assert not second_turn_started.is_set()
    release_first_turn.set()
    first_worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)
    second_worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)
    client.close()

    assert not first_worker.is_alive()
    assert not second_worker.is_alive()
    assert failures == []
    assert len(results) == 2
    assert _request_methods(process).count("turn/start") == 2


def test_codex_app_server_given_parent_auth_newer_than_profile_expect_parent_auth_copied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / "auth.json"
    parent_auth.write_text(_codex_auth_content("parent-current"), encoding="utf-8")
    profile_home = parent_home / codex_settings.K_CODEX_ISOLATED_PROFILE_DIRECTORY
    profile_home.mkdir()
    profile_auth = profile_home / codex_settings.K_CODEX_AUTH_FILENAME
    profile_auth.write_text(_codex_auth_content("profile-stale"), encoding="utf-8")
    older_mtime_ns = parent_auth.stat().st_mtime_ns - 10_000_000_000
    os.utime(profile_auth, ns=(older_mtime_ns, older_mtime_ns))
    monkeypatch.setenv("CODEX_HOME", str(parent_home))

    process = FakeCodexAppServerProcess()
    started_profile_auth: list[dict[str, object]] = []

    def popen(_args: list[str], **kwargs: object) -> FakeCodexAppServerProcess:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        started_auth = Path(str(environment["CODEX_HOME"])) / "auth.json"
        started_profile_auth.append(json.loads(started_auth.read_text(encoding="utf-8")))
        return process

    monkeypatch.setattr(app_server.subprocess, "Popen", popen)
    client = CodexAppServerClient(scratch_root=tmp_path)

    _run_turn(client, process)
    client.close()

    assert started_profile_auth == [{"tokens": {"access_token": "parent-current"}}]


def test_codex_app_server_given_parent_relogin_while_idle_expect_next_turn_starts_with_new_profile(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    initial_auth = _codex_auth_content("initial-login")
    relogin_auth = _codex_auth_content("new-login")
    parent_auth.write_text(initial_auth, encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setattr(codex_settings, "K_CODEX_AUTH_SYNC_POLL_SECONDS", 0.01)
    process_a = FakeCodexAppServerProcess()
    process_b = FakeCodexAppServerProcess()
    started_auth: list[str] = []
    remaining = iter((process_a, process_b))

    def popen(_args: list[str], **kwargs: object) -> FakeCodexAppServerProcess:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        profile_auth = Path(str(environment["CODEX_HOME"])) / codex_settings.K_CODEX_AUTH_FILENAME
        started_auth.append(profile_auth.read_text(encoding="utf-8"))
        return next(remaining)

    monkeypatch.setattr(app_server.subprocess, "Popen", popen)
    client = CodexAppServerClient(scratch_root=tmp_path)
    _run_turn(client, process_a)

    parent_auth.write_text(relogin_auth, encoding="utf-8")
    assert wait_for_test_condition(lambda: process_a.poll() is not None)
    was_retired = process_a.poll() is not None
    if not was_retired:
        client.close()
    assert was_retired

    result = _run_turn(
        client,
        process_b,
        on_turn_started=lambda thread_id, turn_id: process_b.complete_turn(thread_id, turn_id, "new"),
    )
    client.close()

    assert result.text == "new"
    assert started_auth == [initial_auth, relogin_auth]


def test_codex_app_server_given_parent_relogin_during_turn_expect_old_turn_then_new_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("initial-login"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setattr(codex_settings, "K_CODEX_AUTH_SYNC_POLL_SECONDS", 0.01)
    process_a = FakeCodexAppServerProcess()
    process_b = FakeCodexAppServerProcess()
    started_auth: list[str] = []
    remaining = iter((process_a, process_b))

    def popen(_args: list[str], **kwargs: object) -> FakeCodexAppServerProcess:
        environment = kwargs["env"]
        assert isinstance(environment, dict)
        profile_auth = Path(str(environment["CODEX_HOME"])) / codex_settings.K_CODEX_AUTH_FILENAME
        started_auth.append(profile_auth.read_text(encoding="utf-8"))
        return next(remaining)

    monkeypatch.setattr(app_server.subprocess, "Popen", popen)
    client = CodexAppServerClient(scratch_root=tmp_path)
    turn_started = Event()
    active_turn: list[tuple[str, str]] = []
    results: list[CodexTurnResult] = []
    failures: list[Exception] = []

    def track_turn(thread_id: str, turn_id: str) -> None:
        active_turn.append((thread_id, turn_id))
        turn_started.set()

    def run_old_turn() -> None:
        try:
            results.append(
                client.run_turn(
                    settings=CodexChatSettings(model="gpt-6-luna", effort="low"),
                    thread_id=None,
                    prompt="Old turn",
                    output_schema={},
                    on_thread_id=lambda _thread_id: None,
                    on_turn_started=track_turn,
                    on_delta=lambda _delta: None,
                )
            )
        except Exception as exc:
            failures.append(exc)

    worker = Thread(target=run_old_turn)
    worker.start()
    assert turn_started.wait(K_TEST_HANG_GUARD_SECONDS)
    profile_auth = _current_profile_auth(parent_home)
    relogin_auth = _codex_auth_content("new-login")
    parent_auth.write_text(relogin_auth, encoding="utf-8")
    assert wait_for_test_condition(lambda: profile_auth.read_text(encoding="utf-8") == relogin_auth)
    assert process_a.poll() is None

    process_a.complete_turn(*active_turn[0], "old")
    worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)
    assert not worker.is_alive()
    assert failures == []
    assert results[0].text == "old"
    was_retired = process_a.poll() is not None
    if not was_retired:
        client.close()
    assert was_retired

    result = _run_turn(
        client,
        process_b,
        on_turn_started=lambda thread_id, turn_id: process_b.complete_turn(thread_id, turn_id, "new"),
    )
    client.close()

    assert result.text == "new"
    assert started_auth == [_codex_auth_content("initial-login"), relogin_auth]


def test_codex_app_server_given_profile_refresh_during_turn_expect_parent_auth_updated_before_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("initial"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    process = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process)
    client = CodexAppServerClient(scratch_root=tmp_path)
    refreshed = _codex_auth_content("rotated")
    original_inode = parent_auth.stat().st_ino

    def refresh_profile_during_turn(thread_id: str, turn_id: str) -> None:
        profile_auth = _current_profile_auth(parent_home)
        profile_auth.write_text(refreshed, encoding="utf-8")
        # A coarse kernel clock can leave the refreshed profile tied with the parent's
        # preserved modification time; force the tie so the propagation check is deterministic.
        tied_mtime_ns = parent_auth.stat().st_mtime_ns
        os.utime(profile_auth, ns=(tied_mtime_ns, tied_mtime_ns))
        assert wait_for_test_condition(lambda: parent_auth.read_text(encoding="utf-8") == refreshed)
        process.complete_turn(thread_id, turn_id, "{}")

    result = client.run_turn(
        settings=CodexChatSettings(model="gpt-6-luna", effort="low"),
        thread_id=None,
        prompt="Reply",
        output_schema={},
        on_thread_id=lambda _thread_id: None,
        on_turn_started=refresh_profile_during_turn,
        on_delta=lambda _delta: None,
    )
    next_result = _run_turn(
        client,
        process,
        on_turn_started=lambda thread_id, turn_id: process.complete_turn(thread_id, turn_id, "same-process"),
    )
    client.close()

    assert result.text == "{}"
    assert next_result.text == "same-process"
    assert _request_methods(process).count("turn/start") == 2
    assert parent_auth.read_text(encoding="utf-8") == refreshed
    assert parent_auth.stat().st_mode & 0o777 == 0o600
    assert parent_auth.stat().st_ino != original_inode


def test_codex_app_server_given_retired_generation_refreshes_old_profile_expect_parent_relogin_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("initial-login"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setattr(codex_settings, "K_CODEX_AUTH_SYNC_POLL_SECONDS", 0.01)
    process_a = FakeCodexAppServerProcess()
    process_b = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process_a, process_b)
    client = CodexAppServerClient(scratch_root=tmp_path)
    turn_started = Event()
    active_turn: list[tuple[str, str]] = []
    results: list[CodexTurnResult] = []
    failures: list[Exception] = []

    def track_turn(thread_id: str, turn_id: str) -> None:
        active_turn.append((thread_id, turn_id))
        turn_started.set()

    def run_old_turn() -> None:
        try:
            results.append(
                client.run_turn(
                    settings=CodexChatSettings(model="gpt-6-luna", effort="low"),
                    thread_id=None,
                    prompt="Old turn",
                    output_schema={},
                    on_thread_id=lambda _thread_id: None,
                    on_turn_started=track_turn,
                    on_delta=lambda _delta: None,
                )
            )
        except Exception as exc:
            failures.append(exc)

    worker = Thread(target=run_old_turn)
    worker.start()
    assert turn_started.wait(K_TEST_HANG_GUARD_SECONDS)
    profile_auth = _current_profile_auth(parent_home)
    relogin_auth = _codex_auth_content("new-login")
    parent_auth.write_text(relogin_auth, encoding="utf-8")
    assert wait_for_test_condition(lambda: profile_auth.read_text(encoding="utf-8") == relogin_auth)
    generation = client._generation
    assert generation is not None
    assert generation.auth_sync_stop.wait(K_TEST_HANG_GUARD_SECONDS)

    stale_refresh = _codex_auth_content("retired-account-refresh")
    profile_auth.write_text(stale_refresh, encoding="utf-8")
    newer_mtime_ns = parent_auth.stat().st_mtime_ns + 2_000_000_000
    os.utime(profile_auth, ns=(newer_mtime_ns, newer_mtime_ns))
    process_a.complete_turn(*active_turn[0], "old")
    worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)
    assert not worker.is_alive()
    assert failures == []
    was_retired = process_a.poll() is not None
    if not was_retired:
        client.close()
    assert was_retired

    result = _run_turn(
        client,
        process_b,
        on_turn_started=lambda thread_id, turn_id: process_b.complete_turn(thread_id, turn_id, "new"),
    )
    client.close()

    assert results[0].text == "old"
    assert result.text == "new"
    assert parent_auth.read_text(encoding="utf-8") == relogin_auth
    assert not profile_auth.exists()


@pytest.mark.parametrize("deleted_side", ["parent", "profile"])
def test_codex_app_server_given_synced_auth_file_deleted_expect_generation_and_both_copies_cleared(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deleted_side: str,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("initial"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setattr(codex_settings, "K_CODEX_AUTH_SYNC_POLL_SECONDS", 0.01)
    process_a = FakeCodexAppServerProcess()
    process_b = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process_a, process_b)
    client = CodexAppServerClient(scratch_root=tmp_path)

    _run_turn(client, process_a)
    profile_auth = _current_profile_auth(parent_home)
    (parent_auth if deleted_side == "parent" else profile_auth).unlink()

    assert wait_for_test_condition(lambda: process_a.poll() is not None)
    assert not parent_auth.exists()
    assert not profile_auth.exists()

    _run_turn(client, process_b)
    client.close()

    assert not profile_auth.exists()


def test_codex_app_server_given_auth_on_first_start_expect_missing_profile_seeded_from_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("first-login"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    process = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process)
    client = CodexAppServerClient(scratch_root=tmp_path)

    _run_turn(client, process)
    profile_auth = _current_profile_auth(parent_home)
    client.close()

    assert not profile_auth.exists()
    assert json.loads(parent_auth.read_text(encoding="utf-8")) == {"tokens": {"access_token": "first-login"}}


@pytest.mark.parametrize(
    ("truncated_side", "both_changed"),
    [("parent", False), ("profile", False), ("parent", True), ("profile", True)],
    ids=["parent-only", "profile-only", "parent-both-changed", "profile-both-changed"],
)
def test_codex_app_server_given_truncated_auth_file_expect_poll_skipped_and_last_sync_preserved(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    truncated_side: str,
    both_changed: bool,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("baseline"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setattr(codex_settings, "K_CODEX_AUTH_SYNC_POLL_SECONDS", 30)
    process_a = FakeCodexAppServerProcess()
    process_b = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process_a, process_b)
    client_a = CodexAppServerClient(scratch_root=tmp_path)

    _run_turn(client_a, process_a)
    profile_auth = _current_profile_auth(parent_home)
    truncated_auth = parent_auth if truncated_side == "parent" else profile_auth
    other_auth = profile_auth if truncated_side == "parent" else parent_auth
    truncated_auth.write_text('{"tokens":', encoding="utf-8")
    other_content = _codex_auth_content("other-change") if both_changed else _codex_auth_content("baseline")
    if both_changed:
        other_auth.write_text(other_content, encoding="utf-8")
    client_a.close()

    assert not profile_auth.exists()
    expected_content = other_content if both_changed else _codex_auth_content("baseline")
    if truncated_side == "parent":
        assert parent_auth.read_text(encoding="utf-8") == '{"tokens":'
        repaired_content = _codex_auth_content("repaired")
        parent_auth.write_text(repaired_content, encoding="utf-8")
        expected_content = repaired_content
    else:
        assert parent_auth.read_text(encoding="utf-8") == expected_content

    client_b = CodexAppServerClient(scratch_root=tmp_path)
    _run_turn(client_b, process_b)
    current_profile_auth = _current_profile_auth(parent_home)
    client_b.close()

    assert parent_auth.read_text(encoding="utf-8") == expected_content
    assert not current_profile_auth.exists()


def test_codex_app_server_given_older_profile_auth_than_parent_expect_newer_parent_auth_kept(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("initial"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setattr(codex_settings, "K_CODEX_AUTH_SYNC_POLL_SECONDS", 30)
    process = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process)
    client = CodexAppServerClient(scratch_root=tmp_path)
    newer_parent_auth = _codex_auth_content("owner-refresh")

    def update_both_profiles(thread_id: str, turn_id: str) -> None:
        profile_auth = _current_profile_auth(parent_home)
        profile_auth.write_text(_codex_auth_content("stale-child-refresh"), encoding="utf-8")
        older_mtime_ns = parent_auth.stat().st_mtime_ns - 1_000_000_000
        os.utime(profile_auth, ns=(older_mtime_ns, older_mtime_ns))
        parent_auth.write_text(newer_parent_auth, encoding="utf-8")
        newer_mtime_ns = profile_auth.stat().st_mtime_ns + 2_000_000_000
        os.utime(parent_auth, ns=(newer_mtime_ns, newer_mtime_ns))
        process.complete_turn(thread_id, turn_id, "{}")

    _run_turn(client, process, on_turn_started=update_both_profiles)
    client.close()

    assert parent_auth.read_text(encoding="utf-8") == newer_parent_auth


def test_codex_app_server_given_parent_auth_refresh_with_mtime_tie_expect_profile_auth_updated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("initial"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    process = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process)
    client = CodexAppServerClient(scratch_root=tmp_path)
    refreshed = _codex_auth_content("parent-refresh")

    def refresh_parent_during_turn(thread_id: str, turn_id: str) -> None:
        profile_auth = _current_profile_auth(parent_home)
        tied_mtime_ns = profile_auth.stat().st_mtime_ns
        parent_auth.write_text(refreshed, encoding="utf-8")
        os.utime(parent_auth, ns=(tied_mtime_ns, tied_mtime_ns))
        assert wait_for_test_condition(lambda: profile_auth.read_text(encoding="utf-8") == refreshed)
        process.complete_turn(thread_id, turn_id, "{}")

    result = _run_turn(client, process, on_turn_started=refresh_parent_during_turn)
    client.close()

    assert result.text == "{}"
    assert _generation_profile_auths(parent_home) == []
    assert parent_auth.read_text(encoding="utf-8") == refreshed


def test_codex_app_server_given_parent_changed_with_tied_mtimes_expect_parent_auth_kept(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("initial"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setattr(codex_settings, "K_CODEX_AUTH_SYNC_POLL_SECONDS", 30)
    process = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process)
    client = CodexAppServerClient(scratch_root=tmp_path)
    parent_edit = _codex_auth_content("parent-edit")
    profile_refresh = _codex_auth_content("profile-refresh")

    def refresh_both_during_turn(thread_id: str, turn_id: str) -> None:
        profile_auth = _current_profile_auth(parent_home)
        tied_mtime_ns = profile_auth.stat().st_mtime_ns
        parent_auth.write_text(parent_edit, encoding="utf-8")
        profile_auth.write_text(profile_refresh, encoding="utf-8")
        os.utime(parent_auth, ns=(tied_mtime_ns, tied_mtime_ns))
        os.utime(profile_auth, ns=(tied_mtime_ns, tied_mtime_ns))
        process.complete_turn(thread_id, turn_id, "{}")

    _run_turn(client, process, on_turn_started=refresh_both_during_turn)
    client.close()

    assert parent_auth.read_text(encoding="utf-8") == parent_edit
    assert _generation_profile_auths(parent_home) == []


@pytest.mark.parametrize(
    ("parent_expires_at", "profile_expires_at"),
    [
        ("2027-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        ("2026-01-01T00:00:00+00:00", "2027-01-01T00:00:00+00:00"),
    ],
    ids=["parent-newer-expiry", "profile-newer-expiry"],
)
def test_codex_app_server_given_parent_changed_and_both_files_refreshed_expect_parent_auth_kept(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    parent_expires_at: str,
    profile_expires_at: str,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_content("initial"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setattr(codex_settings, "K_CODEX_AUTH_SYNC_POLL_SECONDS", 30)
    process = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process)
    client = CodexAppServerClient(scratch_root=tmp_path)
    parent_edit = _codex_auth_content("parent-login", expires_at=parent_expires_at)
    profile_refresh = _codex_auth_content("profile-login", expires_at=profile_expires_at)

    def refresh_both_during_turn(thread_id: str, turn_id: str) -> None:
        profile_auth = _current_profile_auth(parent_home)
        tied_mtime_ns = profile_auth.stat().st_mtime_ns
        parent_auth.write_text(parent_edit, encoding="utf-8")
        profile_auth.write_text(profile_refresh, encoding="utf-8")
        os.utime(parent_auth, ns=(tied_mtime_ns, tied_mtime_ns))
        os.utime(profile_auth, ns=(tied_mtime_ns, tied_mtime_ns))
        process.complete_turn(thread_id, turn_id, "{}")

    _run_turn(client, process, on_turn_started=refresh_both_during_turn)
    client.close()

    assert parent_auth.read_text(encoding="utf-8") == parent_edit
    assert _generation_profile_auths(parent_home) == []


@pytest.mark.parametrize(
    ("parent_account_id", "parent_expiry"),
    [("account-b", 1_798_761_600), ("account-a", 1_798_761_600)],
    ids=["new-account", "same-account-relogin"],
)
def test_codex_app_server_given_parent_relogin_and_stale_refresh_expect_parent_kept_and_generation_retired(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    parent_account_id: str,
    parent_expiry: int,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(_codex_auth_for_account(1_767_225_600, "initial", "account-a"), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setattr(codex_settings, "K_CODEX_AUTH_SYNC_POLL_SECONDS", 0.01)
    process_a = FakeCodexAppServerProcess()
    process_b = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process_a, process_b)
    client = CodexAppServerClient(scratch_root=tmp_path)
    turn_started = Event()
    active_turn: list[tuple[str, str]] = []
    results: list[CodexTurnResult] = []
    failures: list[Exception] = []

    def track_turn(thread_id: str, turn_id: str) -> None:
        active_turn.append((thread_id, turn_id))
        turn_started.set()

    def run_old_turn() -> None:
        try:
            results.append(
                client.run_turn(
                    settings=CodexChatSettings(model="gpt-6-luna", effort="low"),
                    thread_id=None,
                    prompt="Old account turn",
                    output_schema={},
                    on_thread_id=lambda _thread_id: None,
                    on_turn_started=track_turn,
                    on_delta=lambda _delta: None,
                )
            )
        except Exception as exc:
            failures.append(exc)

    worker = Thread(target=run_old_turn)
    worker.start()
    assert turn_started.wait(K_TEST_HANG_GUARD_SECONDS)
    generation = client._generation
    assert generation is not None
    profile_auth = _current_profile_auth(parent_home)
    parent_login = _codex_auth_for_account(parent_expiry, "parent-relogin", parent_account_id)
    stale_refresh = _codex_auth_for_account(1_924_992_000, "account-a-refresh", "account-a")
    lock_path = parent_home / codex_settings.K_CODEX_AUTH_SYNC_LOCK_FILENAME

    # Let the watcher observe both independent writes together, as happens when
    # logout/login races an app-server token refresh.
    with auth_profile._codex_auth_file_lock(lock_path):
        parent_auth.write_text(parent_login, encoding="utf-8")
        profile_auth.write_text(stale_refresh, encoding="utf-8")
        os.utime(profile_auth, ns=(parent_auth.stat().st_mtime_ns, parent_auth.stat().st_mtime_ns))

    parent_login_synced = wait_for_test_condition(lambda: profile_auth.read_text(encoding="utf-8") == parent_login)
    if not parent_login_synced:
        process_a.complete_turn(*active_turn[0], "cleanup")
        worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)
        client.close()
    assert parent_login_synced
    assert generation.auth_sync_stop.wait(K_TEST_HANG_GUARD_SECONDS)
    assert process_a.poll() is None
    process_a.complete_turn(*active_turn[0], "old turn complete")
    worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)
    assert not worker.is_alive()
    assert failures == []
    assert results[0].text == "old turn complete"
    assert wait_for_test_condition(lambda: process_a.poll() is not None)

    result = _run_turn(
        client,
        process_b,
        on_turn_started=lambda thread_id, turn_id: process_b.complete_turn(thread_id, turn_id, "parent login"),
    )
    client.close()

    assert result.text == "parent login"
    assert parent_auth.read_text(encoding="utf-8") == parent_login
    assert not profile_auth.exists()


def test_codex_app_server_given_unresolved_jwt_identity_and_changed_parent_expect_parent_kept(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_home = tmp_path / "codex-home"
    parent_home.mkdir()
    parent_auth = parent_home / codex_settings.K_CODEX_AUTH_FILENAME
    parent_auth.write_text(json.dumps(_codex_auth(1_767_225_600, "initial-refresh")), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(parent_home))
    monkeypatch.setattr(codex_settings, "K_CODEX_AUTH_SYNC_POLL_SECONDS", 30)
    process = FakeCodexAppServerProcess()
    _install_app_server_process(monkeypatch, process)
    client = CodexAppServerClient(scratch_root=tmp_path)
    parent_login = json.dumps(_codex_auth(1_798_761_600, "parent-refresh"))
    profile_refresh = json.dumps(_codex_auth(1_830_297_600, "profile-refresh"))

    def refresh_both_during_turn(thread_id: str, turn_id: str) -> None:
        profile_auth = _current_profile_auth(parent_home)
        tied_mtime_ns = profile_auth.stat().st_mtime_ns
        parent_auth.write_text(parent_login, encoding="utf-8")
        profile_auth.write_text(profile_refresh, encoding="utf-8")
        os.utime(parent_auth, ns=(tied_mtime_ns, tied_mtime_ns))
        os.utime(profile_auth, ns=(tied_mtime_ns, tied_mtime_ns))
        process.complete_turn(thread_id, turn_id, "{}")

    _run_turn(client, process, on_turn_started=refresh_both_during_turn)
    client.close()

    assert json.loads(parent_auth.read_text(encoding="utf-8"))["tokens"]["refresh_token"] == "parent-refresh"
    assert _generation_profile_auths(parent_home) == []


@pytest.mark.skipif(os.getenv("CODEX_SMOKE") != "1", reason="set CODEX_SMOKE=1 to call the logged-in Codex app-server")
def test_codex_app_server_given_live_smoke_prompt_expect_schema_constrained_reply() -> None:
    client = CodexAppServerClient()
    try:
        result = client.run_turn(
            settings=CodexChatSettings(model="gpt-6.1-sol", effort="medium"),
            thread_id=None,
            prompt='Return only a JSON object with reply "ok", edits [], and regenerate null.',
            output_schema=ChatResponse.model_json_schema(),
            on_thread_id=lambda _thread_id: None,
            on_turn_started=lambda _thread_id, _turn_id: None,
            on_delta=lambda _delta: None,
        )
        response = ChatResponse.model_validate_json(result.text)
        assert response.reply == "ok"
        assert response.edits == []
        assert response.regenerate is None
    finally:
        client.close()


class _TrackingThreadLock:
    """Expose when a selected test thread reaches a held turn lock."""

    def __init__(self, blocked_event: Event, blocked_thread_name: str) -> None:
        self.lock = Lock()
        self.blocked_event = blocked_event
        self.blocked_thread_name = blocked_thread_name

    def __enter__(self) -> _TrackingThreadLock:
        if current_thread().name == self.blocked_thread_name:
            if not self.lock.acquire(blocking=False):
                self.blocked_event.set()
                self.lock.acquire()
        else:
            self.lock.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.lock.release()


def _install_app_server_process(
    monkeypatch: pytest.MonkeyPatch,
    *processes: FakeCodexAppServerProcess,
) -> None:
    remaining = iter(processes)
    monkeypatch.setattr(app_server.subprocess, "Popen", lambda *_args, **_kwargs: next(remaining))


def _current_profile_auth(parent_home: Path) -> Path:
    profile_root = parent_home / codex_settings.K_CODEX_ISOLATED_PROFILE_DIRECTORY
    sync_state = json.loads(
        (profile_root / codex_settings.K_CODEX_AUTH_SYNC_STATE_FILENAME).read_text(encoding="utf-8")
    )
    generation_id = sync_state["generation_id"]
    assert isinstance(generation_id, str)
    return profile_root / generation_id / codex_settings.K_CODEX_AUTH_FILENAME


def _generation_profile_auths(parent_home: Path) -> list[Path]:
    profile_root = parent_home / codex_settings.K_CODEX_ISOLATED_PROFILE_DIRECTORY
    return list(profile_root.glob("*/auth.json"))


def _codex_auth(expires_at: int, refresh_token: str) -> dict[str, object]:
    payload = urlsafe_b64encode(json.dumps({"exp": expires_at}).encode()).decode().rstrip("=")
    return {
        "tokens": {
            "access_token": f"eyJhbGciOiJub25lIn0.{payload}.signature",
            "refresh_token": refresh_token,
        },
        "last_refresh": "2026-10-14T00:00:00Z",
    }


def _codex_auth_for_account(expires_at: int, refresh_token: str, account_id: str) -> str:
    payload = (
        urlsafe_b64encode(
            json.dumps({"exp": expires_at, "https://api.openai.com/auth": {"chatgpt_account_id": account_id}}).encode()
        )
        .decode()
        .rstrip("=")
    )
    return json.dumps(
        {
            "tokens": {
                "access_token": f"eyJhbGciOiJub25lIn0.{payload}.signature",
                "refresh_token": refresh_token,
            }
        }
    )


def _codex_auth_content(access_token: str, **fields: str) -> str:
    document: dict[str, object] = {"tokens": {"access_token": access_token}}
    document.update(fields)
    return json.dumps(document)


def _run_turn(
    client: CodexAppServerClient,
    process: FakeCodexAppServerProcess,
    *,
    settings: CodexChatSettings | None = None,
    output_schema: dict[str, object] | None = None,
    on_turn_started=None,
) -> CodexTurnResult:
    return client.run_turn(
        settings=settings or CodexChatSettings(model="gpt-6-luna", effort="low"),
        thread_id=None,
        prompt="Reply",
        output_schema=output_schema or {},
        on_thread_id=lambda _thread_id: None,
        on_turn_started=on_turn_started or (lambda thread_id, turn_id: process.complete_turn(thread_id, turn_id, "{}")),
        on_delta=lambda _delta: None,
    )


def _request_methods(process: FakeCodexAppServerProcess) -> list[str]:
    return [method for request in process.requests if isinstance((method := request.get("method")), str)]


def _send_codex_completion(
    process: FakeCodexAppServerProcess,
    thread_id: str,
    turn_id: str,
) -> None:
    process.send_message(
        {
            "method": "item/agentMessage/delta",
            "params": {"threadId": thread_id, "turnId": turn_id, "delta": '{"reply":"Hello'},
        }
    )
    process.complete_turn(thread_id, turn_id, "{}")


def _raise_codex_callback_error(_thread_id: str, _turn_id: str) -> None:
    raise RuntimeError("could not persist turn start")


def _complete_after_barrier(
    process: FakeCodexAppServerProcess,
    thread_id: str,
    turn_id: str,
    barrier: Barrier,
) -> None:
    barrier.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
    process.complete_turn(thread_id, turn_id, "reply")


def _schema_keyword_values(schema: object, keyword: str) -> list[object]:
    """Collect every value stored under one JSON Schema keyword, nested anywhere."""
    values: list[object] = []
    if isinstance(schema, dict):
        for key, nested in schema.items():
            if key == keyword:
                values.append(nested)
            values.extend(_schema_keyword_values(nested, keyword))
    elif isinstance(schema, list):
        for nested in schema:
            values.extend(_schema_keyword_values(nested, keyword))
    return values


def _object_nodes_with_properties(schema: object) -> list[dict[str, object]]:
    """Collect every object node declaring properties, including nested definitions."""
    nodes: list[dict[str, object]] = []
    if isinstance(schema, dict):
        if isinstance(schema.get("properties"), dict):
            nodes.append(schema)
        for nested in schema.values():
            nodes.extend(_object_nodes_with_properties(nested))
    elif isinstance(schema, list):
        for nested in schema:
            nodes.extend(_object_nodes_with_properties(nested))
    return nodes
