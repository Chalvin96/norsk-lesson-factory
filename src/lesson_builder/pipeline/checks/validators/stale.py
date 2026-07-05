"""Entry point: ``stale_check``.

Deterministic check: flags when a lesson's recorded requirements hash no
longer matches the current requirements. Also exports ``requirements_hash``,
the hashing utility this check (and callers that record the hash) both use.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from lesson_builder.pipeline.checks.result import CheckResult


def stale_check(
    slug: str,
    current_requirements: dict[str, Any] | None,
    recorded_requirements_hash: str | None,
) -> list[CheckResult]:
    """Detect when a lesson's recorded requirements hash no longer matches the current requirements.

    If no current requirements or no recorded hash is available, this is a no-op.
    Imported lessons need a backfill before stale detection becomes load-bearing.
    """
    if not current_requirements or not recorded_requirements_hash:
        return []

    current_hash = requirements_hash(current_requirements)
    if current_hash == recorded_requirements_hash:
        return []

    return [
        CheckResult(
            check_id="stale_check",
            severity="warning",
            unit_id=slug,
            message="lesson requirements are stale",
            fix_hint="Re-run the lesson against the current requirements or backfill the new requirements hash.",
        )
    ]


def requirements_hash(requirements: dict[str, Any]) -> str:
    canonical = json.dumps(requirements, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
