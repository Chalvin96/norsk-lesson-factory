"""Entry points: `collect_authoring_prose_fingerprints`, `require_authoring_prose_baseline`, `require_authoring_prose_package`."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import cast

from lesson_builder.application.operations.audit_source import scan_authoring_prose_text
from lesson_builder.workspace.paths import WorkspacePaths

K_AUTHORING_PROSE_BASELINE_FILE = "scripts/authoring_prose_baseline.json"
K_AUTHORING_PROSE_BASELINE_VERSION = 1
K_AUTHORING_PROSE_MAX_ERROR_LOCATIONS = 10


def collect_authoring_prose_fingerprints(repo_root: Path) -> dict[str, list[str]]:
    """Inventory exact offending lines by lesson, code, and occurrence count."""
    lessons_root = WorkspacePaths(repo_root).lessons_root
    inventory: dict[str, list[str]] = {}
    for lesson_file in sorted(lessons_root.glob("*/lesson.md")):
        findings = scan_authoring_prose_text(lesson_file.read_text(encoding="utf-8"))
        if findings:
            inventory[lesson_file.parent.name] = sorted(
                _build_fingerprint(finding.code, finding.evidence) for finding in findings
            )
    return inventory


def require_authoring_prose_baseline(repo_root: Path) -> dict[str, list[str]]:
    """Reject any new or changed authoring-prose match before export/check."""
    root = Path(repo_root)
    current = collect_authoring_prose_fingerprints(root)
    allowed = _load_allowed_baseline(root)
    new_matches: dict[str, list[str]] = {}
    for lesson_id, fingerprints in current.items():
        unexpected = sorted((Counter(fingerprints) - Counter(allowed.get(lesson_id, []))).elements())
        if unexpected:
            new_matches[lesson_id] = unexpected
    if new_matches:
        raise ValueError(
            "new authoring prose in learner-facing lesson source: "
            + _build_new_match_locations(root, new_matches, allowed)
        )
    return current


def require_authoring_prose_package(repo_root: Path, lesson_id: str, lesson_text: str) -> None:
    """Reject a staged or approved package with new prose before canonical writes."""
    root = Path(repo_root)
    allowed = _load_allowed_baseline(root)
    fingerprints = [
        _build_fingerprint(finding.code, finding.evidence) for finding in scan_authoring_prose_text(lesson_text)
    ]
    unexpected = sorted((Counter(fingerprints) - Counter(allowed.get(lesson_id, []))).elements())
    if unexpected:
        detail = _build_new_match_locations(root, {lesson_id: unexpected}, allowed, {lesson_id: lesson_text})
        raise ValueError("new authoring prose in learner-facing lesson source: " + detail)


def _load_allowed_baseline(root: Path) -> dict[str, list[str]]:
    """Load the tracked legacy inventory, treating absent baselines as strict."""
    baseline_path = root / K_AUTHORING_PROSE_BASELINE_FILE
    if not baseline_path.is_file():
        return {}
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if baseline.get("version") != K_AUTHORING_PROSE_BASELINE_VERSION or not isinstance(baseline.get("lessons"), dict):
        raise ValueError(f"invalid authoring-prose baseline: {baseline_path}")
    allowed = baseline["lessons"]
    if any(
        not isinstance(lesson_id, str)
        or not isinstance(fingerprints, list)
        or any(not isinstance(fingerprint, str) for fingerprint in fingerprints)
        for lesson_id, fingerprints in allowed.items()
    ):
        raise ValueError(f"invalid authoring-prose baseline: {baseline_path}")
    return cast(dict[str, list[str]], allowed)


def _build_fingerprint(code: str, evidence: str) -> str:
    """Keep baseline lines compact without losing changed-line detection."""
    digest = hashlib.sha256(evidence.strip().encode("utf-8")).hexdigest()
    return f"{code}:{digest}"


def _build_new_match_locations(
    root: Path,
    new_matches: dict[str, list[str]],
    allowed: dict[str, list[str]],
    source_overrides: dict[str, str] | None = None,
) -> str:
    """Show actionable source locations for newly detected fingerprints."""
    details: list[str] = []
    for lesson_id, fingerprints in new_matches.items():
        remaining = Counter(fingerprints)
        legacy = Counter(allowed.get(lesson_id, []))
        lesson_file = WorkspacePaths(root).lessons_root / lesson_id / "lesson.md"
        lesson_text = (
            source_overrides[lesson_id] if source_overrides is not None else lesson_file.read_text(encoding="utf-8")
        )
        for finding in scan_authoring_prose_text(lesson_text):
            fingerprint = _build_fingerprint(finding.code, finding.evidence)
            if legacy[fingerprint] > 0:
                legacy[fingerprint] -= 1
            elif remaining[fingerprint] > 0:
                remaining[fingerprint] -= 1
                details.append(f"{lesson_id}/{finding.location} {finding.code}: {finding.evidence[:80]}")
    shown = K_AUTHORING_PROSE_MAX_ERROR_LOCATIONS
    return "; ".join(details[:shown]) + (f"; and {len(details) - shown} more" if len(details) > shown else "")


__all__ = [
    "collect_authoring_prose_fingerprints",
    "require_authoring_prose_baseline",
    "require_authoring_prose_package",
]
