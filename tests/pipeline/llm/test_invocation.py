import pytest
from pydantic import BaseModel

from lesson_builder.pipeline.llm.exceptions import (
    BackendDownException,
    LlmParseException,
    LlmQuotaException,
)
from lesson_builder.pipeline.llm.invocation import (
    Agent,
    AgentRegistry,
    BackendStep,
    call_llm,
)

from ..fakes import FakeLlmClient


class _Schema(BaseModel):
    color: str
    count: int


def _clients_with(**clients):
    return {name: FakeLlmClient.from_fn(fn, name=name) for name, fn in clients.items()}


# ── call_llm ────────────────────────────────────────────────────────────────


def test_call_llm_given_single_step_chain_expect_text_returned():
    clients = _clients_with(first=lambda p, model=None: f"echo:{p}")
    chain = [BackendStep(client="first")]

    result = call_llm(chain, "hi", clients=clients)

    assert result.text == "echo:hi"


def test_call_llm_given_quota_on_first_step_expect_failover_to_second():
    calls = []

    def boom(prompt, model=None):
        calls.append("boom")
        raise LlmQuotaException("limited")

    def ok(prompt, model=None):
        calls.append("ok")
        return "recovered"

    clients = _clients_with(boom=boom, ok=ok)
    chain = [BackendStep(client="boom"), BackendStep(client="ok")]

    result = call_llm(chain, "hi", clients=clients)

    assert result.text == "recovered"
    assert calls == ["boom", "ok"]


def test_call_llm_given_backend_down_on_first_step_expect_failover_to_second():
    clients = _clients_with(
        down=lambda p, model=None: (_ for _ in ()).throw(BackendDownException("down")),
        ok=lambda p, model=None: "ok",
    )
    chain = [BackendStep(client="down"), BackendStep(client="ok")]

    result = call_llm(chain, "hi", clients=clients)

    assert result.text == "ok"


def test_call_llm_given_all_steps_fail_expect_last_exception_reraised():
    clients = _clients_with(
        first=lambda p, model=None: (_ for _ in ()).throw(LlmQuotaException("a")),
        second=lambda p, model=None: (_ for _ in ()).throw(BackendDownException("b")),
    )
    chain = [BackendStep(client="first"), BackendStep(client="second")]
    with pytest.raises(BackendDownException):
        call_llm(chain, "hi", clients=clients)


def test_call_llm_given_all_steps_fail_expect_attempt_trace_on_exception():
    clients = _clients_with(
        first=lambda p, model=None: (_ for _ in ()).throw(LlmQuotaException("a")),
        second=lambda p, model=None: (_ for _ in ()).throw(BackendDownException("b")),
    )
    chain = [BackendStep(client="first", model="m1"), BackendStep(client="second", model="m2")]

    with pytest.raises(BackendDownException) as excinfo:
        call_llm(chain, "hi", clients=clients)

    attempts = excinfo.value.attempts
    assert [(a.client, a.outcome) for a in attempts] == [("first", "quota"), ("second", "down")]


def test_call_llm_given_empty_chain_expect_backend_down_exception():
    clients = _clients_with()
    with pytest.raises(BackendDownException):
        call_llm([], "hi", clients=clients)


def test_call_llm_given_parse_exception_expect_immediate_propagation_not_failover():
    tried = []

    def parse_fail(prompt, model=None):
        tried.append("first")
        raise LlmParseException("bad json")

    def should_not_run(prompt, model=None):
        tried.append("second")
        return "x"

    clients = _clients_with(first=parse_fail, second=should_not_run)
    chain = [BackendStep(client="first"), BackendStep(client="second")]
    with pytest.raises(LlmParseException):
        call_llm(chain, "hi", clients=clients)
    assert tried == ["first"]


# ── Agent.invoke ────────────────────────────────────────────────────────────


def test_agent_invoke_given_prompt_expect_backend_text():
    clients = _clients_with(t=lambda p, model=None: "hello")
    a = Agent("t", [BackendStep(client="t")], clients=clients)

    result = a.invoke("hi")

    assert result == "hello"


# ── Agent.structured ────────────────────────────────────────────────────────


