"""LLM invocation: call_llm failover + Agent structured output.

Agents bind to a ``BackendStep`` (client name + optional model override). ``call_llm``
tries each step in order, failing over on ``LlmQuotaException`` / ``BackendDownException``.
``LlmParseException`` propagates immediately (it is not a failover signal).

Structured output = JSON-instruction + extract_json + Pydantic validate (not native
tool-calling), with ONE bounded re-ask on ``LlmParseException``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from pydantic import BaseModel, ValidationError

from lesson_builder.pipeline.llm.base import (
    AttemptOutcome,
    BaseLlmClient,
    LlmResponse,
    extract_json_object,
)
from lesson_builder.pipeline.llm.clients.codex import CodexClient
from lesson_builder.pipeline.llm.clients.opencode import OpencodeClient
from lesson_builder.pipeline.llm.clients.openrouter import OpenrouterClient
from lesson_builder.pipeline.llm.clients.zai import ZaiClient
from lesson_builder.pipeline.llm.exceptions import (
    BackendDownException,
    LlmException,
    LlmParseException,
    LlmQuotaException,
)
from lesson_builder.pipeline.llm.telemetry import log_llm_call, log_llm_failure


@dataclass(frozen=True)
class BackendStep:
    """One tryable hop in a fallback chain: a client name + optional model id."""

    client: str
    model: str | None = None


@dataclass
class Agent:
    name: str
    chain: Sequence[BackendStep]
    clients: Mapping[str, BaseLlmClient] = field(default_factory=lambda: default_clients())

    def invoke(self, prompt: str) -> str:
        return self.invoke_response(prompt).text

    def invoke_response(self, prompt: str) -> LlmResponse:
        try:
            response = call_llm(self.chain, prompt, clients=self.clients)
        except LlmException as err:
            log_llm_failure(self.name, err.attempts)
            raise
        log_llm_call(self.name, response)
        return response

    def structured[T: BaseModel](self, schema: type[T]) -> StructuredAgent[T]:
        return StructuredAgent(self, schema)


@dataclass
class StructuredAgent[T: BaseModel]:
    _agent: Agent
    _schema: type[T]
    max_reasks: int = 1

    def invoke(self, prompt: str) -> T:
        schema = self._schema
        schema_json = json.dumps(schema.model_json_schema())
        instruction = (
            f"{prompt}\n\n"
            "Respond with ONLY a single JSON object matching this schema "
            "(no markdown fences, no prose before or after):\n"
            f"{schema_json}"
        )
        last_err: LlmParseException | None = None
        attempt = instruction
        for _ in range(self.max_reasks + 1):
            text = self._agent.invoke(attempt)
            try:
                obj = extract_json_object(text)
            except ValueError as e:
                last_err = LlmParseException(f"no JSON in response: {e}; response was: {text[:200]!r}")
                attempt = self._repair(instruction, text, last_err, schema_json)
                continue
            try:
                return schema.model_validate(obj)
            except ValidationError as e:
                last_err = LlmParseException(f"schema validation failed: {e}")
                attempt = self._repair(instruction, text, last_err, schema_json)
                continue
        assert last_err is not None
        raise last_err

    @staticmethod
    def _repair(
        instruction: str, bad_response: str, err: LlmParseException, schema_json: str
    ) -> str:
        return (
            f"{instruction}\n\n"
            "Your previous response to this exact task was not valid.\n"
            f"Previous response (may be truncated):\n{bad_response[:2000]}\n\n"
            f"Error: {err}\n\n"
            "Return ONLY a single valid JSON object matching the schema above."
        )


@dataclass
class AgentRegistry:
    agents: dict[str, Agent] = field(default_factory=dict)

    def register(self, agent: Agent) -> Agent:
        self.agents[agent.name] = agent
        return agent

    def get(self, name: str) -> Agent:
        try:
            return self.agents[name]
        except KeyError as e:
            raise KeyError(f"no agent profile registered as {name!r}") from e


def call_llm(
    chain: Sequence[BackendStep],
    prompt: str,
    *,
    clients: Mapping[str, BaseLlmClient] | None = None,
) -> LlmResponse:
    """Try each step in order; fail over on LlmQuotaException/BackendDownException.

    Returns the successful step's LlmResponse with latency_ms and the full
    per-step attempt trace. Re-raises the last error on exhaustion.
    LlmParseException and other exceptions propagate immediately.
    """
    if not chain:
        raise BackendDownException("empty backend chain")
    available_clients = clients or default_clients()
    attempts: list[AttemptOutcome] = []
    last_err: LlmException | None = None
    for step in chain:
        try:
            client = available_clients[step.client]
        except KeyError as e:
            raise KeyError(f"no LLM client registered as {step.client!r}") from e
        # On failure the client never returns, so resolve the effective model up front
        # (step.model may be None -> the client's own default) to keep failed-attempt
        # telemetry from losing the model.
        attempt_model: str = step.model or getattr(client, "default_model", "") or ""
        started = time.monotonic()
        try:
            response = client.call(prompt, model=step.model)
        except LlmQuotaException as e:
            attempts.append(AttemptOutcome(step.client, attempt_model, "quota", str(e)))
            last_err = e
            continue
        except BackendDownException as e:
            attempts.append(AttemptOutcome(step.client, attempt_model, "down", str(e)))
            last_err = e
            continue
        elapsed_ms = int((time.monotonic() - started) * 1000)
        attempts.append(AttemptOutcome(response.client, response.model, "ok"))
        return replace(response, latency_ms=elapsed_ms, attempts=tuple(attempts))
    assert last_err is not None
    last_err.attempts = tuple(attempts)
    raise last_err


def default_clients() -> dict[str, BaseLlmClient]:
    """Create the built-in LLM clients, keyed by each client's own ``name``."""
    clients: tuple[BaseLlmClient, ...] = (
        CodexClient(),
        OpencodeClient(),
        OpenrouterClient(),
        ZaiClient(),
    )
    return {c.name: c for c in clients}
