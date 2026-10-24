import fcntl
import json
import os
import threading
import time
from base64 import urlsafe_b64encode
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

import lesson_builder.clients.llm.base as base_mod
import lesson_builder.clients.llm.opencode as opencode_mod
import lesson_builder.clients.llm.opencode_credentials as opencode_credentials_mod
import lesson_builder.clients.llm.opencode_runtime as opencode_runtime_mod
import lesson_builder.clients.llm.role_jobs as role_jobs_mod
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.cancellation import Cancelled
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.exceptions import LlmQuotaException
from lesson_builder.clients.llm.invocation import JobRunner
from lesson_builder.clients.llm.opencode import OpencodeClient
from lesson_builder.clients.llm.settings import K_OPENCODE_INLINE_PROMPT_MAX_BYTES
from lesson_builder.clients.llm.settings import K_OPENCODE_SHARED_CACHE_RELATIVE_PATH
from tests.clients.llm.fakes import FakeHangingTextStream
from tests.clients.llm.fakes import FakeOpencodeProcess
from tests.settings import K_TEST_HANG_GUARD_SECONDS
from tests.settings import wait_for_test_condition


def test_opencode_client_given_agent_and_variant_expect_cli_flags(monkeypatch):
    commands: list[list[str]] = []

    def fake_run(cmd, *, env, cwd=None, **kwargs):
        commands.append(cmd)
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_run)

    result = OpencodeClient().call(
        "prompt",
        model="openai/gpt-5.6-sol",
        agent="catalog-review",
        variant="xhigh",
    )

    assert commands[0] == [
        "opencode",
        "run",
        "--pure",
        "--format",
        "json",
        "--print-logs",
        "--log-level",
        "ERROR",
        "--agent",
        "catalog-review",
        "--variant",
        "xhigh",
        "-m",
        "openai/gpt-5.6-sol",
        "--",
        "prompt",
    ]
    assert result.agent == "catalog-review"
    assert result.variant == "xhigh"


def test_opencode_client_given_streamed_quota_log_expect_early_quota_exception(monkeypatch) -> None:
    process = FakeOpencodeProcess(
        pid=4101,
        stderr=(
            'level=ERROR message="stream error" small=false '
            'agent=lesson-author error="The usage limit has been reached"\n'
        ),
        returncode=None,
    )
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        opencode_mod.os,
        "killpg",
        lambda _pid, sent_signal: process.receive_signal(sent_signal),
    )

    with pytest.raises(LlmQuotaException, match="usage limit"):
        OpencodeClient(timeout=60).call("prompt", agent="lesson-author")

    assert process.signals == [base_mod.signal.SIGTERM]


def test_opencode_client_given_title_agent_quota_and_primary_success_expect_success(monkeypatch) -> None:
    process = FakeOpencodeProcess(
        pid=4102,
        stdout=_opencode_stdout("OK"),
        stderr=('level=ERROR message="stream error" small=true agent=title error="The usage limit has been reached"\n'),
    )
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", lambda *_args, **_kwargs: process)

    result = OpencodeClient(timeout=60).call("prompt", agent="lesson-author")

    assert result.text == "OK"
    assert process.signals == []


def test_opencode_client_given_primary_quota_log_and_completed_stdout_expect_success(monkeypatch) -> None:
    process = FakeOpencodeProcess(
        pid=4105,
        stdout=_opencode_stdout("OK"),
        stderr=(
            'level=ERROR message="stream error" small=false '
            'agent=lesson-author error="The usage limit has been reached"\n'
        ),
    )
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", lambda *_args, **_kwargs: process)

    result = OpencodeClient(timeout=60).call("prompt", agent="lesson-author")

    assert result.text == "OK"
    assert process.signals == []


def test_opencode_client_given_collector_structured_quota_event_expect_llm_quota_exception(monkeypatch) -> None:
    quota_event = json.dumps(
        {"type": "error", "error": {"name": "UnknownError", "data": {"message": "usage limit reached"}}}
    )
    process = FakeOpencodeProcess(pid=4106, stdout=f"{quota_event}\n", returncode=1)
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(LlmQuotaException, match="usage limit"):
        OpencodeClient(timeout=60).call("prompt")


def test_opencode_client_given_expired_deadline_expect_term_then_kill_after_grace(monkeypatch) -> None:
    process = FakeOpencodeProcess(
        pid=4103,
        returncode=None,
        exit_on_terminate=False,
    )
    clock = [0.0]

    def start_process(*_args, **_kwargs):
        clock[0] = 5000.0
        return process

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", start_process)
    monkeypatch.setattr(
        opencode_mod.os,
        "killpg",
        lambda _pid, sent_signal: process.receive_signal(sent_signal),
    )
    monkeypatch.setattr(opencode_mod.time, "monotonic", lambda: clock[0])

    with pytest.raises(BackendDownException, match="timed out"):
        OpencodeClient(timeout=60).call("prompt")

    assert process.signals == [base_mod.signal.SIGTERM, base_mod.signal.SIGKILL]


def test_job_runner_given_batch_cancellation_expect_no_retry(monkeypatch) -> None:
    processes: list[FakeOpencodeProcess] = []

    def fake_popen(*_args, **_kwargs) -> FakeOpencodeProcess:
        process = FakeOpencodeProcess(pid=4104 + len(processes), returncode=None)
        processes.append(process)
        return process

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        base_mod.os,
        "killpg",
        lambda pid, sent_signal: next(process for process in processes if process.pid == pid).receive_signal(
            sent_signal
        ),
    )
    monkeypatch.setattr("lesson_builder.clients.llm.invocation.time.sleep", lambda _seconds: None)
    errors: list[Exception] = []

    def call_client() -> None:
        try:
            client = OpencodeClient(timeout=60)
            JobRunner(name="opencode", client=client).invoke_response("prompt")
        except Exception as exc:  # noqa: BLE001 - the assertion checks cancellation routing
            errors.append(exc)

    worker = threading.Thread(target=call_client)
    worker.start()
    assert wait_for_test_condition(lambda: bool(base_mod.K_ACTIVE_CLI_PROCESSES))
    role_jobs_mod.cancel_active_clients()
    worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    try:
        assert not worker.is_alive()
        assert isinstance(errors[0], Cancelled)
        assert errors[0].reason == "author"
        assert len(processes) == 1
        assert processes[0].signals == [base_mod.signal.SIGTERM]
        assert base_mod.K_ACTIVE_CLI_PROCESSES == {}
    finally:
        role_jobs_mod.reset_client_cancellation()


