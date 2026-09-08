"""Entry point: `GoogleTextToSpeechClient.synthesize` behavior at the provider boundary.

Offline: the OAuth assertion is patched and every HTTP exchange runs through
an injected fake ``AudioHttpClient``. These tests own the provider contract:
lazy token acquisition and reuse, exactly one mid-run token refresh after a
synthesis 401, bounded transient retries with provider-directed backoff, and
base64 audio decoding.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from lesson_builder.clients.tts.google import AudioHttpClient
from lesson_builder.clients.tts.google import GoogleTextToSpeechClient
from tests.clients.tts.fakes import FakeHttpClient
from tests.clients.tts.fakes import UnauthorizedThenWavHttpClient


def _account(tmp_path: Path) -> Path:
    account = tmp_path / "service-account.json"
    account.write_text(
        json.dumps({"client_email": "test@example.com", "private_key": "unused"}),  # pragma: allowlist secret
        encoding="utf-8",
    )
    return account


def _encoded_audio() -> str:
    return base64.b64encode(b"wav-bytes").decode("ascii")


def _patch_jwt(monkeypatch) -> None:
    monkeypatch.setattr(
        "lesson_builder.clients.tts.google.jwt.encode",
        lambda payload, key, algorithm: "assertion",
    )


def _client(tmp_path: Path, http_client: AudioHttpClient) -> GoogleTextToSpeechClient:
    return GoogleTextToSpeechClient(
        service_account_path=_account(tmp_path),
        language="nb-NO",
        http_client=http_client,
    )


def test_google_synthesis_given_uncached_call_expect_lazy_token_then_decoded_audio(tmp_path: Path, monkeypatch):
    _patch_jwt(monkeypatch)
    http_client = FakeHttpClient(_encoded_audio())
    client = _client(tmp_path, http_client)

    audio = client.synthesize(text="Hei.", voice="nb-NO-Wavenet-A")
    second = client.synthesize(text="Hei igjen.", voice="nb-NO-Wavenet-A")

    assert audio == b"wav-bytes"
    assert second == b"wav-bytes"
    assert http_client.urls.count("https://oauth2.googleapis.com/token") == 1
    assert http_client.urls.count("https://texttospeech.googleapis.com/v1/text:synthesize") == 2


def test_google_synthesis_given_transient_throttle_expect_bounded_retry_with_provider_delay(
    tmp_path: Path, monkeypatch
) -> None:
    _patch_jwt(monkeypatch)
    delays: list[float] = []
    monkeypatch.setattr("lesson_builder.clients.tts.google.time.sleep", delays.append)

    class ThrottledOnceClient:
        def __init__(self) -> None:
            self.tts_calls = 0

        def post(self, url: str, **kwargs: object) -> httpx.Response:
            request = httpx.Request("POST", url)
            if "oauth2" in url:
                return httpx.Response(200, json={"access_token": "token"}, request=request)
            self.tts_calls += 1
            if self.tts_calls == 1:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "0.25"},
                    json={"error": {"message": "temporarily throttled"}},
                    request=request,
                )
            return httpx.Response(
                200,
                json={"audioContent": _encoded_audio()},
                request=request,
            )

    http_client = ThrottledOnceClient()
    client = _client(tmp_path, http_client)

    audio = client.synthesize(text="Hei.", voice="nb-NO-Wavenet-A")

    assert audio == b"wav-bytes"
    assert http_client.tts_calls == 2
    assert delays == [0.25]


def test_google_synthesis_given_first_request_401_expect_one_token_refresh_and_retry(
    tmp_path: Path, monkeypatch
) -> None:
    _patch_jwt(monkeypatch)
    http_client = UnauthorizedThenWavHttpClient(_encoded_audio(), failures=1)
    client = _client(tmp_path, http_client)

    audio = client.synthesize(text="Hei.", voice="nb-NO-Wavenet-A")

    assert audio == b"wav-bytes"
    assert http_client.tokens_issued == 2
    assert http_client.tts_authorizations == ["Bearer token-1", "Bearer token-2"]


def test_google_synthesis_given_second_synthesis_401_expect_failure(tmp_path: Path, monkeypatch) -> None:
    _patch_jwt(monkeypatch)
    http_client = UnauthorizedThenWavHttpClient(_encoded_audio(), failures=2)
    client = _client(tmp_path, http_client)

    with pytest.raises(httpx.HTTPStatusError):
        client.synthesize(text="Hei.", voice="nb-NO-Wavenet-A")


def test_google_synthesis_given_missing_audio_content_expect_value_error(tmp_path: Path, monkeypatch):
    _patch_jwt(monkeypatch)

    class EmptyContentClient:
        def post(self, url: str, **kwargs: object) -> httpx.Response:
            request = httpx.Request("POST", url)
            if "oauth2" in url:
                return httpx.Response(200, json={"access_token": "token"}, request=request)
            return httpx.Response(200, json={}, request=request)

    client = _client(tmp_path, EmptyContentClient())

    with pytest.raises(ValueError, match="did not contain audioContent"):
        client.synthesize(text="Hei.", voice="nb-NO-Wavenet-A")
