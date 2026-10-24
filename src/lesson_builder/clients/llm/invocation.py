"""Entry points: `JobRunner.invoke_response` (job callers), `provider_call_cancellation` (operation scopes), and `build_structured_json_prompt` (Promptfoo adapters).

Each job runner owns one configured OpenCode client and one model/agent/variant
route. A transient backend failure receives the bounded shared retry policy;
quota exhaustion and parse failures propagate without trying another backend.

Structured output = JSON-instruction + extract_json + Pydantic validate (not native
tool-calling), with ONE bounded re-ask on ``LlmParseException``. ``build_structured_json_prompt``
is the shared first-attempt wording for every generic structured call.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace

from pydantic import BaseModel
from pydantic import ValidationError

from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.clients.cancellation import active_cancellation_token
from lesson_builder.clients.cancellation import provider_call_cancellation
from lesson_builder.clients.llm.base import AttemptOutcome
from lesson_builder.clients.llm.base import BaseLlmClient
from lesson_builder.clients.llm.base import LlmResponse
from lesson_builder.clients.llm.base import extract_json_object
from lesson_builder.clients.llm.base import raise_if_cli_call_cancelled
from lesson_builder.clients.llm.exceptions import BackendDownException
from lesson_builder.clients.llm.exceptions import LlmException
from lesson_builder.clients.llm.exceptions import LlmParseException
from lesson_builder.clients.llm.exceptions import LlmQuotaException
from lesson_builder.clients.llm.failure_messages import provider_name_for_model
from lesson_builder.clients.llm.settings import K_LLM_BACKEND_RETRIES
from lesson_builder.clients.llm.settings import K_LLM_BACKEND_RETRY_BACKOFF_SECONDS


@dataclass
class JobRunner:
    name: str
    client: BaseLlmClient
    model: str | None = None
    agent: str | None = None
    variant: str | None = None
    max_reasks: int = 1
    last_response: LlmResponse | None = field(default=None, init=False, repr=False)
    last_exception: LlmException | None = field(default=None, init=False, repr=False)

    def invoke(self, prompt: str, *, cancellation: CancellationToken | None = None) -> str:
        return self.invoke_response(prompt, cancellation=cancellation).text

    def invoke_response(self, prompt: str, *, cancellation: CancellationToken | None = None) -> LlmResponse:
        self.last_response = None
        self.last_exception = None
        resolved_cancellation = _resolve_cancellation(cancellation)
        try:
            if resolved_cancellation is None:
                response = _invoke_client(
                    self.client,
                    prompt,
                    model=self.model,
                    agent=self.agent,
                    variant=self.variant,
                    cancellation=None,
                )
            else:
                with provider_call_cancellation(resolved_cancellation):
                    response = _invoke_client(
                        self.client,
                        prompt,
                        model=self.model,
                        agent=self.agent,
                        variant=self.variant,
                        cancellation=resolved_cancellation,
                    )
        except LlmException as exc:
            exc.provider_name = provider_name_for_model(self.model or self.client.name)
            self.last_exception = exc
            raise
        self.last_response = response
        return response

    def structured[T: BaseModel](self, schema: type[T]) -> StructuredJobRunner[T]:
        return StructuredJobRunner(self, schema, max_reasks=self.max_reasks)


def build_structured_json_prompt(prompt: str, schema: type[BaseModel]) -> str:
    """Return the first-attempt prompt for one generic schema-validated call.

    This is the single wording for appending a JSON schema instruction to a
    prompt. ``StructuredJobRunner`` and Promptfoo adapters both render their
    first attempt through this helper so an evaluated prompt is byte-exact with
    the production call.
    """
    return (
        f"{prompt}\n\n"
        "Respond with a single JSON object matching this schema "
        "(no markdown fences, no prose before or after):\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )


@dataclass
class StructuredJobRunner[T: BaseModel]:
    _runner: JobRunner
    _schema: type[T]
    max_reasks: int = 1

    def invoke(self, prompt: str, *, cancellation: CancellationToken | None = None) -> T:
        schema = self._schema
        instruction = build_structured_json_prompt(prompt, schema)
        last_err: LlmParseException | None = None
        attempt = instruction
        for _ in range(self.max_reasks + 1):
            text = self._runner.invoke(attempt, cancellation=cancellation)
            try:
                obj = extract_json_object(text)
            except (TypeError, ValueError) as e:
                last_err = LlmParseException(f"no JSON in response: {e}; response was: {text[:200]!r}")
                attempt = self._repair(instruction, text, last_err)
                continue
            try:
                return schema.model_validate(obj)
            except ValidationError as e:
                last_err = LlmParseException(f"schema validation failed: {e}")
                attempt = self._repair(instruction, text, last_err)
                continue
        if last_err is None:
            raise RuntimeError("unreachable")
        raise last_err

    @staticmethod
    def _repair(instruction: str, bad_response: str, err: LlmParseException) -> str:
        return (
            f"{instruction}\n\n"
            "Your previous response to this exact task was not valid.\n"
            f"Previous response (may be truncated):\n{bad_response[:2000]}\n\n"
            f"Error: {err}\n\n"
            "Return one valid JSON object matching the schema above."
        )


def _invoke_client(
    client: BaseLlmClient,
    prompt: str,
    *,
    model: str | None,
    agent: str | None,
    variant: str | None,
    cancellation: CancellationToken | None,
) -> LlmResponse:
    """Call one configured client with bounded transient-failure retries."""
    attempts: list[AttemptOutcome] = []
    last_err: LlmException | None = None
    attempt_model: str = model or getattr(client, "default_model", "") or ""
    for backend_attempt in range(K_LLM_BACKEND_RETRIES + 1):
        _raise_if_call_cancelled(cancellation)
        started = time.monotonic()
        try:
            response = client.call(
                prompt,
                model=model,
                agent=agent,
                variant=variant,
                cancellation=cancellation,
            )
        except LlmQuotaException as exc:
            attempts.append(AttemptOutcome(client.name, attempt_model, "quota", str(exc), agent, variant))
            last_err = exc
            break
        except BackendDownException as exc:
            attempts.append(AttemptOutcome(client.name, attempt_model, "down", str(exc), agent, variant))
            last_err = exc
            _raise_if_call_cancelled(cancellation, cause=exc)
            if backend_attempt < K_LLM_BACKEND_RETRIES:
                time.sleep(K_LLM_BACKEND_RETRY_BACKOFF_SECONDS)
                continue
            break
        _raise_if_call_cancelled(cancellation)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        response = replace(
            response,
            agent=response.agent or agent,
            variant=response.variant or variant,
        )
        attempts.append(
            AttemptOutcome(
                response.client,
                response.model,
                "ok",
                agent=response.agent,
                variant=response.variant,
            )
        )
        return replace(response, latency_ms=elapsed_ms, attempts=tuple(attempts))
    _raise_if_call_cancelled(cancellation)
    if last_err is None:
        raise RuntimeError("LLM invocation ended without a response or error")
    last_err.attempts = tuple(attempts)
    raise last_err


def _resolve_cancellation(cancellation: CancellationToken | None) -> CancellationToken | None:
    """Use the explicit token or inherit the current operation token."""
    return cancellation or active_cancellation_token()


def _raise_if_call_cancelled(cancellation: CancellationToken | None, *, cause: Exception | None = None) -> None:
    """Raise the operation's reason or the uncoupled CLI batch reason."""
    raise_if_cli_call_cancelled(cancellation, cause=cause)


__all__ = [
    # Job invocation API.
    "JobRunner",
    "StructuredJobRunner",
    # Shared prompt rendering.
    "build_structured_json_prompt",
    # Workflow cancellation scope.
    "provider_call_cancellation",
]