@pytest.mark.parametrize("signal_kind", ["cancel", "deadline"])
def test_opencode_client_given_cancel_or_deadline_in_parallel_expect_processes_terminated(
    monkeypatch, signal_kind: str
) -> None:
    _warm_opencode_cache("opencode-go/glm-5.2")
    processes: list[FakeOpencodeProcess] = []
    both_processes_started = threading.Event()
    cancellation = CancellationToken()
    errors: list[Exception] = []

    def fake_popen(*_args, **_kwargs) -> FakeOpencodeProcess:
        process = FakeOpencodeProcess(pid=4300 + len(processes), returncode=None)
        processes.append(process)
        if len(processes) == 2:
            both_processes_started.set()
        return process

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        base_mod.os,
        "killpg",
        lambda pid, sent_signal: next(process for process in processes if process.pid == pid).receive_signal(
            sent_signal
        ),
    )

    def call_client() -> None:
        try:
            JobRunner(name="opencode", client=OpencodeClient(timeout=60)).invoke_response(
                "prompt", cancellation=cancellation
            )
        except Exception as exc:  # noqa: BLE001 - the assertion checks cancellation routing
            errors.append(exc)

    workers = [threading.Thread(target=call_client) for _ in range(2)]
    for worker in workers:
        worker.start()
    assert both_processes_started.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
    reason = "author" if signal_kind == "cancel" else "deadline"
    cancellation.cancel(reason, f"{signal_kind} requested")
    for worker in workers:
        worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert all(not worker.is_alive() for worker in workers)
    assert len(errors) == 2
    assert all(isinstance(error, Cancelled) and error.reason == reason for error in errors)
    assert len(processes) == 2
    assert [process.signals for process in processes] == [[base_mod.signal.SIGTERM]] * 2
    assert base_mod.K_ACTIVE_CLI_PROCESSES == {}


def test_job_runner_given_cancellation_before_registration_expect_no_retry_and_reset_allows_next_call(
    monkeypatch,
) -> None:
    _warm_opencode_cache("opencode-go/glm-5.2")
    processes: list[FakeOpencodeProcess] = []
    popen_started = threading.Event()
    release_popen = threading.Event()

    def fake_popen(*_args, **_kwargs) -> FakeOpencodeProcess:
        if not processes:
            popen_started.set()
            if not release_popen.wait(timeout=K_TEST_HANG_GUARD_SECONDS):
                raise AssertionError("test did not release the blocked Popen")
            process = FakeOpencodeProcess(pid=4200, returncode=None)
        else:
            process = FakeOpencodeProcess(pid=4201, stdout=_opencode_stdout("OK"), returncode=0)
        processes.append(process)
        return process

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        base_mod.os,
        "killpg",
        lambda pid, sent_signal: next(process for process in processes if process.pid == pid).receive_signal(
            sent_signal
        ),
    )
    monkeypatch.setattr("lesson_builder.clients.llm.invocation.time.sleep", lambda _seconds: None)
    errors: list[Exception] = []

    def call_client() -> None:
        try:
            client = OpencodeClient(timeout=60)
            JobRunner(name="opencode", client=client).invoke_response("prompt")
        except Exception as exc:  # noqa: BLE001 - the assertion checks cancellation routing
            errors.append(exc)

    worker = threading.Thread(target=call_client)
    worker.start()
    cancellation_requested = False
    try:
        assert popen_started.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        role_jobs_mod.cancel_active_clients()
        cancellation_requested = True
    finally:
        if not cancellation_requested:
            role_jobs_mod.cancel_active_clients()
        release_popen.set()
        worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    try:
        assert not worker.is_alive()
        assert isinstance(errors[0], Cancelled)
        assert errors[0].reason == "author"
        assert len(processes) == 1
        assert processes[0].signals == [base_mod.signal.SIGTERM]
        assert base_mod.K_ACTIVE_CLI_PROCESSES == {}
    finally:
        role_jobs_mod.reset_client_cancellation()

    assert OpencodeClient(timeout=60).call("prompt").text == "OK"
    assert len(processes) == 2


def test_opencode_client_given_large_prompt_expect_file_attachment(monkeypatch):
    calls: list[tuple[list[str], str]] = []
    prompt = "x" * (K_OPENCODE_INLINE_PROMPT_MAX_BYTES + 1)

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        prompt_text = cmd[-1]
        calls.append((cmd, prompt_text))
        prompt_path = Path(cmd[cmd.index("-f") + 1])
        assert prompt_path.read_text(encoding="utf-8") == prompt
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    result = OpencodeClient(timeout=60).call(
        prompt,
        model="openai/gpt-5.6-sol",
        agent="catalog-review",
        variant="xhigh",
    )

    assert result.text == "OK"
    assert len(calls) == 1
    command, prompt_text = calls[0]
    assert prompt not in command
    assert command[command.index("-f") + 3] == (
        "Execute the attached prompt as the complete task and emit its requested output."
    )


def test_opencode_client_given_no_options_expect_no_provider_flags(monkeypatch):
    commands: list[list[str]] = []

    def fake_run(cmd, *, env, cwd=None, **kwargs):
        commands.append(cmd)
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_run)

    OpencodeClient().call("prompt")

    assert "--agent" not in commands[0]
    assert "--variant" not in commands[0]


def test_opencode_client_given_explicit_timeout_expect_subprocess_uses_call_deadline(monkeypatch):
    clock = [1000.0]
    process = FakeOpencodeProcess(pid=4201, returncode=None)

    def start_process(*_args, **_kwargs):
        clock[0] = 1240.0
        return process

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", start_process)
    monkeypatch.setattr(opencode_mod.os, "killpg", lambda _pid, sent_signal: process.receive_signal(sent_signal))

    with pytest.raises(BackendDownException, match="timed out after 240s"):
        OpencodeClient(timeout=240).call("prompt")

    assert process.signals == [base_mod.signal.SIGTERM]


@pytest.mark.parametrize("timeout", [0, -5])
def test_opencode_client_given_non_positive_integer_timeout_expect_value_error(timeout):
    with pytest.raises(ValueError, match="timeout must be"):
        OpencodeClient(timeout=timeout)


@pytest.mark.parametrize("timeout", [True, 1.5, "30"])
def test_opencode_client_given_non_integer_timeout_expect_type_error(timeout):
    with pytest.raises(TypeError, match="timeout must be"):
        OpencodeClient(timeout=timeout)


