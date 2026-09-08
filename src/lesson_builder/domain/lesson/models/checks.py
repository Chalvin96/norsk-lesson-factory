"""Not a check itself — lesson validation result contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Severity = Literal["blocker", "warning", "info"]


class CheckResult(BaseModel):
    """One check's verdict on one unit of a lesson.

    check_id   — stable identifier of the check (e.g. "nynorsk_scan")
    severity   — blocker, warning, info
    unit_id    — the sub-unit the finding is about (objective id, exercise id, section id, or "" for whole-lesson)
    message    — human-readable description of the issue
    fix_hint   — optional guidance for the author/fix node; None when the check has no actionable hint
    advisory   — True for LLM judgments that inform review but do not route the gate
    revision_target — True when the issue should route into revision/fix loops without
                      hard-blocking final accept/export on its own
    """

    check_id: str
    severity: Severity
    unit_id: str = ""
    message: str
    fix_hint: str | None = None
    advisory: bool = False
    revision_target: bool = False

    @property
    def is_blocking(self) -> bool:
        return self.severity == "blocker" and not self.advisory and not self.revision_target


__all__ = ["CheckResult", "Severity"]
