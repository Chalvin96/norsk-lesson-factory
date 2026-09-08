import json

import pytest
from pydantic import BaseModel

from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.clients.llm.exceptions import LlmQuotaException
from lesson_builder.clients.llm.invocation import JobRunner
from lesson_builder.clients.llm.invocation import build_structured_json_prompt
from tests.clients.llm.fakes import FakeLlmClient


class _Schema(BaseModel):
    color: str
    count: int


# ── build_structured_json_prompt ────────────────────────────────────────────


def test_build_structured_json_prompt_given_prompt_and_schema_expect_instruction_with_schema_json():
    instruction = build_structured_json_prompt("Pick one farge.", _Schema)

    assert "Pick one farge." in instruction
    assert "single JSON object matching this schema" in instruction
    assert json.loads(instruction[instruction.index("{") :]) == _Schema.model_json_schema()


def test_build_structured_json_prompt_given_unicode_schema_expect_unescaped_description():
    class AnnotatedSchema(BaseModel):
        farge: str = "Bokmål Norwegian color name"

    instruction = build_structured_json_prompt("Pick.", AnnotatedSchema)

    assert "Bokmål" in instruction


def test_structured_invoke_given_first_attempt_expect_shared_helper_prompt():
    prompts: list[str] = []
    client = FakeLlmClient.from_fn(lambda p, model=None: prompts.append(p) or '{"color": "red", "count": 1}', name="t")

    JobRunner("t", client=client).structured(_Schema).invoke("pick")

    assert prompts[0] == build_structured_json_prompt("pick", _Schema)


def test_job_runner_given_transient_backend_failures_expect_fourth_attempt_success(monkeypatch):
    calls = []

    def flaky(prompt, model=None):
        calls.append("flaky")
        if len(calls) < 4:
            raise BackendDownException("temporary outage")
        return "recovered"

    monkeypatch.setattr("lesson_builder.clients.llm.invocation.time.sleep", lambda _: None)
    response = JobRunner(
        "flaky",
        client=FakeLlmClient.from_fn(flaky, name="flaky"),
        model="m1",
    ).invoke_response("hi")

    assert response.text == "recovered"
    assert calls == ["flaky"] * 4
    assert [attempt.outcome for attempt in response.attempts] == ["down", "down", "down", "ok"]


def test_job_runner_given_persistent_backend_down_expect_four_attempts_then_raise(monkeypatch):
    calls = []

    def down(prompt, model=None):
        calls.append("down")
        raise BackendDownException("persistent outage")

    monkeypatch.setattr("lesson_builder.clients.llm.invocation.time.sleep", lambda _: None)
    with pytest.raises(BackendDownException) as excinfo:
        JobRunner("down", client=FakeLlmClient.from_fn(down, name="down"), model="m1").invoke_response("hi")

    assert calls == ["down"] * 4
    assert len(excinfo.value.attempts) == 4
    assert all(attempt.outcome == "down" for attempt in excinfo.value.attempts)


def test_job_runner_given_quota_exhaustion_expect_no_retry(monkeypatch):
    calls = []

    def quota(prompt, model=None):
        calls.append("quota")
        raise LlmQuotaException("quota exhausted")

    monkeypatch.setattr("lesson_builder.clients.llm.invocation.time.sleep", lambda _: None)
    with pytest.raises(LlmQuotaException):
        JobRunner("quota", client=FakeLlmClient.from_fn(quota, name="quota"), model="m1").invoke_response("hi")

    assert calls == ["quota"]


def test_job_runner_given_parse_exception_expect_immediate_propagation():
    tried = []

    def parse_fail(prompt, model=None):
        tried.append("first")
        raise LlmParseException("bad json")

    agent = JobRunner("first", client=FakeLlmClient.from_fn(parse_fail, name="first"))
    with pytest.raises(LlmParseException):
        agent.invoke_response("hi")
    assert tried == ["first"]


def test_job_runner_given_prompt_expect_backend_text():
    agent = JobRunner("t", client=FakeLlmClient.responding("hello", name="t"))

    result = agent.invoke("hi")

    assert result == "hello"


# ── JobRunner.structured ─────────────────────────────────────────────────────


def test_structured_invoke_given_valid_json_expect_validated_object():
    agent = JobRunner("t", client=FakeLlmClient.responding('{"color": "red", "count": 3}', name="t"))

    obj = agent.structured(_Schema).invoke("pick a color")

    assert isinstance(obj, _Schema)
    assert obj.color == "red" and obj.count == 3


def test_structured_invoke_given_bad_json_then_valid_expect_one_reask():
    calls = []

    def fake(prompt, model=None):
        calls.append(prompt)
        if len(calls) == 1:
            return "no json here"
        return '{"color": "blue", "count": 1}'

    agent = JobRunner("t", client=FakeLlmClient.from_fn(fake, name="t"))

    obj = agent.structured(_Schema).invoke("pick")

    assert obj.color == "blue"
    assert len(calls) == 2


def test_structured_invoke_given_schema_mismatch_then_valid_expect_one_reask():
    calls = []

    def fake(prompt, model=None):
        calls.append(prompt)
        if len(calls) == 1:
            return '{"color": "red"}'
        return '{"color": "red", "count": 2}'

    agent = JobRunner("t", client=FakeLlmClient.from_fn(fake, name="t"))

    obj = agent.structured(_Schema).invoke("pick")

    assert obj.count == 2
    assert len(calls) == 2


def test_structured_invoke_given_persistent_bad_json_expect_parse_exception():
    agent = JobRunner("t", client=FakeLlmClient.responding("still no json", name="t"))

    with pytest.raises(LlmParseException):
        agent.structured(_Schema).invoke("pick")


def test_structured_invoke_given_quota_exception_expect_no_reask():
    agent = JobRunner("t", client=FakeLlmClient.raising(LlmQuotaException("limited"), name="t"))

    with pytest.raises(LlmQuotaException):
        agent.structured(_Schema).invoke("pick")


def test_structured_invoke_given_reask_expect_original_task_and_bad_response_in_repair_prompt():
    prompts: list[str] = []

    def fn(prompt, model=None):
        prompts.append(prompt)
        if len(prompts) == 1:
            return "not json at all"
        return '{"color": "red", "count": 2}'

    agent = JobRunner(name="a", client=FakeLlmClient.from_fn(fn, name="fake"))

    result = agent.structured(_Schema).invoke("Describe the box named KASSE-7.")

    assert result == _Schema(color="red", count=2)
    repair_prompt = prompts[1]
    assert "Describe the box named KASSE-7." in repair_prompt  # original task preserved
    assert "not json at all" in repair_prompt  # bad response shown back


def test_agent_invoke_response_given_prompt_expect_envelope_with_provenance():
    agent = JobRunner(
        name="a",
        client=FakeLlmClient.responding("hei", name="fake"),
        model="glm-5.2",
        agent="catalog-review",
        variant="high",
    )

    response = agent.invoke_response("hi")

    assert response.text == "hei"
    assert response.client == "fake"
    assert response.model == "glm-5.2"

    assert response.agent == "catalog-review"
    assert response.variant == "high"
    assert response.attempts[0].agent == "catalog-review"
    assert response.attempts[0].variant == "high"