def test_opencode_client_given_parallel_calls_expect_concurrent_processes(monkeypatch):
    _warm_opencode_cache("opencode-go/glm-5.2")
    active = 0
    maximum_active = 0
    state_lock = threading.Lock()
    entered = threading.Barrier(2)
    errors: list[Exception] = []

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        prompt_text = cmd[-1]
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        entered.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        with state_lock:
            active -= 1
        return FakeOpencodeProcess(stdout=_opencode_stdout(prompt_text), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    def run_call(prompt: str) -> None:
        try:
            OpencodeClient(timeout=60).call(prompt)
        except Exception as exc:  # pragma: no cover - assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=run_call, args=(prompt,)) for prompt in ("call-a", "call-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert maximum_active == 2


def test_opencode_client_given_one_failed_parallel_call_expect_other_call_to_complete(monkeypatch):
    _warm_opencode_cache("opencode-go/glm-5.2")
    entered = threading.Barrier(2)

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        prompt_text = cmd[-1]
        entered.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        if prompt_text == "fail":
            return FakeOpencodeProcess(stdout="", stderr="failed", returncode=1)
        return FakeOpencodeProcess(stdout=_opencode_stdout("success"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)
    results: dict[str, object] = {}

    def run_call(prompt: str) -> None:
        try:
            results[prompt] = OpencodeClient(timeout=60).call(prompt)
        except Exception as exc:  # pragma: no cover - assertion below reports it
            results[prompt] = exc

    threads = [threading.Thread(target=run_call, args=(prompt,)) for prompt in ("fail", "success")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert isinstance(results["fail"], BackendDownException)
    assert getattr(results["success"], "text", None) == "success"


def test_opencode_client_given_parallel_calls_expect_private_data_and_shared_cache(monkeypatch, tmp_path):
    source_data_home = tmp_path / "source-data"
    source_auth = source_data_home / "opencode" / "auth.json"
    source_auth.parent.mkdir(parents=True)
    source_auth.write_text(
        json.dumps(
            {
                "openai": {"type": "api", "key": "test-openai-key"},
                "opencode-go": {"type": "api", "key": "test-opencode-key"},
                "zai-coding-plan": {"type": "api", "key": "test-zai-key"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(source_data_home))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))

    captured: list[tuple[tuple[Path, Path, Path], dict[str, object]]] = []
    captured_lock = threading.Lock()

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        assert env is not None
        xdg_dirs = tuple(Path(env[key]) for key in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"))
        auth_path = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        with captured_lock:
            captured.append((xdg_dirs, json.loads(auth_path.read_text(encoding="utf-8"))))
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    def run_call(prompt: str) -> None:
        OpencodeClient(timeout=60).call(prompt)

    threads = [threading.Thread(target=run_call, args=(prompt,)) for prompt in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(captured) == 2
    assert captured[0][0][:2] != captured[1][0][:2]
    assert captured[0][0][2] == captured[1][0][2]
    assert captured[0][1] == {"opencode-go": {"type": "api", "key": "test-opencode-key"}}
    assert captured[1][1] == {"opencode-go": {"type": "api", "key": "test-opencode-key"}}
    for xdg_dirs, _ in captured:
        assert all(not directory.exists() for directory in xdg_dirs[:2])
    assert captured[0][0][2].exists()
    assert not list(captured[0][0][2].rglob("auth.json"))
    assert os.environ["XDG_DATA_HOME"] == str(source_data_home)
    assert json.loads(source_auth.read_text(encoding="utf-8")) == {
        "openai": {"type": "api", "key": "test-openai-key"},
        "opencode-go": {"type": "api", "key": "test-opencode-key"},
        "zai-coding-plan": {"type": "api", "key": "test-zai-key"},
    }


def test_opencode_client_given_sequential_calls_expect_shared_cache_and_private_data_under_20_mb(monkeypatch, tmp_path):
    parent_cache = tmp_path / "parent-cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(parent_cache))
    monkeypatch.setenv("OPENCODE_CONFIG_CONTENT", '{"skills":{"urls":["https://example.invalid/skills"]}}')
    captured: list[tuple[str, str, str, int, str]] = []

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        assert env is not None
        per_call_roots = [Path(env[name]) for name in ("XDG_DATA_HOME", "XDG_STATE_HOME", "TMPDIR")]
        disk_bytes = sum(path.stat().st_size for root in per_call_roots for path in root.rglob("*") if path.is_file())
        captured.append(
            (
                env["XDG_CACHE_HOME"],
                env["XDG_DATA_HOME"],
                env["XDG_STATE_HOME"],
                disk_bytes,
                env["OPENCODE_CONFIG_CONTENT"],
            )
        )
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    OpencodeClient().call("first")
    OpencodeClient().call("second")

    expected_cache = parent_cache / "norsk-lesson-data" / "opencode"
    assert len(captured) == 2
    assert {Path(item[0]) for item in captured} == {expected_cache}
    assert captured[0][1] != captured[1][1]
    assert captured[0][2] != captured[1][2]
    assert all(item[3] < 20 * 1024 * 1024 for item in captured)
    assert {item[4] for item in captured} == {'{"skills":{"urls":[]}}'}
    assert not list(expected_cache.rglob("auth.json"))


def test_opencode_client_given_first_sweep_waiting_past_deadline_expect_failure(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(runtime_root))
    lock_path = runtime_root / f".norsk-opencode-temp-root-{os.getuid()}.lock"

    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BackendDownException, match="temporary directory sweep"):
            OpencodeClient(timeout=1).call("prompt")


def test_opencode_client_given_first_sweep_waiting_when_cancelled_expect_failure(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(runtime_root))
    sweep_waiting = threading.Event()
    cancellation = CancellationToken()
    errors: list[Exception] = []
    original_file_lock = opencode_runtime_mod.hold_opencode_file_lock

    def observed_file_lock(path, *, deadline, timeout, is_cancelled=None, purpose, **kwargs):
        if purpose == "temporary directory sweep":
            sweep_waiting.set()
        return original_file_lock(
            path,
            deadline=deadline,
            timeout=timeout,
            is_cancelled=is_cancelled,
            purpose=purpose,
            **kwargs,
        )

    def run_call() -> None:
        try:
            OpencodeClient(timeout=60).call("prompt", cancellation=cancellation)
        except Exception as error:  # noqa: BLE001 - assert cancellation routing from the owning call
            errors.append(error)

    monkeypatch.setattr(opencode_runtime_mod, "hold_opencode_file_lock", observed_file_lock)
    lock_path = runtime_root / f".norsk-opencode-temp-root-{os.getuid()}.lock"
    with lock_path.open("a+") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        worker = threading.Thread(target=run_call)
        worker.start()
        assert sweep_waiting.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        cancellation.cancel("author", "stop the test call")
        worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], Cancelled)


class _OpenCodeCacheCallProbe:
    def __init__(self) -> None:
        self.phase = "cold"
        self.release_cold = threading.Event()
        self.first_cold_entered = threading.Event()
        self.second_cold_entered = threading.Event()
        self.active = {"cold": 0, "warm": 0}
        self.max_active = {"cold": 0, "warm": 0}
        self.active_lock = threading.Lock()
        self.warm_barrier = threading.Barrier(2)
        self.errors: list[BaseException] = []

    def run_call(self, model: str) -> None:
        try:
            OpencodeClient(timeout=60).call("prompt", model=model)
        except BaseException as error:
            self.errors.append(error)

    def fake_exec(self, cmd, *, env, cwd=None, **kwargs):
        phase = self.phase
        with self.active_lock:
            self.active[phase] += 1
            self.max_active[phase] = max(self.max_active[phase], self.active[phase])
            if phase == "cold" and self.active[phase] == 1:
                self.first_cold_entered.set()
            elif phase == "cold":
                self.second_cold_entered.set()
        try:
            if phase == "cold":
                assert self.release_cold.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
            else:
                self.warm_barrier.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        finally:
            with self.active_lock:
                self.active[phase] -= 1
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    def run_pair(self, phase: str, models: tuple[str, str]) -> None:
        self.phase = phase
        threads = [threading.Thread(target=self.run_call, args=(model,)) for model in models]
        threads[0].start()
        if phase == "cold":
            assert self.first_cold_entered.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
            threads[1].start()
            assert not self.second_cold_entered.wait(timeout=0.1)
            self.release_cold.set()
        else:
            threads[1].start()
        for thread in threads:
            thread.join(timeout=K_TEST_HANG_GUARD_SECONDS)


def test_opencode_client_given_cold_cache_calls_expect_serialization_and_warm_parallelism(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(runtime_root))
    probe = _OpenCodeCacheCallProbe()
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", probe.fake_exec)

    models = ("openai/gpt-5.6-sol", "opencode-go/glm-5.2")
    probe.run_pair("cold", models)
    assert not probe.errors
    assert probe.max_active["cold"] == 1

    probe.run_pair("warm", models)
    assert not probe.errors
    assert probe.max_active["warm"] == 2


def test_opencode_client_given_waiters_during_cold_population_expect_warm_handoff_in_parallel(monkeypatch):
    cold_entered = threading.Event()
    release_cold = threading.Event()
    waiters_queued = threading.Event()
    warm_calls = threading.Barrier(2)
    errors: list[Exception] = []
    population_attempts = 0
    attempts_lock = threading.Lock()
    original_lock = opencode_runtime_mod.hold_opencode_file_lock

    def observed_lock(path, *, deadline, timeout, is_cancelled=None, purpose, **kwargs):
        nonlocal population_attempts
        if purpose == "cache population intent":
            with attempts_lock:
                population_attempts += 1
                if population_attempts == 3:
                    waiters_queued.set()
        return original_lock(
            path, deadline=deadline, timeout=timeout, is_cancelled=is_cancelled, purpose=purpose, **kwargs
        )

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        prompt_text = cmd[-1]
        if prompt_text == "cold":
            cold_entered.set()
            assert release_cold.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        else:
            warm_calls.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    def run_call(prompt: str) -> None:
        try:
            OpencodeClient(timeout=60).call(prompt)
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(opencode_runtime_mod, "hold_opencode_file_lock", observed_lock)
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)
    cold = threading.Thread(target=run_call, args=("cold",))
    cold.start()
    assert cold_entered.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
    waiters = [threading.Thread(target=run_call, args=("warm",)) for _ in range(2)]
    for waiter in waiters:
        waiter.start()
    try:
        assert waiters_queued.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
    finally:
        release_cold.set()
        for worker in [cold, *waiters]:
            worker.join(timeout=K_TEST_HANG_GUARD_SECONDS * 2)

    assert not errors
    assert all(not worker.is_alive() for worker in [cold, *waiters])


@pytest.mark.parametrize("previously_warm", [False, True])
@pytest.mark.parametrize(
    "failure_stdout",
    [None, '{"type":"error","error":{"data":{"message":"interrupted installation"}}}', ""],
    ids=["raised", "zero-exit-error", "zero-exit-empty"],
)
def test_opencode_client_given_handled_failure_expect_packages_preserved_before_retry(
    monkeypatch, previously_warm, failure_stdout
):
    cached_package = (
        Path(os.environ["XDG_CACHE_HOME"]) / K_OPENCODE_SHARED_CACHE_RELATIVE_PATH / "opencode/packages/complete"
    )
    cached_package.mkdir(parents=True)
    if previously_warm:
        _warm_opencode_cache("opencode-go/glm-5.2")

    def interrupted_exec(cmd, *, env, cwd=None, **kwargs):
        if failure_stdout is not None:
            return FakeOpencodeProcess(stdout=failure_stdout, returncode=0)
        raise BackendDownException("interrupted installation")

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", interrupted_exec)
    with pytest.raises(BackendDownException):
        OpencodeClient().call("first")

    def recovered_exec(cmd, *, env, cwd=None, **kwargs):
        assert cached_package.is_dir()
        return FakeOpencodeProcess(stdout=_opencode_stdout("recovered"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", recovered_exec)
    response = OpencodeClient().call("retry")

    assert response.text == "recovered"


def test_opencode_client_given_changed_provider_packages_expect_shared_install_locks(monkeypatch):
    _warm_opencode_cache("opencode-go/glm-5.2")
    _warm_opencode_cache("openai/gpt-5.6-sol")
    lock_roots: list[Path] = []

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        root = Path(env["XDG_STATE_HOME"]) / "opencode/locks"
        assert root.is_dir()
        lock_roots.append(root.resolve())
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)
    OpencodeClient().call("new package mapping", model="opencode-go/glm-5.2")
    OpencodeClient().call("same new package mapping", model="openai/gpt-5.6-sol")

    assert len(set(lock_roots)) == 1


def test_opencode_client_given_overlapping_first_calls_expect_one_sweep(monkeypatch):
    runtime_root = Path(os.environ["XDG_RUNTIME_DIR"])
    _warm_opencode_cache("opencode-go/glm-5.2")
    first_sweep = threading.Event()
    duplicate_sweep = threading.Event()
    release_sweep = threading.Event()
    sweep_calls: list[object] = []
    errors: list[Exception] = []
    original_iterdir = Path.iterdir

    def observed_iterdir(path):
        if path != runtime_root:
            return original_iterdir(path)
        sweep_calls.append(path)
        if len(sweep_calls) == 1:
            first_sweep.set()
        else:
            duplicate_sweep.set()
        assert release_sweep.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        return original_iterdir(path)

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    def run_call() -> None:
        try:
            OpencodeClient(timeout=60).call("prompt")
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(Path, "iterdir", observed_iterdir)
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)
    workers = [threading.Thread(target=run_call) for _ in range(2)]
    workers[0].start()
    assert first_sweep.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
    workers[1].start()
    try:
        assert not duplicate_sweep.wait(timeout=0.1)
    finally:
        release_sweep.set()
        for worker in workers:
            worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert not errors
    assert len(sweep_calls) == 1


def test_opencode_client_given_first_call_expect_legacy_temp_dirs_swept_before_spawn(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    stale_dir = runtime_root / "norsk-opencode-state-legacy"
    stale_dir.mkdir()
    old = time.time() - 24 * 60 * 60
    os.utime(stale_dir, (old, old))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(runtime_root))

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        assert not stale_dir.exists()
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    OpencodeClient().call("prompt")


def test_opencode_client_given_model_provider_expect_only_matching_auth_entry_copied(monkeypatch, tmp_path):
    source_data_home = tmp_path / "source-data"
    source_auth = source_data_home / "opencode" / "auth.json"
    source_auth.parent.mkdir(parents=True)
    credentials = {
        "openai": {"type": "oauth", "refresh": "broken-openai-token"},
        "zai-coding-plan": {"type": "api", "key": "working-zai-key"},
    }
    source_auth.write_text(json.dumps(credentials), encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(source_data_home))
    captured: dict[str, object] = {}

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        auth_path = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        captured.update(json.loads(auth_path.read_text(encoding="utf-8")))
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    result = OpencodeClient(timeout=60).call("prompt", model="zai-coding-plan/glm-5.3")

    assert result.text == "OK"
    assert captured == {"zai-coding-plan": credentials["zai-coding-plan"]}


def test_opencode_client_given_refreshed_oauth_entry_expect_only_provider_written_back(monkeypatch, tmp_path):
    source_auth = _write_opencode_auth(
        tmp_path,
        {
            "openai": _openai_oauth_entry("old-access", "old-refresh", expires=int(time.time()) + 3600),
            "zai-coding-plan": {"type": "api", "key": "keep-this-key"},
        },
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    refreshed = _openai_oauth_entry("new-access", "new-refresh", expires=int(time.time()) + 7200)

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        target_auth.write_text(json.dumps({"openai": refreshed}), encoding="utf-8")
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    result = OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")

    assert result.text == "OK"
    assert json.loads(source_auth.read_text(encoding="utf-8")) == {
        "openai": refreshed,
        "zai-coding-plan": {"type": "api", "key": "keep-this-key"},
    }


def test_opencode_client_given_unchanged_oauth_entry_expect_source_file_not_replaced(monkeypatch, tmp_path):
    original_entry = _openai_oauth_entry("access", "refresh", expires=int(time.time()) + 3600)
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry})
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    original_inode = source_auth.stat().st_ino

    monkeypatch.setattr(
        opencode_mod.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0),
    )

    OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")

    assert source_auth.stat().st_ino == original_inode
    assert json.loads(source_auth.read_text(encoding="utf-8")) == {"openai": original_entry}


