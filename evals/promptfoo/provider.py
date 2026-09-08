"""Entry point: ``call_api`` runs one Promptfoo request through a configured LLM job."""

from __future__ import annotations

import hashlib
from typing import Any

from lesson_builder.clients.llm.jobs import runner_for_job
from lesson_builder.workspace.paths import K_WORKSPACE_ROOT


def call_api(prompt: str, options: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Return one configured job response in Promptfoo's provider shape."""
    del context
    config = options.get("config")
    if not isinstance(config, dict):
        raise TypeError("Promptfoo provider config must be a mapping")
    job = config.get("job")
    if not isinstance(job, str) or not job.strip():
        raise ValueError("Promptfoo provider config requires a non-empty job")
    if config.get("json_response") is True:
        prompt += (
            "\n\nReturn exactly one JSON object with these fields: pass (boolean), "
            "score (number from 0 to 1), and reason (string)."
        )

    response = runner_for_job(job, repo_root=K_WORKSPACE_ROOT).invoke_response(prompt)
    result: dict[str, Any] = {
        "output": response.text,
        "latencyMs": response.latency_ms,
        "metadata": {
            "client": response.client,
            "model": response.model,
            "agent": response.agent,
            "job": job,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        },
    }
    if response.input_tokens is not None and response.output_tokens is not None:
        result["tokenUsage"] = {
            "prompt": response.input_tokens,
            "completion": response.output_tokens,
            "total": response.input_tokens + response.output_tokens,
        }
    return result


__all__ = ["call_api"]
