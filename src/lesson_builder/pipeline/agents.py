"""Default lesson-building agent profiles.

author
    Drafts and repairs lesson data. Use for generation work: building lesson
    elements, regenerating weak sections, and applying requested fixes.
reviewer
    Critiques lesson data. Use for gate-style review work: pedagogy,
    objective alignment, requirement coverage, and structured validation notes.

Two-hop fallback chains; no circuit-breaker, no agents.yaml yet.
"""

from __future__ import annotations

from lesson_builder.pipeline.llm.invocation import Agent, AgentRegistry, BackendStep
from lesson_builder.settings import K_OPENROUTER_DEFAULT_MODEL

_AUTHOR_MODEL = "gpt-5.5"
_REVIEWER_MODEL = "opencode-go/glm-5.2"
_ZAI_REVIEWER_MODEL = "glm-5.2"
_OPENROUTER_FALLBACK_MODEL = K_OPENROUTER_DEFAULT_MODEL


def author() -> Agent:
    return Agent(
        name="author",
        chain=[
            BackendStep(client="codex", model=_AUTHOR_MODEL),
            BackendStep(client="openrouter", model=_OPENROUTER_FALLBACK_MODEL),
        ],
    )


def reviewer() -> Agent:
    return Agent(
        name="reviewer",
        chain=[
            # Z.AI GLM direct (Anthropic-compatible) is the working GLM path; the
            # opencode-go provider hangs and openrouter is quota-exhausted.
            BackendStep(client="zai", model=_ZAI_REVIEWER_MODEL),
            BackendStep(client="opencode", model=_REVIEWER_MODEL),
            BackendStep(client="openrouter", model=_OPENROUTER_FALLBACK_MODEL),
            # Last-resort fallback: codex catches the all-dead case.
            BackendStep(client="codex", model=_AUTHOR_MODEL),
        ],
    )


def default_registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register(author())
    reg.register(reviewer())
    return reg