@pytest.mark.parametrize("temporary_contents", [None, "not-json"])
def test_opencode_client_given_missing_or_invalid_temporary_auth_expect_writeback_skipped(
    monkeypatch,
    tmp_path,
    caplog,
    temporary_contents,
):
    original_entry = _openai_oauth_entry("access", "refresh", expires=int(time.time()) + 3600)
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry})
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    original_inode = source_auth.stat().st_ino

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        if temporary_contents is None:
            target_auth.unlink()
        else:
            target_auth.write_text(temporary_contents, encoding="utf-8")
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")

    assert source_auth.stat().st_ino == original_inode
    assert json.loads(source_auth.read_text(encoding="utf-8")) == {"openai": original_entry}
    assert "Skipping OpenCode credential writeback" in caplog.text


def test_opencode_client_given_older_refreshed_oauth_entry_expect_newer_source_kept(monkeypatch, tmp_path):
    original_entry = _openai_oauth_entry("old-access", "old-refresh", expires=int(time.time()) + 3600)
    source_auth = _write_opencode_auth(
        tmp_path,
        {"openai": original_entry, "zai-coding-plan": {"type": "api", "key": "keep-this-key"}},
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    newer_entry = _openai_oauth_entry("newer-access", "newer-refresh", expires=int(time.time()) + 10800)
    stale_entry = _openai_oauth_entry("stale-access", "stale-refresh", expires=int(time.time()) + 7200)

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        source_credentials = json.loads(source_auth.read_text(encoding="utf-8"))
        source_credentials["openai"] = newer_entry
        source_auth.write_text(json.dumps(source_credentials), encoding="utf-8")
        target_auth.write_text(json.dumps({"openai": stale_entry}), encoding="utf-8")
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")

    assert json.loads(source_auth.read_text(encoding="utf-8")) == {
        "openai": newer_entry,
        "zai-coding-plan": {"type": "api", "key": "keep-this-key"},
    }


def test_opencode_client_given_new_account_and_old_account_refresh_expect_new_account_kept(monkeypatch, tmp_path):
    original_entry = _openai_oauth_entry(
        _oauth_access_token("account-a", 1_767_225_600), "account-a-refresh", expires=1_767_225_600
    )
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry})
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    monkeypatch.setattr(opencode_credentials_mod, "K_OPENCODE_AUTH_LOCK_POLL_SECONDS", 30)
    parent_login = _openai_oauth_entry(
        _oauth_access_token("account-b", 1_798_761_600), "account-b-login", expires=1_798_761_600
    )
    stale_refresh = _openai_oauth_entry(
        _oauth_access_token("account-a", 1_924_992_000), "account-a-refresh-rotated", expires=1_924_992_000
    )

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        source_credentials = json.loads(source_auth.read_text(encoding="utf-8"))
        source_credentials["openai"] = parent_login
        source_auth.write_text(json.dumps(source_credentials), encoding="utf-8")
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        target_auth.write_text(json.dumps({"openai": stale_refresh}), encoding="utf-8")
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    result = OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")

    assert result.text == "OK"
    assert json.loads(source_auth.read_text(encoding="utf-8")) == {"openai": parent_login}


