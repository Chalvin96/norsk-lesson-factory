import pytest


@pytest.fixture(autouse=True)
def _llm_log_off(monkeypatch):
    monkeypatch.setenv("NORSK_LLM_LOG_PATH", "off")
