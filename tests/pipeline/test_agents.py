"""Agent profiles expose real multi-hop fallback chains."""
from __future__ import annotations

from lesson_builder.pipeline.agents import author, default_registry, reviewer
from lesson_builder.pipeline.llm.exceptions import BackendDownException
from lesson_builder.pipeline.llm.invocation import BackendStep, call_llm

from .fakes import FakeLlmClient


def test_default_registry_given_no_overrides_expect_author_and_reviewer():
    reg = default_registry()
    assert {a.name for a in reg.agents.values()} == {"author", "reviewer"}


def test_author_profile_given_default_registry_expect_codex_gpt55_first_hop():
    a = default_registry().get("author")
    assert len(a.chain) >= 2
    step: BackendStep = a.chain[0]
    assert step.client == "codex"
    assert step.model == "gpt-5.5"


def test_reviewer_profile_given_default_registry_expect_zai_glm_first_hop():
    a = default_registry().get("reviewer")
    assert len(a.chain) >= 2
    step: BackendStep = a.chain[0]
    assert step.client == "zai"
    assert step.model == "glm-5.2"


def test_author_chain_has_fallback_hop():
    assert len(author().chain) >= 2


def test_reviewer_chain_has_fallback_hop():
    assert len(reviewer().chain) >= 2


def test_call_llm_given_backend_down_on_first_hop_expect_failover_to_second():
    chain = [BackendStep(client="down"), BackendStep(client="ok")]
    clients = {
        "down": FakeLlmClient.raising(BackendDownException("down"), name="down"),
        "ok": FakeLlmClient.responding("ok-response", name="ok"),
    }
    assert call_llm(chain, "p", clients=clients) == "ok-response"
