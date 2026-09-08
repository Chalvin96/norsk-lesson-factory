import fcntl
import json
import os
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

import lesson_builder.clients.llm.base as base_mod
import lesson_builder.clients.llm.jobs as jobs_mod
import lesson_builder.clients.llm.opencode as opencode_mod
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.exceptions import LlmCancelledException
from lesson_builder.clients.llm.exceptions import LlmQuotaException
from lesson_builder.clients.llm.invocation import JobRunner
from lesson_builder.clients.llm.opencode import OpencodeClient
from lesson_builder.clients.llm.settings import K_OPENCODE_INLINE_PROMPT_MAX_BYTES
from tests.clients.llm.fakes import FakeCompletedProcess
from tests.clients.llm.fakes import FakeHangingTextStream
from tests.clients.llm.fakes import FakeOpencodeProcess


def test_opencode_client_given_agent_and_variant_expect_cli_flags(monkeypatch):
    commands: list[list[str]] = []

    def fake_run(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        commands.append(cmd)
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_run)

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
    monkeypatch.setattr(opencode_mod.subprocess, "Popen", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        opencode_mod.os,
        "killpg",
        lambda _pid, sent_signal: process.receive_signal(sent_signal),
    )
    monotonic_values = iter([0.0, 1.0, 2.0, 5000.0])
    monkeypatch.setattr(opencode_mod.time, "monotonic", lambda: next(monotonic_values, 5000.0))

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
    deadline = time.monotonic() + 1
    while not base_mod.K_ACTIVE_CLI_PROCESSES and time.monotonic() < deadline:
        time.sleep(0.001)
    jobs_mod.cancel_active_clients()
    worker.join(timeout=2)

    try:
        assert not worker.is_alive()
        assert isinstance(errors[0], LlmCancelledException)
        assert len(processes) == 1
        assert processes[0].signals == [base_mod.signal.SIGTERM]
        assert base_mod.K_ACTIVE_CLI_PROCESSES == {}
    finally:
        jobs_mod.reset_client_cancellation()


def test_job_runner_given_cancellation_before_registration_expect_no_retry_and_reset_allows_next_call(
    monkeypatch,
) -> None:
    processes: list[FakeOpencodeProcess] = []
    popen_started = threading.Event()
    release_popen = threading.Event()

    def fake_popen(*_args, **_kwargs) -> FakeOpencodeProcess:
        if not processes:
            popen_started.set()
            if not release_popen.wait(timeout=1):
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
        assert popen_started.wait(timeout=1)
        jobs_mod.cancel_active_clients()
        cancellation_requested = True
    finally:
        if not cancellation_requested:
            jobs_mod.cancel_active_clients()
        release_popen.set()
        worker.join(timeout=2)

    try:
        assert not worker.is_alive()
        assert isinstance(errors[0], LlmCancelledException)
        assert len(processes) == 1
        assert processes[0].signals == [base_mod.signal.SIGTERM]
        assert base_mod.K_ACTIVE_CLI_PROCESSES == {}
    finally:
        jobs_mod.reset_client_cancellation()

    assert OpencodeClient(timeout=60).call("prompt").text == "OK"
    assert len(processes) == 2


def test_opencode_client_given_large_prompt_expect_file_attachment(monkeypatch):
    calls: list[tuple[list[str], str]] = []
    prompt = "x" * (K_OPENCODE_INLINE_PROMPT_MAX_BYTES + 1)

    def fake_exec(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        calls.append((cmd, prompt_text))
        prompt_path = Path(cmd[cmd.index("-f") + 1])
        assert prompt_path.read_text(encoding="utf-8") == prompt
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_exec)

    result = OpencodeClient(timeout=60).call(
        prompt,
        model="openai/gpt-5.6-sol",
        agent="catalog-review",
        variant="xhigh",
    )

    assert result.text == "OK"
    assert len(calls) == 1
    command, prompt_text = calls[0]
    assert prompt_text == ""
    assert prompt not in command
    assert command[command.index("-f") + 3] == (
        "Execute the attached prompt as the complete task and emit its requested output."
    )


def test_opencode_client_given_no_options_expect_no_provider_flags(monkeypatch):
    commands: list[list[str]] = []

    def fake_run(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        commands.append(cmd)
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_run)

    OpencodeClient().call("prompt")

    assert "--agent" not in commands[0]
    assert "--variant" not in commands[0]


def test_opencode_client_given_explicit_timeout_expect_subprocess_deadline_forwarded(monkeypatch):
    timeouts: list[int] = []

    def fake_run(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        timeouts.append(timeout)
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_run)

    OpencodeClient(timeout=240).call("prompt")

    assert timeouts == [240]


