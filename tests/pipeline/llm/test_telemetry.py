import json

import pytest

from lesson_builder.pipeline.llm.base import AttemptOutcome, LlmResponse
from lesson_builder.pipeline.llm.exceptions import BackendDownException
from lesson_builder.pipeline.llm.invocation import Agent, BackendStep
from lesson_builder.pipeline.llm.telemetry import log_llm_call, log_llm_failure

from ..fakes import FakeLlmClient


def _response() -> LlmResponse:
    return LlmResponse(
        text="hei",
        client="zai",
        model="glm-5.2",
        input_tokens=100,
        output_tokens=40,
        latency_ms=1234,
        attempts=(AttemptOutcome("zai", "glm-5.2", "ok"),),
    )


def test_log_llm_call_given_response_expect_jsonl_record_appended(tmp_path, monkeypatch):
    monkeypatch.delenv("NORSK_LLM_LOG_PATH", raising=False)  # neutralize the autouse "off"
    target = tmp_path / "llm_calls.jsonl"

    log_llm_call("reviewer", _response(), path=target)
    log_llm_call("reviewer", _response(), path=target)

    lines = target.read_text().strip().splitlines()
    assert len(lines) == 2
    record = json.loads(lines[0])
    assert record["agent"] == "reviewer"
    assert record["client"] == "zai"
    assert record["input_tokens"] == 100
    assert record["attempts"] == [
        {"client": "zai", "model": "glm-5.2", "outcome": "ok", "error": None}
    ]
    assert "ts" in record


def test_log_llm_call_given_env_off_expect_no_file(tmp_path, monkeypatch):
    monkeypatch.setenv("NORSK_LLM_LOG_PATH", "off")
    target = tmp_path / "llm_calls.jsonl"

    log_llm_call("reviewer", _response(), path=target)

    assert not target.exists()


def test_agent_invoke_given_call_expect_telemetry_record_written(tmp_path, monkeypatch):
    target = tmp_path / "llm_calls.jsonl"
    monkeypatch.setenv("NORSK_LLM_LOG_PATH", str(target))
    clients = {"fake": FakeLlmClient.from_fn(lambda p, model=None: "ok", name="fake")}
    agent = Agent(name="author", chain=[BackendStep(client="fake", model="m")], clients=clients)

    agent.invoke("hi")

    record = json.loads(target.read_text().strip())
    assert record["agent"] == "author"
    assert record["model"] == "m"


def test_log_llm_failure_given_attempts_expect_failed_record(tmp_path, monkeypatch):
    monkeypatch.delenv("NORSK_LLM_LOG_PATH", raising=False)
    target = tmp_path / "llm_calls.jsonl"

    attempts = (
        AttemptOutcome("zai", "glm-5.2", "down", "boom"),
        AttemptOutcome("codex", "gpt-5.5", "quota", "limited"),
    )
    log_llm_failure("author", attempts, path=target)

    record = json.loads(target.read_text().strip())
    assert record["agent"] == "author"
    assert record["failed"] is True
    assert record["client"] is None
    assert record["input_tokens"] is None
    assert [a["outcome"] for a in record["attempts"]] == ["down", "quota"]


def test_agent_invoke_given_all_backends_fail_expect_failure_record_and_reraise(tmp_path, monkeypatch):
    target = tmp_path / "llm_calls.jsonl"
    monkeypatch.setenv("NORSK_LLM_LOG_PATH", str(target))
    clients = {
        "a": FakeLlmClient.raising(BackendDownException("a-down"), name="a"),
        "b": FakeLlmClient.raising(BackendDownException("b-down"), name="b"),
    }
    agent = Agent(
        name="author",
        chain=[BackendStep(client="a", model="m1"), BackendStep(client="b", model="m2")],
        clients=clients,
    )

    with pytest.raises(BackendDownException):
        agent.invoke("hi")

    record = json.loads(target.read_text().strip())
    assert record["agent"] == "author"
    assert record["failed"] is True
    assert [(a["client"], a["outcome"]) for a in record["attempts"]] == [("a", "down"), ("b", "down")]
