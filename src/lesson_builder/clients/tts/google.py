"""Entry point: `GoogleTextToSpeechClient.synthesize` (called by `distribution.audio.synthesize_lesson_audio`).

The Google Cloud Text-to-Speech adapter owns the provider boundary: the
service-account OAuth token exchange, one bounded retry loop for transient
throttling and transport failures, a single mid-run token refresh after a
synthesis 401, and base64 audio decoding. The encoding/sample-rate pair is
exported because the distribution synthesis fingerprint in ``distribution.audio`` must
identity the same provider request without owning it.
"""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Protocol
from typing import cast

import httpx
import jwt

K_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
K_GOOGLE_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
K_GOOGLE_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
# Private provider constants. The synthesis request and the distribution cache
# fingerprint always use this one encoding/sample-rate pair, so it is not
# configuration.
K_GOOGLE_TTS_ENCODING = "LINEAR16"
K_GOOGLE_TTS_SAMPLE_RATE_HZ = 24_000
K_GOOGLE_TTS_MAX_ATTEMPTS = 4
K_GOOGLE_TTS_BACKOFF_SECONDS: float = 1.0
K_GOOGLE_TTS_MAX_BACKOFF_SECONDS: float = 30.0
K_GOOGLE_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
K_GOOGLE_UNAUTHORIZED_STATUS = 401


class GoogleTextToSpeechClient:
    """Google Cloud TTS adapter: one ``synthesize`` call per uncached candidate.

    The client acquires one OAuth token lazily on the first synthesis request,
    reuses it for the whole run, and refreshes it exactly once when a synthesis
    request answers 401. Transient throttling and transport failures receive
    bounded retries with provider-directed or exponential backoff.
    """

    def __init__(
        self,
        *,
        service_account_path: Path,
        language: str,
        http_client: AudioHttpClient | None = None,
    ) -> None:
        self._service_account_path = Path(service_account_path)
        self._language = language
        self._owned_client: httpx.Client | None = None
        if http_client is None:
            self._owned_client = httpx.Client(timeout=60.0)
            self._client: AudioHttpClient = cast(AudioHttpClient, self._owned_client)
        else:
            self._client = http_client
        self._access_token: str | None = None

    def synthesize(self, *, text: str, voice: str) -> bytes:
        """Synthesize one candidate and return the provider audio bytes."""
        if self._access_token is None:
            self._access_token = _google_access_token(
                service_account_path=self._service_account_path,
                client=self._client,
            )
        try:
            return _synthesize_google(
                text=text,
                language=self._language,
                voice=voice,
                client=self._client,
                access_token=self._access_token,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code != K_GOOGLE_UNAUTHORIZED_STATUS:
                raise
            # A synthesis 401 means the cached token expired mid-run: acquire
            # one fresh token and retry that candidate once. A second 401 is a
            # real authentication failure and propagates.
            self._access_token = _google_access_token(
                service_account_path=self._service_account_path,
                client=self._client,
            )
            return _synthesize_google(
                text=text,
                language=self._language,
                voice=voice,
                client=self._client,
                access_token=self._access_token,
            )

    def close(self) -> None:
        """Close the owned HTTP client; an injected client stays open."""
        if self._owned_client is not None:
            self._owned_client.close()


class AudioHttpClient(Protocol):
    """Minimal HTTP surface used by the Google TTS client and its tests."""

    def post(self, url: str, **kwargs: object) -> httpx.Response:
        """POST one token or synthesis request."""


def _google_access_token(*, service_account_path: Path, client: AudioHttpClient) -> str:
    """Acquire one Google OAuth token for a synthesis run."""
    account = json.loads(service_account_path.read_text(encoding="utf-8"))
    if not isinstance(account, dict):
        raise TypeError("service account JSON must be an object")
    email = account.get("client_email")
    private_key = account.get("private_key")
    token_uri = account.get("token_uri", K_GOOGLE_TOKEN_URL)
    if not isinstance(email, str) or not isinstance(private_key, str):
        raise TypeError("service account JSON lacks client_email/private_key")
    now = int(time.time())
    assertion = jwt.encode(
        {
            "iss": email,
            "scope": K_GOOGLE_SCOPE,
            "aud": token_uri,
            "iat": now,
            "exp": now + 3600,
        },
        private_key,
        algorithm="RS256",
    )
    token_response = client.post(
        token_uri,
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
    )
    token_response.raise_for_status()
    token_payload = token_response.json()
    access_token = token_payload.get("access_token") if isinstance(token_payload, dict) else None
    if not isinstance(access_token, str) or not access_token:
        raise ValueError("Google OAuth response did not contain access_token")
    return access_token


def _synthesize_google(
    *,
    text: str,
    language: str,
    voice: str,
    client: AudioHttpClient,
    access_token: str,
) -> bytes:
    """Call Google Cloud TTS with the token acquired for this run."""
    request_kwargs = {
        "headers": {"Authorization": f"Bearer {access_token}"},
        "json": {
            "input": {"text": text},
            "voice": {"languageCode": language, "name": voice},
            "audioConfig": {
                "audioEncoding": K_GOOGLE_TTS_ENCODING,
                "sampleRateHertz": K_GOOGLE_TTS_SAMPLE_RATE_HZ,
            },
        },
    }
    tts_response: httpx.Response | None = None
    for attempt in range(K_GOOGLE_TTS_MAX_ATTEMPTS):
        try:
            tts_response = client.post(K_GOOGLE_TTS_URL, **request_kwargs)
            tts_response.raise_for_status()
            break
        except (httpx.TimeoutException, httpx.TransportError):
            if attempt + 1 >= K_GOOGLE_TTS_MAX_ATTEMPTS:
                raise
            time.sleep(_retry_delay_seconds(attempt=attempt, response=None))
        except httpx.HTTPStatusError as exc:
            if (
                exc.response.status_code not in K_GOOGLE_TRANSIENT_STATUS_CODES
                or attempt + 1 >= K_GOOGLE_TTS_MAX_ATTEMPTS
            ):
                raise
            time.sleep(_retry_delay_seconds(attempt=attempt, response=exc.response))
    if tts_response is None:
        raise RuntimeError("Google TTS retry loop produced no response")
    payload = tts_response.json()
    encoded = payload.get("audioContent") if isinstance(payload, dict) else None
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Google TTS response did not contain audioContent")
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Google TTS audioContent was not valid base64") from exc


def _retry_delay_seconds(*, attempt: int, response: httpx.Response | None) -> float:
    """Return provider-directed or bounded exponential retry delay."""
    if response is not None:
        raw_retry_after = response.headers.get("Retry-After")
        if raw_retry_after is not None:
            try:
                return min(max(float(raw_retry_after), 0.0), K_GOOGLE_TTS_MAX_BACKOFF_SECONDS)
            except ValueError:
                pass
    return float(
        min(
            K_GOOGLE_TTS_BACKOFF_SECONDS * (2**attempt),
            K_GOOGLE_TTS_MAX_BACKOFF_SECONDS,
        )
    )


__all__ = [
    # Adapter entry point
    "GoogleTextToSpeechClient",
    # Injectable HTTP contract
    "AudioHttpClient",
    # Request constants shared with the release synthesis fingerprint
    "K_GOOGLE_TTS_ENCODING",
    "K_GOOGLE_TTS_SAMPLE_RATE_HZ",
]
