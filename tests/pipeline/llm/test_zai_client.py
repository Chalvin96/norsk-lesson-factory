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
    assert ZaiClient().call("hi") == "OK!"


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