def test_opencode_client_given_unresolved_identity_and_changed_parent_expect_parent_kept(monkeypatch, tmp_path):
    original_entry = _openai_oauth_entry("original-access", "original-refresh", expires=int(time.time()) + 3600)
    parent_login = _openai_oauth_entry("parent-access", "parent-login", expires=int(time.time()) + 7200)
    stale_refresh = _openai_oauth_entry("stale-access", "stale-refresh", expires=int(time.time()) + 10_800)
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry})
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        source_auth.write_text(json.dumps({"openai": parent_login}), encoding="utf-8")
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        target_auth.write_text(json.dumps({"openai": stale_refresh}), encoding="utf-8")
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")

    assert json.loads(source_auth.read_text(encoding="utf-8")) == {"openai": parent_login}


def test_opencode_client_given_same_account_relogin_with_earlier_expiry_expect_parent_kept(monkeypatch, tmp_path):
    original_entry = _openai_oauth_entry(
        _oauth_access_token("account-a", 1_767_225_600), "original-refresh", expires=int(time.time()) + 3600
    )
    parent_login = _openai_oauth_entry(
        _oauth_access_token("account-a", 1_798_761_600), "parent-relogin", expires=int(time.time()) + 1800
    )
    stale_refresh = _openai_oauth_entry(
        _oauth_access_token("account-a", 1_924_992_000), "stale-refresh", expires=int(time.time()) + 10_800
    )
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry})
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        source_auth.write_text(json.dumps({"openai": parent_login}), encoding="utf-8")
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        target_auth.write_text(json.dumps({"openai": stale_refresh}), encoding="utf-8")
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")

    assert json.loads(source_auth.read_text(encoding="utf-8")) == {"openai": parent_login}


def test_opencode_client_given_other_call_advanced_baseline_expect_stale_refresh_rejected(monkeypatch, tmp_path):
    original_entry = _openai_oauth_entry("original-access", "original-refresh", expires=int(time.time()) + 3600)
    first_refresh = _openai_oauth_entry("first-access", "first-refresh", expires=int(time.time()) + 7200)
    stale_refresh = _openai_oauth_entry("stale-access", "stale-refresh", expires=int(time.time()) + 10_800)
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry})
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    _warm_opencode_cache("openai/gpt-5.6-sol")
    stale_call_ready = threading.Event()
    allow_stale_call_to_finish = threading.Event()
    failures: list[Exception] = []

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        prompt_text = cmd[-1]
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        entry = first_refresh if prompt_text == "first call" else stale_refresh
        target_auth.write_text(json.dumps({"openai": entry}), encoding="utf-8")
        if prompt_text == "stale call":
            stale_call_ready.set()
            allow_stale_call_to_finish.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    def run_stale_call() -> None:
        try:
            OpencodeClient(timeout=60).call("stale call", model="openai/gpt-5.6-sol")
        except Exception as exc:  # pragma: no cover - assertion below reports unexpected call failures
            failures.append(exc)

    worker = threading.Thread(target=run_stale_call)
    try:
        worker.start()
        assert stale_call_ready.wait(K_TEST_HANG_GUARD_SECONDS)
        OpencodeClient(timeout=60).call("first call", model="openai/gpt-5.6-sol")
    finally:
        allow_stale_call_to_finish.set()
        worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert not worker.is_alive()
    assert failures == []
    assert json.loads(source_auth.read_text(encoding="utf-8")) == {"openai": first_refresh}


