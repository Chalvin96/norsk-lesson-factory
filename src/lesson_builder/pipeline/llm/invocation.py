"""LLM invocation: call_llm failover + Agent structured output.

Agents bind to a ``BackendStep`` (client name + optional model override). ``call_llm``
tries each step in order, failing over on ``LlmQuotaException`` / ``BackendDownException``.
``LlmParseException`` propagates immediately (it is not a failover signal).

Structured output = JSON-instruction + extract_json + Pydantic validate (not native
tool-calling), with ONE bounded re-ask on ``LlmParseException``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from lesson_builder.pipeline.llm.base import BaseLlmClient, extract_json_object
from lesson_builder.pipeline.llm.clients.codex import CodexClient
from lesson_builder.pipeline.llm.clients.opencode import OpencodeClient
from lesson_builder.pipeline.llm.clients.openrouter import OpenrouterClient
from lesson_builder.pipeline.llm.clients.zai import ZaiClient
from lesson_builder.pipeline.llm.exceptions import (
    BackendDownException,
    LlmParseException,
    LlmQuotaException,
)


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

    def invoke(self, prompt: str, **kw: Any) -> str:
        return call_llm(self.chain, prompt, clients=self.clients, **kw)

    def structured[T: BaseModel](self, schema: type[T]) -> StructuredAgent[T]:
        return StructuredAgent(self, schema)


@dataclass
class StructuredAgent[T: BaseModel]:
    _agent: Agent
    _schema: type[T]
    max_reasks: int = 1

    def invoke(self, prompt: str, **kw: Any) -> T:
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
            text = self._agent.invoke(attempt, **kw)
            try:
                obj = extract_json_object(text)
            except ValueError as e:
                last_err = LlmParseException(f"no JSON in response: {e}; response was: {text[:200]!r}")
                attempt = self._repair(last_err, schema_json)
                continue
            try:
                return schema.model_validate(obj)
            except ValidationError as e:
                last_err = LlmParseException(f"schema validation failed: {e}")
                attempt = self._repair(last_err, schema_json)
                continue
        assert last_err is not None
        raise last_err

    @staticmethod
    def _repair(err: LlmParseException, schema_json: str) -> str:
        return (
            "Your previous response was not valid. Error: "
            f"{err}\n\nReturn ONLY a single valid JSON object matching this schema:\n{schema_json}"
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
    **kw: Any,
) -> str:
    """Try each step in order; fail over on LlmQuotaException/BackendDownException.

    Re-raise the last on exhaustion. LlmParseException and other exceptions
    propagate immediately (not failover signals).
    """
    if not chain:
        raise BackendDownException("empty backend chain")
    available_clients = clients or default_clients()
    last_err: Exception | None = None
    for step in chain:
        try:
            client = available_clients[step.client]
        except KeyError as e:
            raise KeyError(f"no LLM client registered as {step.client!r}") from e
        try:
            return client.call(prompt, model=step.model, **kw)
        except (LlmQuotaException, BackendDownException) as e:
            last_err = e
            continue
    assert last_err is not None
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