@pytest.mark.parametrize("timeout", [0, -5])
def test_opencode_client_given_non_positive_integer_timeout_expect_value_error(timeout):
    with pytest.raises(ValueError, match="timeout must be"):
        OpencodeClient(timeout=timeout)


@pytest.mark.parametrize("timeout", [True, 1.5, "30"])
def test_opencode_client_given_non_integer_timeout_expect_type_error(timeout):
    with pytest.raises(TypeError, match="timeout must be"):
        OpencodeClient(timeout=timeout)


def test_opencode_client_given_parallel_calls_expect_concurrent_processes(monkeypatch):
    active = 0
    maximum_active = 0
    state_lock = threading.Lock()
    entered = threading.Barrier(2)
    errors: list[Exception] = []

    def fake_exec(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        entered.wait(timeout=2)
        time.sleep(0.01)
        with state_lock:
            active -= 1
        return FakeCompletedProcess(stdout=_opencode_stdout(prompt_text), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_exec)

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
    entered = threading.Barrier(2)

    def fake_exec(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        entered.wait(timeout=2)
        if prompt_text == "fail":
            return FakeCompletedProcess(stdout="", stderr="failed", returncode=1)
        return FakeCompletedProcess(stdout=_opencode_stdout("success"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_exec)
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


def test_opencode_client_given_parallel_calls_expect_unique_xdg_directories_and_cleanup(monkeypatch, tmp_path):
    source_data_home = tmp_path / "source-data"
    source_auth = source_data_home / "opencode" / "auth.json"
    source_auth.parent.mkdir(parents=True)
    source_auth.write_text('{"provider":"test"}', encoding="utf-8")
    monkeypatch.setenv("XDG_DATA_HOME", str(source_data_home))
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))

    captured: list[tuple[tuple[Path, Path, Path], str]] = []
    captured_lock = threading.Lock()
    entered = threading.Barrier(2)

    def fake_exec(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        assert env is not None
        xdg_dirs = tuple(Path(env[key]) for key in ("XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME"))
        auth_path = Path(env["XDG_DATA_HOME"]) / "opencode" / "auth.json"
        with captured_lock:
            captured.append((xdg_dirs, auth_path.read_text(encoding="utf-8")))
        entered.wait(timeout=2)
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_exec)

    def run_call(prompt: str) -> None:
        OpencodeClient(timeout=60).call(prompt)

    threads = [threading.Thread(target=run_call, args=(prompt,)) for prompt in ("first", "second")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(captured) == 2
    assert captured[0][0] != captured[1][0]
    assert captured[0][1] == '{"provider":"test"}'
    assert captured[1][1] == '{"provider":"test"}'
    for xdg_dirs, _ in captured:
        assert all(not directory.exists() for directory in xdg_dirs)
    assert os.environ["XDG_DATA_HOME"] == str(source_data_home)
    assert source_auth.read_text(encoding="utf-8") == '{"provider":"test"}'


def test_opencode_client_given_explicit_temp_root_expect_child_temp_is_private(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCODE_TEMP_ROOT", str(tmp_path))
    captured: dict[str, str] = {}

    def fake_exec(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        assert env is not None
        captured["tmpdir"] = env["TMPDIR"]
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_exec)

    OpencodeClient(timeout=60).call("prompt")

    assert Path(captured["tmpdir"]).parent.parent == tmp_path
    assert not Path(captured["tmpdir"]).exists()


def test_opencode_client_given_parallel_large_prompts_expect_unique_attachment_paths(monkeypatch):
    prompt = "x" * (K_OPENCODE_INLINE_PROMPT_MAX_BYTES + 1)
    captured: list[Path] = []
    captured_lock = threading.Lock()
    entered = threading.Barrier(2)

    def fake_exec(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        prompt_path = Path(cmd[cmd.index("-f") + 1])
        assert prompt_path.read_text(encoding="utf-8") == prompt
        with captured_lock:
            captured.append(prompt_path)
        entered.wait(timeout=2)
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_exec)

    threads = [threading.Thread(target=lambda: OpencodeClient(timeout=60).call(prompt)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(captured) == 2
    assert captured[0] != captured[1]
    assert all(not path.exists() for path in captured)


def test_opencode_client_given_serial_mode_expect_compatibility_lease(monkeypatch, tmp_path):
    active = 0
    maximum_active = 0
    state_lock = threading.Lock()

    def fake_exec(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        nonlocal active, maximum_active
        with state_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.01)
        with state_lock:
            active -= 1
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setenv("OPENCODE_EXECUTION_MODE", "serial")
    monkeypatch.setattr(opencode_mod, "K_OPENCODE_DEFAULT_LOCK_FILENAME", str(tmp_path / "serial.lock"))
    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_exec)

    threads = [threading.Thread(target=lambda: OpencodeClient(timeout=60).call("prompt")) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert maximum_active == 1


def test_opencode_client_given_invalid_execution_mode_expect_configuration_error(monkeypatch):
    monkeypatch.setenv("OPENCODE_EXECUTION_MODE", "sometimes")

    with pytest.raises(ValueError, match="OPENCODE_EXECUTION_MODE"):
        OpencodeClient(timeout=60)


def test_opencode_client_given_successful_run_expect_concatenated_text(monkeypatch):
    monkeypatch.setattr(
        opencode_mod,
        "_run_opencode",
        lambda *a, **k: FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0),
    )

    result = OpencodeClient().call("prompt", model="opencode-go/glm-5.2")

    assert result.text == "OK"


def test_opencode_client_given_multiple_text_events_expect_concatenated(monkeypatch):
    monkeypatch.setattr(
        opencode_mod,
        "_run_opencode",
        lambda *a, **k: FakeCompletedProcess(stdout=_opencode_stdout("Hello", " ", "world"), returncode=0),
    )

    result = OpencodeClient().call("prompt")

    assert result.text == "Hello world"


def test_opencode_client_given_success_text_mentions_quota_expect_text_without_quota_error(monkeypatch):
    monkeypatch.setattr(
        opencode_mod,
        "_run_opencode",
        lambda *a, **k: FakeCompletedProcess(
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
        opencode_mod, "_run_opencode", lambda *a, **k: FakeCompletedProcess(stdout=stdout, returncode=1)
    )

    with pytest.raises(BackendDownException):
        OpencodeClient().call("prompt")


def test_opencode_client_given_quota_event_expect_llm_quota_exception(monkeypatch):
    stdout = json.dumps(
        {"type": "error", "error": {"name": "UnknownError", "data": {"message": "usage limit reached"}}}
    )
    monkeypatch.setattr(
        opencode_mod, "_run_opencode", lambda *a, **k: FakeCompletedProcess(stdout=stdout, returncode=1)
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
        opencode_mod,
        "_run_opencode",
        lambda *a, **k: FakeCompletedProcess(stdout="", stderr="", returncode=1),
    )

    with pytest.raises(BackendDownException):
        OpencodeClient().call("prompt")


@pytest.mark.parametrize("prompt", ["--help", "-m evil/model", "--pure", "--"])
def test_opencode_client_given_option_like_prompt_expect_prompt_after_end_of_options(monkeypatch, prompt):
    commands: list[list[str]] = []

    def fake_run(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        commands.append(cmd)
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_run)

    result = OpencodeClient(timeout=60).call(prompt)

    assert result.text == "OK"
    command = commands[0]
    marker = command.index("--")
    assert command[marker + 1] == prompt
    assert command.index("-m") < marker


def test_opencode_client_given_repo_root_expect_subprocess_working_directory(monkeypatch, tmp_path):
    captured: dict[str, str | None] = {}

    def fake_run(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        captured["cwd"] = cwd
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_run)

    OpencodeClient(timeout=60, repo_root=tmp_path).call("prompt")

    assert captured["cwd"] == str(tmp_path)


def test_opencode_client_given_no_repo_root_expect_inherited_working_directory(monkeypatch):
    captured: dict[str, str | None] = {}

    def fake_run(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        captured["cwd"] = cwd
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_run)

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

    def fake_run(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        captured.update(env)
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_run)

    OpencodeClient(timeout=60).call("prompt")

    assert "OPENCODE_CONFIG" not in captured
    assert "OPENCODE_PERMISSION" not in captured


def test_opencode_client_given_credential_env_expect_preserved_in_child(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "test-opencode-key")
    monkeypatch.setenv("GLM_API_KEY", "test-glm-key")
    captured: dict[str, str] = {}

    def fake_run(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        captured.update(env)
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_run)

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


def test_opencode_client_given_budget_started_at_call_entry_expect_deadline_fixed_before_setup(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(opencode_mod.time, "monotonic", lambda: clock[0])
    captured: dict[str, float] = {}

    def fake_run(cmd, prompt_text, *, timeout, deadline, env, quota_fn, cwd=None):
        clock[0] = 5000.0
        captured["deadline"] = deadline
        return FakeCompletedProcess(stdout=_opencode_stdout("OK"), returncode=0)

    monkeypatch.setattr(opencode_mod, "_run_opencode", fake_run)

    OpencodeClient(timeout=60).call("prompt")

    assert captured["deadline"] == 1060.0


def test_opencode_client_given_setup_budget_expired_expect_backend_down_before_popen(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(opencode_mod.time, "monotonic", lambda: clock[0])

    def expire_budget_during_setup(*args, **kwargs):
        clock[0] = 5000.0
        return TemporaryDirectory(*args, **kwargs)

    monkeypatch.setattr(opencode_mod.tempfile, "TemporaryDirectory", expire_budget_during_setup)

    def fake_popen(*_args, **_kwargs):
        raise AssertionError("an expired setup budget must not start the subprocess")

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_popen)

    with pytest.raises(BackendDownException, match="before the process started"):
        OpencodeClient(timeout=60).call("prompt")


def test_opencode_client_given_serial_slot_held_expect_deadline_error_not_unbounded_wait(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("OPENCODE_EXECUTION_MODE", "serial")
    lock_name = str(tmp_path / "serial.lock")
    monkeypatch.setattr(opencode_mod, "K_OPENCODE_DEFAULT_LOCK_FILENAME", lock_name)
    with opencode_mod.K_OPENCODE_PROCESS_LOCKS_GUARD:
        local_lock = opencode_mod.K_OPENCODE_PROCESS_LOCKS.setdefault(lock_name, threading.Lock())
    assert local_lock.acquire(timeout=1)
    monotonic_values = iter([0.0, 1.0, 5000.0])
    monkeypatch.setattr(opencode_mod.time, "monotonic", lambda: next(monotonic_values, 5000.0))

    def fake_popen(*_args, **_kwargs):
        raise AssertionError("the serial slot must time out before the process starts")

    monkeypatch.setattr(opencode_mod.subprocess, "Popen", fake_popen)

    try:
        with pytest.raises(BackendDownException, match="serial execution slot"):
            OpencodeClient(timeout=60).call("prompt")
    finally:
        local_lock.release()


def test_opencode_client_given_local_serial_slot_held_and_cancellation_requested_expect_cancellation(
    monkeypatch,
    tmp_path,
):
    lock_name = str(tmp_path / "serial.lock")
    monkeypatch.setenv("OPENCODE_EXECUTION_MODE", "serial")
    monkeypatch.setattr(opencode_mod, "K_OPENCODE_DEFAULT_LOCK_FILENAME", lock_name)
    with opencode_mod.K_OPENCODE_PROCESS_LOCKS_GUARD:
        local_lock = opencode_mod.K_OPENCODE_PROCESS_LOCKS.setdefault(lock_name, threading.Lock())
    assert local_lock.acquire(timeout=1)
    cancellation_states = iter((False, True))
    monkeypatch.setattr(
        opencode_mod,
        "is_cli_cancellation_requested",
        lambda: next(cancellation_states, True),
    )

    try:
        with pytest.raises(LlmCancelledException, match="cancelled"):
            OpencodeClient(timeout=60).call("prompt")
    finally:
        local_lock.release()


def test_opencode_client_given_file_serial_slot_held_and_cancellation_requested_expect_cancellation(
    monkeypatch,
    tmp_path,
):
    lock_path = tmp_path / "serial.lock"
    monkeypatch.setenv("OPENCODE_EXECUTION_MODE", "serial")
    monkeypatch.setattr(opencode_mod, "K_OPENCODE_DEFAULT_LOCK_FILENAME", str(lock_path))
    handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    cancellation_states = iter((False, False, True))
    monkeypatch.setattr(
        opencode_mod,
        "is_cli_cancellation_requested",
        lambda: next(cancellation_states, True),
    )

    try:
        with pytest.raises(LlmCancelledException, match="cancelled"):
            OpencodeClient(timeout=60).call("prompt")
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def test_opencode_client_given_held_file_serial_slot_expect_deadline_error(monkeypatch, tmp_path):
    lock_path = tmp_path / "serial.lock"
    monkeypatch.setenv("OPENCODE_EXECUTION_MODE", "serial")
    monkeypatch.setattr(opencode_mod, "K_OPENCODE_DEFAULT_LOCK_FILENAME", str(lock_path))
    handle = lock_path.open("a+", encoding="utf-8")
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    started = time.monotonic()
    try:
        with pytest.raises(BackendDownException, match="serial execution slot"):
            OpencodeClient(timeout=1).call("prompt")
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    assert time.monotonic() - started < 5


def _opencode_stdout(*texts):
    lines = [json.dumps({"type": "step_start", "part": {"type": "step-start"}})]
    for text in texts:
        lines.append(json.dumps({"type": "text", "part": {"type": "text", "text": text}}))
    lines.append(json.dumps({"type": "step_finish", "part": {"type": "step-finish", "reason": "stop"}}))
    return "\n".join(lines)