def test_opencode_client_given_unchanged_parent_and_older_refresh_expect_refresh_written_back(monkeypatch, tmp_path):
    original_entry = _openai_oauth_entry("current-access", "current-refresh", expires=int(time.time()) + 7200)
    stale_entry = _openai_oauth_entry("stale-access", "stale-refresh", expires=int(time.time()) + 3600)
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry})
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        target_auth.write_text(json.dumps({"openai": stale_entry}), encoding="utf-8")
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")

    assert json.loads(source_auth.read_text(encoding="utf-8")) == {"openai": stale_entry}


@pytest.mark.parametrize("failure", [BackendDownException("failed"), Cancelled("author", "cancelled")])
def test_opencode_client_given_failure_after_oauth_refresh_expect_refresh_written_back(monkeypatch, tmp_path, failure):
    original_entry = _openai_oauth_entry("old-access", "old-refresh", expires=int(time.time()) - 10)
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry})
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    refreshed = _openai_oauth_entry("new-access", "new-refresh", expires=int(time.time()) + 7200)

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        target_auth.write_text(json.dumps({"openai": refreshed}), encoding="utf-8")
        raise failure

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    with pytest.raises(type(failure)):
        OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")

    assert json.loads(source_auth.read_text(encoding="utf-8")) == {"openai": refreshed}


def test_opencode_client_given_refreshed_oauth_entry_expect_auth_file_mode_0600(monkeypatch, tmp_path):
    original_entry = _openai_oauth_entry("old-access", "old-refresh", expires=int(time.time()) + 3600)
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry})
    source_auth.chmod(0o644)
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    refreshed = _openai_oauth_entry("new-access", "new-refresh", expires=int(time.time()) + 7200)

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        target_auth.write_text(json.dumps({"openai": refreshed}), encoding="utf-8")
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")

    assert source_auth.stat().st_mode & 0o777 == 0o600


def test_opencode_client_given_parallel_calls_with_expired_oauth_expect_single_refresh(monkeypatch, tmp_path):
    original_entry = _openai_oauth_entry("expired-access", "rotating-refresh", expires=int(time.time()) - 10)
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry})
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    _warm_opencode_cache("openai/gpt-5.6-sol")
    observed_refresh_tokens: list[str] = []
    observed_lock = threading.Lock()
    second_call_started = threading.Event()
    refresh_handoff_results: list[bool] = []
    refreshed = _openai_oauth_entry("fresh-access", "rotated-refresh", expires=int(time.time()) + 7200)

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        credentials = json.loads(target_auth.read_text(encoding="utf-8"))
        with observed_lock:
            observed_refresh_tokens.append(credentials["openai"]["refresh"])
        if credentials["openai"]["refresh"] == "rotating-refresh":
            target_auth.write_text(json.dumps({"openai": refreshed}), encoding="utf-8")
            refresh_handoff_results.append(second_call_started.wait(timeout=K_TEST_HANG_GUARD_SECONDS))
        else:
            second_call_started.set()
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)
    errors: list[Exception] = []

    def run_call() -> None:
        try:
            OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")
        except Exception as exc:  # pragma: no cover - assertion below reports unexpected call failures
            errors.append(exc)

    threads = [threading.Thread(target=run_call) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert all(not thread.is_alive() for thread in threads)
    assert not errors
    assert observed_refresh_tokens.count("rotating-refresh") == 1
    assert sorted(observed_refresh_tokens) == ["rotated-refresh", "rotating-refresh"]
    assert refresh_handoff_results == [True]


def test_opencode_client_given_oauth_entry_without_expiry_expect_parallel_calls_not_serialized(monkeypatch, tmp_path):
    source_auth = _write_opencode_auth(
        tmp_path,
        {"openai": {"type": "oauth", "access": "access", "refresh": "refresh"}},
    )
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    _warm_opencode_cache("openai/gpt-5.6-sol")
    first_call_entered = threading.Event()
    release_first_call = threading.Event()
    second_call_entered = threading.Event()
    call_count = 0
    call_count_lock = threading.Lock()
    failures: list[Exception] = []

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        nonlocal call_count
        with call_count_lock:
            call_count += 1
            call_index = call_count
        if call_index == 1:
            first_call_entered.set()
            release_first_call.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        else:
            second_call_entered.set()
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    def run_call() -> None:
        try:
            OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")
        except Exception as exc:  # pragma: no cover - assertion below reports unexpected call failures
            failures.append(exc)

    threads = [threading.Thread(target=run_call) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        assert first_call_entered.wait(K_TEST_HANG_GUARD_SECONDS)
        assert second_call_entered.wait(K_TEST_HANG_GUARD_SECONDS)
    finally:
        release_first_call.set()
        for thread in threads:
            thread.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert all(not thread.is_alive() for thread in threads)
    assert failures == []
    assert call_count == 2


@pytest.mark.parametrize(
    "has_expiry",
    [True, False],
)
def test_opencode_client_given_oauth_entry_changes_during_call_expect_source_updated_before_completion(
    monkeypatch,
    tmp_path,
    has_expiry,
):
    original_entry = {"type": "oauth", "access": "old-access", "refresh": "old-refresh"}
    refreshed_entry = {"type": "oauth", "access": "new-access", "refresh": "new-refresh"}
    if has_expiry:
        original_entry["expires"] = int(time.time()) + 3600
        refreshed_entry["expires"] = int(time.time()) + 7200
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry})
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    child_auth_ready = threading.Event()
    allow_call_to_finish = threading.Event()
    failures: list[Exception] = []

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        target_auth.write_text(json.dumps({"openai": refreshed_entry}), encoding="utf-8")
        child_auth_ready.set()
        allow_call_to_finish.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    def run_call() -> None:
        try:
            OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")
        except Exception as exc:  # pragma: no cover - assertion below reports unexpected call failures
            failures.append(exc)

    worker = threading.Thread(target=run_call)
    try:
        worker.start()
        assert child_auth_ready.wait(K_TEST_HANG_GUARD_SECONDS)
        assert wait_for_test_condition(
            lambda: json.loads(source_auth.read_text(encoding="utf-8")) == {"openai": refreshed_entry}
        )
    finally:
        allow_call_to_finish.set()
        worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert not worker.is_alive()
    assert failures == []


