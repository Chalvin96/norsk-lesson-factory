"""Shared fakes for the pipeline layer.

Entry point: ``FakeLlmClient`` — a typed, behavior-driven stand-in for any
``BaseLlmClient``, plus ``fake_agent`` to wrap one in an ``Agent``. Imported
explicitly by tests; see AGENTS.md "Fakes and factories" for the convention.
"""

from __future__ import annotations

from collections.abc import Callable

from lesson_builder.pipeline.llm.base import BaseLlmClient, LlmResponse
from lesson_builder.pipeline.llm.invocation import Agent, BackendStep

# fn(prompt, model) -> response text (or raises to simulate a failing backend).
LlmCallFn = Callable[[str, "str | None"], str]


class FakeLlmClient(BaseLlmClient):
    """Behavior-driven fake LLM client.

    Driven by an ``fn(prompt, model)`` closure so one class covers every case:
    canned, echo, conditional, raise, malformed JSON. Records every call on
    ``calls`` so interaction can be asserted without ``unittest.mock``.
    """

    def __init__(self, fn: LlmCallFn | None = None, *, name: str = "fake") -> None:
        self.name = name
        self._fn: LlmCallFn = fn or (lambda prompt, model: "")
        self.calls: list[tuple[str, str | None]] = []

    def call(self, prompt: str, *, model: str | None = None) -> LlmResponse:
        self.calls.append((prompt, model))
        return LlmResponse(
            text=self._fn(prompt, model), client=self.name, model=model or "fake-model"
        )

    @classmethod
    def responding(cls, response: str, *, name: str = "fake") -> FakeLlmClient:
        """A client that always returns ``response``."""
        return cls(lambda prompt, model: response, name=name)

    @classmethod
    def raising(cls, exc: Exception, *, name: str = "fake") -> FakeLlmClient:
        """A client whose every call raises ``exc`` (e.g. a down/quota backend)."""

        def _raise(prompt: str, model: str | None) -> str:
            raise exc

        return cls(_raise, name=name)

    @classmethod
    def from_fn(cls, fn: LlmCallFn, *, name: str = "fake") -> FakeLlmClient:
        """A client driven by an arbitrary ``fn(prompt, model)`` closure."""
        return cls(fn, name=name)


def fake_agent(client: BaseLlmClient) -> Agent:
    """A single-hop ``Agent`` backed by ``client`` (keyed on ``client.name``)."""
    return Agent(name=client.name, chain=[BackendStep(client.name)], clients={client.name: client})
