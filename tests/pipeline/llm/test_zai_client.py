"""Offline unit tests for the Z.AI GLM client (Anthropic message format)."""

from __future__ import annotations

import httpx
import pytest

from lesson_builder.pipeline.llm.clients.zai import ZaiClient
from lesson_builder.pipeline.llm.exceptions import BackendDownException, LlmQuotaException


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _patch_post(monkeypatch, resp):
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: resp)


def test_call_parses_anthropic_content_blocks(monkeypatch):
    resp = _Resp(200, {"content": [{"type": "text", "text": "OK"}, {"type": "text", "text": "!"}]})
    _patch_post(monkeypatch, resp)
    assert ZaiClient().call("hi").text == "OK!"


def test_quota_status_raises_quota(monkeypatch):
    _patch_post(monkeypatch, _Resp(429, text="rate limit"))
    with pytest.raises(LlmQuotaException):
        ZaiClient().call("hi")


def test_empty_content_raises_backend_down(monkeypatch):
    _patch_post(monkeypatch, _Resp(200, {"content": []}))
    with pytest.raises(BackendDownException):
        ZaiClient().call("hi")


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    with pytest.raises(BackendDownException, match="GLM_API_KEY"):
        ZaiClient().call("hi")


def test_server_error_raises_backend_down(monkeypatch):
    _patch_post(monkeypatch, _Resp(500, text="boom"))
    with pytest.raises(BackendDownException):
        ZaiClient().call("hi")


def test_zai_call_given_usage_in_payload_expect_tokens_on_response(monkeypatch):
    resp = _Resp(
        200,
        {
            "content": [{"type": "text", "text": "hei"}],
            "usage": {"input_tokens": 120, "output_tokens": 45},
        },
    )
    _patch_post(monkeypatch, resp)

    client = ZaiClient()
    response = client.call("hei")

    assert response.text == "hei"
    assert response.client == "zai"
    assert response.model == client.default_model
    assert response.input_tokens == 120
    assert response.output_tokens == 45


def test_zai_call_given_one_502_then_200_expect_success_after_retry(monkeypatch):
    responses = iter([
        _Resp(502, {}),
        _Resp(200, {
            "content": [{"type": "text", "text": "hei"}],
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }),
    ])
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    monkeypatch.setattr("lesson_builder.pipeline.llm.clients.zai.httpx.post", lambda *a, **k: next(responses))
    monkeypatch.setattr("lesson_builder.pipeline.llm.base.time.sleep", lambda s: None)

    assert ZaiClient().call("hei").text == "hei"


def test_zai_call_given_persistent_502_expect_backend_down_after_one_retry(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return _Resp(502, {})

    monkeypatch.setattr("lesson_builder.pipeline.llm.clients.zai.httpx.post", fake_post)
    monkeypatch.setattr("lesson_builder.pipeline.llm.base.time.sleep", lambda s: None)

    with pytest.raises(BackendDownException):
        ZaiClient().call("hei")
    assert len(calls) == 2  # original + exactly one retry


def test_zai_call_given_503_with_quota_body_expect_no_retry_and_quota(monkeypatch):
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return _Resp(503, text="rate limit exceeded")

    monkeypatch.setattr("lesson_builder.pipeline.llm.clients.zai.httpx.post", fake_post)
    monkeypatch.setattr("lesson_builder.pipeline.llm.base.time.sleep", lambda s: None)

    with pytest.raises(LlmQuotaException):
        ZaiClient().call("hei")
    assert len(calls) == 1  # quota-signal body must not be retried