def test_opencode_client_given_other_provider_changes_before_final_read_expect_provider_update_preserved(
    monkeypatch,
    tmp_path,
):
    original_entry = {"type": "oauth", "access": "old-access", "refresh": "old-refresh"}
    refreshed_entry = {"type": "oauth", "access": "new-access", "refresh": "new-refresh"}
    original_other = {"type": "api", "key": "old-key"}
    updated_other = {"type": "api", "key": "concurrent-key"}
    source_auth = _write_opencode_auth(tmp_path, {"openai": original_entry, "anthropic": original_other})
    monkeypatch.setenv("XDG_DATA_HOME", str(source_auth.parents[1]))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    allow_auth_write = threading.Event()
    auth_write_started = threading.Event()
    original_mkstemp = opencode_credentials_mod.tempfile.mkstemp
    changed_parent = False
    failures: list[Exception] = []

    def publish_other_provider_then_allocate_temp(*args, **kwargs):
        nonlocal changed_parent
        result = original_mkstemp(*args, **kwargs)
        if not changed_parent:
            changed_parent = True
            external_descriptor, external_name = original_mkstemp(dir=source_auth.parent)
            with os.fdopen(external_descriptor, "w", encoding="utf-8") as external_file:
                json.dump({"openai": original_entry, "anthropic": updated_other}, external_file)
            os.replace(external_name, source_auth)
        return result

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        target_auth = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        target_auth.write_text(json.dumps({"openai": refreshed_entry}), encoding="utf-8")
        auth_write_started.set()
        allow_auth_write.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)
    monkeypatch.setattr(opencode_credentials_mod.tempfile, "mkstemp", publish_other_provider_then_allocate_temp)

    def run_call() -> None:
        try:
            OpencodeClient(timeout=60).call("prompt", model="openai/gpt-5.6-sol")
        except Exception as exc:  # pragma: no cover - assertion below reports unexpected call failures
            failures.append(exc)

    worker = threading.Thread(target=run_call)
    try:
        worker.start()
        assert auth_write_started.wait(K_TEST_HANG_GUARD_SECONDS)
        assert wait_for_test_condition(
            lambda: json.loads(source_auth.read_text(encoding="utf-8")).get("openai") == refreshed_entry
        )
    finally:
        allow_auth_write.set()
        worker.join(timeout=K_TEST_HANG_GUARD_SECONDS)

    assert not worker.is_alive()
    assert failures == []
    assert json.loads(source_auth.read_text(encoding="utf-8")) == {
        "openai": refreshed_entry,
        "anthropic": updated_other,
    }


def test_opencode_client_given_missing_model_provider_credential_expect_login_error_before_spawn(monkeypatch, tmp_path):
    source_data_home = tmp_path / "source-data"
    source_auth = source_data_home / "opencode" / "auth.json"
    source_auth.parent.mkdir(parents=True)
    source_auth.write_text(json.dumps({"openai": {"type": "oauth", "refresh": "broken-token"}}), encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(source_data_home))
    spawned = False

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        nonlocal spawned
        spawned = True
        return FakeOpencodeProcess(stdout=_opencode_stdout("unexpected"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    with pytest.raises(BackendDownException, match="provider 'zai-coding-plan'.*opencode auth login"):
        OpencodeClient(timeout=60).call("prompt", model="zai-coding-plan/glm-5.3")

    assert not spawned


def test_opencode_client_given_explicit_temp_root_expect_child_temp_is_private(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    captured: dict[str, str] = {}

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        assert env is not None
        captured["tmpdir"] = env["TMPDIR"]
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    OpencodeClient(timeout=60).call("prompt")

    assert Path(captured["tmpdir"]).parent.parent == tmp_path
    assert not Path(captured["tmpdir"]).exists()


def test_opencode_client_given_parallel_large_prompts_expect_unique_attachment_paths(monkeypatch):
    _warm_opencode_cache("opencode-go/glm-5.2")
    prompt = "x" * (K_OPENCODE_INLINE_PROMPT_MAX_BYTES + 1)
    captured: list[Path] = []
    captured_lock = threading.Lock()
    entered = threading.Barrier(2)

    def fake_exec(cmd, *, env, cwd=None, **kwargs):
        prompt_path = Path(cmd[cmd.index("-f") + 1])
        assert prompt_path.read_text(encoding="utf-8") == prompt
        with captured_lock:
            captured.append(prompt_path)
        entered.wait(timeout=K_TEST_HANG_GUARD_SECONDS)
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_exec)

    threads = [threading.Thread(target=lambda: OpencodeClient(timeout=60).call(prompt)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(captured) == 2
    assert captured[0] != captured[1]
    assert all(not path.exists() for path in captured)


def test_opencode_client_given_legacy_execution_mode_env_expect_ignored(monkeypatch):
    monkeypatch.setenv("OPENCODE_EXECUTION_MODE", "sometimes")
    monkeypatch.setattr(
        opencode_mod.subprocess,
        "Popen",
        lambda *args, **kwargs: FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0),
    )

    assert OpencodeClient(timeout=60).call("prompt").text == "OK"


def test_opencode_client_given_successful_run_expect_concatenated_text(monkeypatch):
    monkeypatch.setattr(
        opencode_mod.subprocess,
        "Popen",
        lambda *a, **k: FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0),
    )

    result = OpencodeClient().call("prompt", model="opencode-go/glm-5.2")

    assert result.text == "OK"


def test_opencode_client_given_multiple_text_events_expect_concatenated(monkeypatch):
    monkeypatch.setattr(
        opencode_mod.subprocess,
        "Popen",
        lambda *a, **k: FakeOpencodeProcess(stdout=_opencode_stdout("Hello", " ", "world"), returncode=0),
    )

    result = OpencodeClient().call("prompt")

    assert result.text == "Hello world"


def test_opencode_client_given_success_text_mentions_quota_expect_text_without_quota_error(monkeypatch):
    monkeypatch.setattr(
        opencode_mod.subprocess,
        "Popen",
        lambda *a, **k: FakeOpencodeProcess(
            stdout=_opencode_stdout("The review discusses provider quota risks."),
            stderr="quota wording in a harmless warning",
            returncode=0,
        ),
    )

    result = OpencodeClient().call("prompt")

    assert result.text == "The review discusses provider quota risks."


def test_opencode_client_given_error_event_expect_backend_down_exception(monkeypatch):
    stdout = json.dumps({"type": "error", "error": {"name": "UnknownError", "data": {"message": "Model not found: x"}}})
    monkeypatch.setattr(
        opencode_mod.subprocess, "Popen", lambda *a, **k: FakeOpencodeProcess(stdout=stdout, returncode=1)
    )

    with pytest.raises(BackendDownException):
        OpencodeClient().call("prompt")


def test_opencode_client_given_quota_event_expect_llm_quota_exception(monkeypatch):
    stdout = json.dumps(
        {"type": "error", "error": {"name": "UnknownError", "data": {"message": "usage limit reached"}}}
    )
    monkeypatch.setattr(
        opencode_mod.subprocess, "Popen", lambda *a, **k: FakeOpencodeProcess(stdout=stdout, returncode=1)
    )

    with pytest.raises(LlmQuotaException):
        OpencodeClient().call("prompt")


@pytest.mark.parametrize(
    "stdout",
    [
        "{not-json",
        "[]",
        json.dumps({"type": "text", "part": []}),
        json.dumps({"type": "text", "part": "invalid"}),
        json.dumps({"type": "error", "error": "invalid"}),
        json.dumps({"type": "error", "error": {"data": []}}),
    ],
)
def test_opencode_client_given_malformed_stdout_event_expect_backend_down_exception(monkeypatch, stdout):
    process = FakeOpencodeProcess(pid=4112, stdout=f"{stdout}\n", returncode=1)
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", lambda *_args, **_kwargs: process)

    with pytest.raises(BackendDownException):
        OpencodeClient(timeout=60).call("prompt")


def test_opencode_client_given_empty_output_expect_backend_down_exception(monkeypatch):
    monkeypatch.setattr(
        opencode_mod.subprocess,
        "Popen",
        lambda *a, **k: FakeOpencodeProcess(stdout="", stderr="", returncode=1),
    )

    with pytest.raises(BackendDownException):
        OpencodeClient().call("prompt")


@pytest.mark.parametrize("prompt", ["--help", "-m evil/model", "--pure", "--"])
def test_opencode_client_given_option_like_prompt_expect_prompt_after_end_of_options(monkeypatch, prompt):
    commands: list[list[str]] = []

    def fake_run(cmd, *, env, cwd=None, **kwargs):
        commands.append(cmd)
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_run)

    result = OpencodeClient(timeout=60).call(prompt)

    assert result.text == "OK"
    command = commands[0]
    marker = command.index("--")
    assert command[marker + 1] == prompt
    assert command.index("-m") < marker


def test_opencode_client_given_repo_root_expect_subprocess_working_directory(monkeypatch, tmp_path):
    captured: dict[str, str | None] = {}

    def fake_run(cmd, *, env, cwd=None, **kwargs):
        captured["cwd"] = cwd
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_run)

    OpencodeClient(timeout=60, repo_root=tmp_path).call("prompt")

    assert captured["cwd"] == str(tmp_path)