def test_structured_invoke_given_valid_json_expect_validated_object():
    clients = _clients_with(t=lambda p, model=None: '{"color": "red", "count": 3}')
    a = Agent("t", [BackendStep(client="t")], clients=clients)

    obj = a.structured(_Schema).invoke("pick a color")

    assert isinstance(obj, _Schema)
    assert obj.color == "red" and obj.count == 3


def test_structured_invoke_given_bad_json_then_valid_expect_one_reask():
    calls = []

    def fake(prompt, model=None):
        calls.append(prompt)
        if len(calls) == 1:
            return "no json here"
        return '{"color": "blue", "count": 1}'

    clients = _clients_with(t=fake)
    a = Agent("t", [BackendStep(client="t")], clients=clients)

    obj = a.structured(_Schema).invoke("pick")

    assert obj.color == "blue"
    assert len(calls) == 2


def test_structured_invoke_given_schema_mismatch_then_valid_expect_one_reask():
    calls = []

    def fake(prompt, model=None):
        calls.append(prompt)
        if len(calls) == 1:
            return '{"color": "red"}'
        return '{"color": "red", "count": 2}'

    clients = _clients_with(t=fake)

    obj = Agent("t", [BackendStep(client="t")], clients=clients).structured(_Schema).invoke("pick")

    assert obj.count == 2
    assert len(calls) == 2


def test_structured_invoke_given_persistent_bad_json_expect_parse_exception():
    clients = _clients_with(t=lambda p, model=None: "still no json")
    a = Agent("t", [BackendStep(client="t")], clients=clients)

    with pytest.raises(LlmParseException):
        a.structured(_Schema).invoke("pick")


def test_structured_invoke_given_quota_exception_expect_no_reask():
    clients = _clients_with(t=lambda p, model=None: (_ for _ in ()).throw(LlmQuotaException("limited")))
    a = Agent("t", [BackendStep(client="t")], clients=clients)

    with pytest.raises(LlmQuotaException):
        a.structured(_Schema).invoke("pick")


# ── AgentRegistry ───────────────────────────────────────────────────────────


def test_registry_given_registered_agent_expect_get_returns_it():
    clients = _clients_with(r=lambda p, model=None: "ok")
    reg = AgentRegistry()

    a = reg.register(Agent("reviewer", [BackendStep(client="r")], clients=clients))

    assert reg.get("reviewer") is a


def test_registry_given_missing_name_expect_keyerror():
    with pytest.raises(KeyError):
        AgentRegistry().get("nope")


def test_structured_invoke_given_reask_expect_original_task_and_bad_response_in_repair_prompt():
    prompts: list[str] = []

    def fn(prompt, model=None):
        prompts.append(prompt)
        if len(prompts) == 1:
            return "not json at all"
        return '{"color": "red", "count": 2}'

    clients = _clients_with(fake=fn)
    agent = Agent(name="a", chain=[BackendStep(client="fake")], clients=clients)

    result = agent.structured(_Schema).invoke("Describe the box named KASSE-7.")

    assert result == _Schema(color="red", count=2)
    repair_prompt = prompts[1]
    assert "Describe the box named KASSE-7." in repair_prompt  # original task preserved
    assert "not json at all" in repair_prompt  # bad response shown back


def test_call_llm_given_failover_expect_attempt_trace_on_response():
    clients = _clients_with(
        boom=lambda p, model=None: (_ for _ in ()).throw(LlmQuotaException("limited")),
        ok=lambda p, model=None: "recovered",
    )
    chain = [BackendStep(client="boom", model="m1"), BackendStep(client="ok", model="m2")]

    response = call_llm(chain, "hi", clients=clients)

    assert response.text == "recovered"
    assert response.latency_ms is not None and response.latency_ms >= 0
    assert [(a.client, a.outcome) for a in response.attempts] == [("boom", "quota"), ("ok", "ok")]
    assert response.attempts[0].error == "limited"


def test_agent_invoke_response_given_prompt_expect_envelope_with_client_and_model():
    clients = _clients_with(fake=lambda p, model=None: "hei")
    agent = Agent(name="a", chain=[BackendStep(client="fake", model="glm-5.2")], clients=clients)

    response = agent.invoke_response("hi")

    assert response.text == "hei"
    assert response.client == "fake"
    assert response.model == "glm-5.2"
