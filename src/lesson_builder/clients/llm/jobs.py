"""Entry point: job factories, ``runner_for_job``, and ``client_for_job``.

reviewer
    Critiques lesson data: pedagogy, objective alignment, requirement
    coverage, and structured validation notes.
catalog_explorer
    Discovers catalog candidates using internet-backed research and citations.
catalog_reviewer
    Evaluates and reconciles catalog artifacts from model knowledge without
    browsing; the catalog prompts enforce this boundary.
curriculum_reviewer
    Independently reviews a curriculum proposal without browsing or editing.
normalizer
    Normalizes generated lesson packages into the transport contract.
exercise_author
    Fills exercise contracts from immutable lesson requests.

Jobs are single-hop OpenCode CLI calls selected from ``config.yaml``; nodes
own task/user prompts, and LangGraph owns retry and checkpoint behavior
around each invocation. Every job runs with the configured
``llm.timeout_seconds`` default unless the caller passes an explicit finite
positive override; explicit batch cancellation is the additional escape path.
"""

from __future__ import annotations

from pathlib import Path

from lesson_builder.clients.llm.base import BaseLlmClient
from lesson_builder.clients.llm.base import cancel_active_cli_calls
from lesson_builder.clients.llm.base import reset_cli_cancellation
from lesson_builder.clients.llm.config import LlmJob
from lesson_builder.clients.llm.config import load_llm_job
from lesson_builder.clients.llm.config import load_llm_timeout_seconds
from lesson_builder.clients.llm.invocation import JobRunner
from lesson_builder.clients.llm.opencode import OpencodeClient


def reviewer(*, repo_root: Path | None = None, timeout_seconds: int | None = None) -> JobRunner:
    """Return the configured reviewer job."""
    return runner_for_job("reviewer", repo_root=repo_root, timeout_seconds=timeout_seconds)


def catalog_explorer(*, repo_root: Path | None = None, timeout_seconds: int | None = None) -> JobRunner:
    """Return the internet-research job used by the explorer graph branch."""
    return runner_for_job("catalog_explorer", repo_root=repo_root, timeout_seconds=timeout_seconds)


def catalog_reviewer(*, repo_root: Path | None = None, timeout_seconds: int | None = None) -> JobRunner:
    """Return the no-browsing job used by the catalog review branch."""
    return runner_for_job("catalog_reviewer", repo_root=repo_root, timeout_seconds=timeout_seconds)


def curriculum_reviewer(*, repo_root: Path | None = None, timeout_seconds: int | None = None) -> JobRunner:
    """Return the independent no-browsing curriculum review job."""
    return runner_for_job("curriculum_reviewer", repo_root=repo_root, timeout_seconds=timeout_seconds)


def normalizer(*, repo_root: Path | None = None, timeout_seconds: int | None = None) -> JobRunner:
    """Return the package-normalization job used by stacked generation code."""
    return runner_for_job("normalizer", repo_root=repo_root, timeout_seconds=timeout_seconds)


def exercise_author(*, repo_root: Path | None = None, timeout_seconds: int | None = None) -> JobRunner:
    """Return the exercise-contract job used by stacked generation code."""
    return runner_for_job("exercise_author", repo_root=repo_root, timeout_seconds=timeout_seconds)


def runner_for_job(
    job_name: str,
    *,
    repo_root: Path | None = None,
    timeout_seconds: int | None = None,
) -> JobRunner:
    """Build one single-hop CLI job runner from a validated config job."""
    job = load_llm_job(job_name, repo_root=repo_root)
    client = client_for_job(job, repo_root=repo_root, timeout_seconds=timeout_seconds)
    return JobRunner(
        name=job.name,
        client=client,
        model=job.tier.model,
        agent=job.agent_template,
        variant=job.tier.variant,
    )


def client_for_job(
    job: LlmJob,
    *,
    repo_root: Path | None = None,
    timeout_seconds: int | None = None,
) -> BaseLlmClient:
    """Build the configured OpenCode transport for one job.

    This is the composition boundary: the concrete OpenCode client is
    constructed here, never at production call sites. OpenCode is the only supported transport; the
    job's tier selects the model/provider routed through it and the job's
    agent template selects the ``.opencode/agents`` policy. ``repo_root`` is
    the validated repository boundary and becomes the subprocess working
    directory. The client runs with the configured ``llm.timeout_seconds``
    default unless the caller passes an explicit finite positive override.
    """
    return OpencodeClient(
        default_model=job.tier.model,
        timeout=_resolved_timeout(repo_root=repo_root, timeout_seconds=timeout_seconds),
        repo_root=repo_root,
    )


def cancel_active_clients() -> None:
    """Terminate and reap every active vendor-client process owned by this process."""
    cancel_active_cli_calls()


def reset_client_cancellation() -> None:
    """Allow new client calls after an interrupted batch has shut down."""
    reset_cli_cancellation()


def _resolved_timeout(*, repo_root: Path | None, timeout_seconds: int | None) -> int:
    """Return the explicit override when valid, else the config default."""
    if timeout_seconds is None:
        return load_llm_timeout_seconds(repo_root=repo_root)
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise TypeError("timeout override must be a positive integer")
    if timeout_seconds <= 0:
        raise ValueError("timeout override must be positive")
    return timeout_seconds


__all__ = [
    "cancel_active_clients",
    "catalog_explorer",
    "catalog_reviewer",
    "curriculum_reviewer",
    "client_for_job",
    "exercise_author",
    "normalizer",
    "reset_client_cancellation",
    "reviewer",
    "runner_for_job",
]
