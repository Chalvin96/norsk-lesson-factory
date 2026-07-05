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

    assert result.text == "hi"


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

    assert result.text == "Your rate limit and quota depend on your plan."


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


def test_openrouter_call_given_usage_in_payload_expect_tokens_on_response(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(
        base_mod.httpx,
        "post",
        lambda *a, **k: _Resp(
            200,
            {
                "choices": [{"message": {"content": "hei"}}],
                "usage": {"prompt_tokens": 80, "completion_tokens": 30},
            },
        ),
    )

    response = OpenrouterClient().call("hei")

    assert response.text == "hei"
    assert response.input_tokens == 80
    assert response.output_tokens == 30


def test_openrouter_call_given_one_502_then_200_expect_success_after_retry(monkeypatch):
    responses = iter([
        _Resp(502, {}),
        _Resp(200, {
            "choices": [{"message": {"content": "hei"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }),
    ])
    monkeypatch.setattr("lesson_builder.pipeline.llm.base.httpx.post", lambda *a, **k: next(responses))
    monkeypatch.setattr("lesson_builder.pipeline.llm.base.time.sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    assert OpenrouterClient().call("hei").text == "hei"


def test_openrouter_call_given_persistent_502_expect_backend_down_after_one_retry(monkeypatch):
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return _Resp(502, {})

    monkeypatch.setattr("lesson_builder.pipeline.llm.base.httpx.post", fake_post)
    monkeypatch.setattr("lesson_builder.pipeline.llm.base.time.sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    with pytest.raises(BackendDownException):
        OpenrouterClient().call("hei")
    assert len(calls) == 2  # original + exactly one retry


def test_openrouter_call_given_503_with_quota_body_expect_no_retry_and_quota(monkeypatch):
    calls = []

    def fake_post(*a, **k):
        calls.append(1)
        return _Resp(HTTPStatus.SERVICE_UNAVAILABLE, text="rate limit exceeded")

    monkeypatch.setattr("lesson_builder.pipeline.llm.base.httpx.post", fake_post)
    monkeypatch.setattr("lesson_builder.pipeline.llm.base.time.sleep", lambda s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")

    with pytest.raises(LlmQuotaException):
        OpenrouterClient().call("hei")
    assert len(calls) == 1  # quota-signal body must not be retried
