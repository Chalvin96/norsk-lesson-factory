import json
from pathlib import Path

import pytest

import lesson_builder.pipeline.llm.base as base_mod
from lesson_builder.pipeline.llm.clients.codex import CodexClient
from lesson_builder.pipeline.llm.clients.opencode import OpencodeClient
from lesson_builder.pipeline.llm.exceptions import BackendDownException, LlmQuotaException


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_codex_client_given_successful_exec_expect_output_text(monkeypatch):
    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=None):
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text("hello from codex")
        return _Proc(stderr="warnings", returncode=0)

    monkeypatch.setattr(base_mod.subprocess, "run", fake_run)

    result = CodexClient().call("prompt", model="gpt-5.5")

    assert result.text == "hello from codex"


def test_codex_client_given_success_expect_response_with_none_tokens(monkeypatch):
    def fake_run(cmd, input=None, capture_output=True, text=True, timeout=None):
        out_path = Path(cmd[cmd.index("-o") + 1])
        out_path.write_text("hello from codex")
        return _Proc(stderr="warnings", returncode=0)

    monkeypatch.setattr(base_mod.subprocess, "run", fake_run)

    response = CodexClient().call("prompt", model="gpt-5.5")

    assert response.text == "hello from codex"
    assert response.client == "codex"
    assert response.model == "gpt-5.5"
    assert response.input_tokens is None
    assert response.output_tokens is None


def test_codex_client_given_quota_signal_expect_llm_quota_exception(monkeypatch):
    monkeypatch.setattr(
        base_mod.subprocess, "run",
        lambda *a, **k: _Proc(stderr="rate limit exceeded", returncode=1),
    )

    with pytest.raises(LlmQuotaException):
        CodexClient().call("prompt")


def test_codex_client_given_real_usage_limit_message_expect_llm_quota_exception(monkeypatch):
    message = (
        "You've hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), "
        "visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again"
    )
    monkeypatch.setattr(
        base_mod.subprocess, "run",
        lambda *a, **k: _Proc(stderr=message, returncode=1),
    )

    with pytest.raises(LlmQuotaException):
        CodexClient().call("prompt")


def test_codex_client_given_missing_cli_expect_backend_down_exception(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("codex")

    monkeypatch.setattr(base_mod.subprocess, "run", boom)

    with pytest.raises(BackendDownException):
        CodexClient().call("prompt")


def test_codex_client_given_timeout_expect_backend_down_exception(monkeypatch):
    def boom(*a, **k):
        raise base_mod.subprocess.TimeoutExpired(cmd="codex", timeout=600)

    monkeypatch.setattr(base_mod.subprocess, "run", boom)

    with pytest.raises(BackendDownException):
        CodexClient().call("prompt")


def test_codex_client_given_nonzero_exit_no_output_expect_backend_down_exception(monkeypatch):
    monkeypatch.setattr(
        base_mod.subprocess, "run",
        lambda *a, **k: _Proc(stderr="boom", returncode=2),
    )

    with pytest.raises(BackendDownException):
        CodexClient().call("prompt")


def _opencode_stdout(*texts):
    lines = [json.dumps({"type": "step_start", "part": {"type": "step-start"}})]
    for text in texts:
        lines.append(json.dumps({"type": "text", "part": {"type": "text", "text": text}}))
    lines.append(json.dumps({"type": "step_finish", "part": {"type": "step-finish", "reason": "stop"}}))
    return "\n".join(lines)


def test_opencode_client_given_successful_run_expect_concatenated_text(monkeypatch):
    monkeypatch.setattr(
        base_mod.subprocess, "run",
        lambda *a, **k: _Proc(stdout=_opencode_stdout("OK"), returncode=0),
    )

    result = OpencodeClient().call("prompt", model="opencode-go/glm-5.2")

    assert result.text == "OK"


def test_opencode_client_given_multiple_text_events_expect_concatenated(monkeypatch):
    monkeypatch.setattr(
        base_mod.subprocess, "run",
        lambda *a, **k: _Proc(stdout=_opencode_stdout("Hello", " ", "world"), returncode=0),
    )

    result = OpencodeClient().call("prompt")

    assert result.text == "Hello world"


def test_opencode_client_given_error_event_expect_backend_down_exception(monkeypatch):
    stdout = json.dumps(
        {"type": "error", "error": {"name": "UnknownError", "data": {"message": "Model not found: x"}}}
    )
    monkeypatch.setattr(base_mod.subprocess, "run", lambda *a, **k: _Proc(stdout=stdout, returncode=1))

    with pytest.raises(BackendDownException):
        OpencodeClient().call("prompt")


def test_opencode_client_given_quota_event_expect_llm_quota_exception(monkeypatch):
    stdout = json.dumps(
        {"type": "error", "error": {"name": "UnknownError", "data": {"message": "usage limit reached"}}}
    )
    monkeypatch.setattr(base_mod.subprocess, "run", lambda *a, **k: _Proc(stdout=stdout, returncode=1))

    with pytest.raises(LlmQuotaException):
        OpencodeClient().call("prompt")


def test_opencode_client_given_empty_output_expect_backend_down_exception(monkeypatch):
    monkeypatch.setattr(base_mod.subprocess, "run", lambda *a, **k: _Proc(stdout="", stderr="", returncode=1))

    with pytest.raises(BackendDownException):
        OpencodeClient().call("prompt")
