import json
from http import HTTPStatus

import pytest

import lesson_builder.pipeline.llm.base as base_mod
from lesson_builder.pipeline.llm.clients.openrouter import OpenrouterClient
from lesson_builder.pipeline.llm.exceptions import BackendDownException, LlmQuotaException


class _Resp:
    def __init__(self, status_code, payload=None, text="", json_error: Exception | None = None):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error
        self.text = text or (json.dumps(payload) if payload else "")

    def json(self):
        if self._json_error:
            raise self._json_error
        return self._payload


def test_openrouter_client_given_successful_response_expect_content(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(
        base_mod.httpx,
        "post",
        lambda *a, **k: _Resp(HTTPStatus.OK, {"choices": [{"message": {"content": "hi"}}]}),
    )

    result = OpenrouterClient().call("prompt")

    assert result == "hi"


def test_openrouter_client_given_200_response_mentioning_quota_expect_content_not_exception(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(
        base_mod.httpx,
        "post",
        lambda *a, **k: _Resp(
            HTTPStatus.OK,
            {"choices": [{"message": {"content": "Your rate limit and quota depend on your plan."}}]},
        ),
    )

    result = OpenrouterClient().call("prompt")

    assert result == "Your rate limit and quota depend on your plan."


def test_openrouter_client_given_too_many_requests_response_expect_llm_quota_exception(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(
        base_mod.httpx,
        "post",
        lambda *a, **k: _Resp(HTTPStatus.TOO_MANY_REQUESTS, text="rate limit"),
    )

    with pytest.raises(LlmQuotaException):
        OpenrouterClient().call("prompt")


def test_openrouter_client_given_400_response_matching_quota_pattern_expect_llm_quota_exception(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(
        base_mod.httpx,
        "post",
        lambda *a, **k: _Resp(HTTPStatus.BAD_REQUEST, text="quota exceeded for this key"),
    )

    with pytest.raises(LlmQuotaException):
        OpenrouterClient().call("prompt")


def test_openrouter_client_given_402_payment_required_expect_llm_quota_exception(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(
        base_mod.httpx,
        "post",
        lambda *a, **k: _Resp(HTTPStatus.PAYMENT_REQUIRED, text="Payment Required"),
    )

    with pytest.raises(LlmQuotaException):
        OpenrouterClient().call("prompt")


def test_openrouter_client_given_unauthorized_response_expect_backend_down_exception(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(
        base_mod.httpx,
        "post",
        lambda *a, **k: _Resp(HTTPStatus.UNAUTHORIZED, text="unauthorized"),
    )

    with pytest.raises(BackendDownException):
        OpenrouterClient().call("prompt")


def test_openrouter_client_given_successful_invalid_json_response_expect_backend_down_exception(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(
        base_mod.httpx,
        "post",
        lambda *a, **k: _Resp(HTTPStatus.OK, text="<html>bad</html>", json_error=ValueError("bad json")),
    )

    with pytest.raises(BackendDownException):
        OpenrouterClient().call("prompt")


def test_openrouter_client_given_missing_api_key_expect_backend_down_exception(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(BackendDownException):
        OpenrouterClient().call("prompt")
