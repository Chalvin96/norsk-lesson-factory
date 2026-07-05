"""Cycle 2 acceptance smoke test: REAL opencode CLI call through the reviewer profile.

Gated behind LLM_SMOKE=1 so the default suite stays fast/offline. Run explicitly:
    LLM_SMOKE=1 uv run pytest tests/pipeline/llm/test_acceptance_smoke.py -v
"""

import os

import pytest
from pydantic import BaseModel

from lesson_builder.pipeline.agents import default_registry


class _Smoke(BaseModel):
    word: str
    n: int


@pytest.mark.skipif(not os.environ.get("LLM_SMOKE"), reason="set LLM_SMOKE=1 to run the real-CLI smoke test")
def test_reviewer_structured_given_real_opencode_cli_expect_validated_object():
    agent = default_registry().get("reviewer")
    obj = agent.structured(_Smoke).invoke(
        'Return a JSON object: word should be "ok" and n should be 1. '
        "Respond with ONLY the JSON object, no prose."
    )
    assert isinstance(obj, _Smoke)  # the acceptance bar: validated object via the real CLI
    assert isinstance(obj.n, int)
    assert obj.n == 1
    assert obj.word.strip().lower() == "ok"