def test_opencode_client_given_no_repo_root_expect_inherited_working_directory(monkeypatch):
    captured: dict[str, str | None] = {}

    def fake_run(cmd, *, env, cwd=None, **kwargs):
        captured["cwd"] = cwd
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_run)

    OpencodeClient(timeout=60).call("prompt")

    assert captured["cwd"] is None


def test_opencode_client_given_repo_root_expect_popen_cwd_forwarded(monkeypatch, tmp_path):
    process = FakeOpencodeProcess(pid=4110, stdout=_opencode_stdout("OK"))
    captured: dict[str, object] = {}

    def fake_popen(_cmd, **kwargs):
        captured.update(kwargs)
        return process

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_popen)

    result = OpencodeClient(timeout=60, repo_root=tmp_path).call("prompt")

    assert result.text == "OK"
    assert captured["cwd"] == str(tmp_path)


def test_opencode_client_given_inherited_opencode_override_env_expect_stripped_from_child(monkeypatch):
    monkeypatch.setenv("OPENCODE_CONFIG", "/nonexistent/override.json")
    monkeypatch.setenv("OPENCODE_PERMISSION", "disable")
    captured: dict[str, str] = {}

    def fake_run(cmd, *, env, cwd=None, **kwargs):
        captured.update(env)
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_run)

    OpencodeClient(timeout=60).call("prompt")

    assert "OPENCODE_CONFIG" not in captured
    assert "OPENCODE_PERMISSION" not in captured


def test_opencode_client_given_credential_env_expect_preserved_in_child(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-opencode-key")
    monkeypatch.setenv("GLM_API_KEY", "test-glm-key")
    captured: dict[str, str] = {}

    def fake_run(cmd, *, env, cwd=None, **kwargs):
        captured.update(env)
        return FakeOpencodeProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_run)

    OpencodeClient(timeout=60).call("prompt")

    assert captured["OPENCODE_API_KEY"] == "test-opencode-key"
    assert captured["GLM_API_KEY"] == "test-glm-key"


def test_opencode_client_given_quota_stderr_and_hanging_stdout_expect_prompt_quota_exception(monkeypatch) -> None:
    monkeypatch.setattr(opencode_mod, "K_OPENCODE_STDERR_QUOTA_GRACE_SECONDS", 0.1)
    stdout_stream = FakeHangingTextStream([])
    process = FakeOpencodeProcess(
        pid=4111,
        stderr=(
            'level=ERROR message="stream error" small=false '
            'agent=lesson-author error="The usage limit has been reached"\n'
        ),
        returncode=None,
        stdout_stream=stdout_stream,
    )
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        opencode_mod.os,
        "killpg",
        lambda _pid, sent_signal: process.receive_signal(sent_signal),
    )

    try:
        with pytest.raises(LlmQuotaException, match="usage limit"):
            OpencodeClient(timeout=60).call("prompt", agent="lesson-author")
        assert process.signals == [base_mod.signal.SIGTERM]
    finally:
        stdout_stream.close()


def test_opencode_client_given_budget_started_at_call_entry_expect_setup_included_in_deadline(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def consume_setup_budget(*args, **kwargs):
        clock[0] = 1040.0
        return TemporaryDirectory(*args, **kwargs)

    def start_process(*args, **kwargs):
        clock[0] = 1060.0
        return FakeOpencodeProcess(stdout=_opencode_stdout("too late"))

    monkeypatch.setattr(opencode_runtime_mod.tempfile, "TemporaryDirectory", consume_setup_budget)
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", start_process)

    with pytest.raises(BackendDownException, match="timed out after 60s"):
        OpencodeClient(timeout=60).call("prompt")


def test_opencode_client_given_setup_budget_expired_expect_backend_down_before_popen(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(opencode_mod.time, "monotonic", lambda: clock[0])

    def expire_budget_during_setup(*args, **kwargs):
        clock[0] = 5000.0
        return TemporaryDirectory(*args, **kwargs)

    monkeypatch.setattr(opencode_credentials_mod.tempfile, "TemporaryDirectory", expire_budget_during_setup)

    def fake_popen(*_args, **_kwargs):
        raise AssertionError("an expired setup budget must not start the subprocess")

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_popen)

    with pytest.raises(BackendDownException, match="before the process started"):
        OpencodeClient(timeout=60).call("prompt")


def _opencode_stdout(*texts):
    lines = [json.dumps({"type": "step_start", "part": {"type": "step-start"}})]
    for text in texts:
        lines.append(json.dumps({"type": "text", "part": {"type": "text", "text": text}}))
    lines.append(json.dumps({"type": "step_finish", "part": {"type": "step-finish", "reason": "stop"}}))
    return "\n".join(lines)


def _warm_opencode_cache(model: str) -> None:
    cache_home = Path(os.environ["XDG_CACHE_HOME"]) / K_OPENCODE_SHARED_CACHE_RELATIVE_PATH
    with opencode_runtime_mod.hold_opencode_cache(
        cache_home, model, deadline=time.monotonic() + 5, timeout=5, is_cancelled=None
    ) as mark_warm:
        mark_warm()


def _openai_oauth_entry(access: str, refresh: str, *, expires: int) -> dict[str, object]:
    return {"type": "oauth", "access": access, "refresh": refresh, "expires": expires}


def _oauth_access_token(account_id: str, expires_at: int) -> str:
    payload = (
        urlsafe_b64encode(
            json.dumps({"exp": expires_at, "https://api.openai.com/auth": {"chatgpt_account_id": account_id}}).encode()
        )
        .decode()
        .rstrip("=")
    )
    return f"eyJhbGciOiJub25lIn0.{payload}.signature"


def _write_opencode_auth(tmp_path: Path, credentials: dict[str, object]) -> Path:
    source_auth = tmp_path / "source-data" / "opencode" / "auth.json"
    source_auth.parent.mkdir(parents=True)
    source_auth.write_text(json.dumps(credentials), encoding="utf-8")
    return source_auth
