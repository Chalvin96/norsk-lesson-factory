"""Not a check itself — shared cancellation types and the provider-call scope."""

from __future__ import annotations

from collections.abc import Callable
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from threading import Event
from threading import Lock
from typing import Literal

CancellationReason = Literal["author", "shutdown", "deadline"]

K_CLIENTS_ACTIVE_CANCELLATION_TOKEN: ContextVar[CancellationToken | None] = ContextVar(
    "active_cancellation_token", default=None
)


class Cancelled(RuntimeError):
    """One cancellation signal with the reason selected by its owner."""

    def __init__(self, reason: CancellationReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class CancellationToken:
    """Thread-safe cancellation state and optional active-operation interrupt hook."""

    def __init__(self) -> None:
        self._cancelled = Event()
        self._lock = Lock()
        self._reason: CancellationReason | None = None
        self._message: str | None = None
        self._interrupt_hook: Callable[[], None] | None = None

    @property
    def is_cancelled(self) -> bool:
        with self._lock:
            return self._reason is not None

    @property
    def reason(self) -> CancellationReason | None:
        with self._lock:
            return self._reason

    def cancel(self, reason: CancellationReason, message: str) -> bool:
        """Set the first cancellation reason and run the registered interrupt hook."""
        with self._lock:
            if self._reason is not None:
                return False
            self._reason = reason
            self._message = message
            self._cancelled.set()
            interrupt_hook = self._interrupt_hook
        if interrupt_hook is not None:
            interrupt_hook()
        return True

    def set_interrupt_hook(self, interrupt_hook: Callable[[], None]) -> None:
        """Register an interrupt hook, running it immediately after an earlier cancel."""
        with self._lock:
            self._interrupt_hook = interrupt_hook
            already_cancelled = self._reason is not None
        if already_cancelled:
            interrupt_hook()

    def clear_interrupt_hook(self) -> None:
        """Forget the active operation hook after its remote turn finishes."""
        with self._lock:
            self._interrupt_hook = None

    def wait(self, timeout: float | None = None) -> bool:
        """Wait until cancellation is requested or the timeout elapses."""
        return self._cancelled.wait(timeout)

    def raise_if_cancelled(self) -> None:
        """Raise the normalized cancellation carrying this token's first reason."""
        with self._lock:
            reason = self._reason
            message = self._message
        if reason is not None:
            raise Cancelled(reason, message or "Operation cancelled.")


def active_cancellation_token() -> CancellationToken | None:
    """Return the token installed around the current provider operation, if any."""
    return K_CLIENTS_ACTIVE_CANCELLATION_TOKEN.get()


@contextmanager
def provider_call_cancellation(cancellation: CancellationToken) -> Iterator[None]:
    """Bridge one workbench operation token into every provider call in its context."""
    context_token = K_CLIENTS_ACTIVE_CANCELLATION_TOKEN.set(cancellation)
    try:
        yield
    finally:
        K_CLIENTS_ACTIVE_CANCELLATION_TOKEN.reset(context_token)


__all__ = [
    "Cancelled",
    "CancellationReason",
    "CancellationToken",
    "active_cancellation_token",
    "provider_call_cancellation",
]
