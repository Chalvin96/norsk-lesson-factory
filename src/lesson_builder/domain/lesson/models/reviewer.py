"""Not a check itself — provider-neutral contracts for structured lesson review."""

from __future__ import annotations

from typing import Protocol
from typing import TypeVar

from pydantic import BaseModel

_ReviewModel = TypeVar("_ReviewModel", bound=BaseModel)
_ReviewModel_co = TypeVar("_ReviewModel_co", bound=BaseModel, covariant=True)


class StructuredReviewer(Protocol[_ReviewModel_co]):
    """Invoke one prompt and return a validated review model."""

    def invoke(self, prompt: str) -> _ReviewModel_co:
        """Return the structured review for ``prompt``."""


class ReviewerAgent(Protocol):
    """Bind a response model without exposing a concrete LLM provider."""

    def structured(self, schema: type[_ReviewModel]) -> StructuredReviewer[_ReviewModel]:
        """Return a reviewer bound to ``schema``."""


__all__ = ["ReviewerAgent", "StructuredReviewer"]
