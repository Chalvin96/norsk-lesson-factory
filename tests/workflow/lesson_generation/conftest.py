"""Entry point: `use_empty_terminology_policy`."""

import pytest

from lesson_builder.domain.lesson.models.terminology import TerminologyBans
from lesson_builder.workflow.lesson_generation import dependencies


@pytest.fixture(autouse=True)
def use_empty_terminology_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep workflow behavior tests independent of the canonical glossary."""
    monkeypatch.setattr(dependencies, "load_terminology_bans", TerminologyBans)
