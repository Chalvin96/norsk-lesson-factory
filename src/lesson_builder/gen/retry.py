"""Bounded retry around a JSON-emitting call, validating each attempt against a model
or TypeAdapter. Re-raises the last exception after max_attempts."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError

type Validator[T: BaseModel] = type[T] | TypeAdapter[T]

_RETRYABLE = (ValidationError, ValueError, json.JSONDecodeError, KeyError)

# Set GEN_DEBUG_RETRY=1 to dump raw model output + validation errors on each
# failed attempt. Used to diagnose why a stage's output fails schema validation.
_DEBUG = os.environ.get("GEN_DEBUG_RETRY", "") not in ("", "0", "false")


def _validate[T: BaseModel](raw: Any, validator: Validator[T]) -> T:
    if isinstance(validator, TypeAdapter):
        return validator.validate_python(raw)
    return validator.model_validate(raw)


def _log_failure(label: str, attempt: int, exc: Exception, raw: Any) -> None:
    if not _DEBUG:
        return
    print(f"  [retry:{label}] attempt {attempt} FAILED: {type(exc).__name__}", file=sys.stderr)
    if isinstance(exc, ValidationError):
        for err in exc.errors()[:8]:
            loc = ".".join(str(p) for p in err.get("loc", ()))
            print(f"      - {err.get('type')} @ {loc}: {err.get('msg')}", file=sys.stderr)
        print(f"      ({exc.error_count()} total errors)", file=sys.stderr)
    else:
        print(f"      {exc}", file=sys.stderr)
    if raw is not None:
        dump = json.dumps(raw, ensure_ascii=False)
        print(f"      raw[{len(dump)} chars]: {dump[:3000]}", file=sys.stderr)


def call_with_validation[T: BaseModel](
    emit: Callable[[], Any], validator: Validator[T], *, max_attempts: int = 2, label: str = "stage"
) -> T:
    last: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        raw: Any = None
        try:
            raw = emit()
            return _validate(raw, validator)
        except _RETRYABLE as exc:
            last = exc
            _log_failure(label, attempt, exc, raw)
    assert last is not None
    raise last
