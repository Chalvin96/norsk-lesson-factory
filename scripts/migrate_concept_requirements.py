"""Entry point: ``main``.

Lift CEFR level and objectives from existing lesson files onto concept
requirements cards, then re-baseline acceptance-log requirement hashes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from lesson_builder.pipeline.checks.validators.stale import requirements_hash
from lesson_builder.pipeline.concept_requirements import ConceptRequirements

K_CONCEPT_REQUIREMENTS_DIR = Path("data/concept_requirements")
K_LESSONS_DIR = Path("data/lessons")
K_ACCEPTANCE_LOG_PATH = Path("data/lesson_acceptance_log.jsonl")


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    stats = migrate_concept_requirements(repo_root)
    print(f"cards_total={stats.cards_total}")
    print(f"cards_migrated={stats.cards_migrated}")
    print(f"cards_already_current={stats.cards_already_current}")
    print(f"cards_missing_lessons={stats.cards_missing_lessons}")
    print(f"ledger_entries_rebaselined={stats.ledger_entries_rebaselined}")
    print(f"ledger_entries_already_current={stats.ledger_entries_already_current}")
    return 0


def migrate_concept_requirements(repo_root: Path) -> MigrationStats:
    requirements_dir = repo_root / K_CONCEPT_REQUIREMENTS_DIR
    lessons_dir = repo_root / K_LESSONS_DIR
    cards_total = 0
    cards_migrated = 0
    cards_already_current = 0
    cards_missing_lessons = 0
    requirement_hashes_by_slug: dict[str, str] = {}

    for requirements_path in sorted(requirements_dir.glob("*.json")):
        cards_total += 1
        slug = requirements_path.stem
        lesson_path = lessons_dir / f"{slug}.json"
        if not lesson_path.exists():
            cards_missing_lessons += 1
            continue

        current_payload = _load_json_object(requirements_path)
        lesson_payload = _load_json_object(lesson_path)
        next_payload = {
            **current_payload,
            "slug": slug,
            "cefr_level": lesson_payload["cefr_level"],
            "objectives": lesson_payload["objectives"],
        }
        validated = ConceptRequirements.model_validate(next_payload)
        next_text = _json_text(validated.model_dump(mode="json"))
        if requirements_path.read_text(encoding="utf-8") == next_text:
            cards_already_current += 1
        else:
            requirements_path.write_text(next_text, encoding="utf-8")
            cards_migrated += 1
        requirement_hashes_by_slug[slug] = requirements_hash(validated.model_dump(mode="json"))

    rebaselined, already_current = _rebaseline_acceptance_log(
        repo_root / K_ACCEPTANCE_LOG_PATH,
        requirement_hashes_by_slug,
    )
    return MigrationStats(
        cards_total=cards_total,
        cards_migrated=cards_migrated,
        cards_already_current=cards_already_current,
        cards_missing_lessons=cards_missing_lessons,
        ledger_entries_rebaselined=rebaselined,
        ledger_entries_already_current=already_current,
    )


@dataclass(frozen=True)
class MigrationStats:
    cards_total: int
    cards_migrated: int
    cards_already_current: int
    cards_missing_lessons: int
    ledger_entries_rebaselined: int
    ledger_entries_already_current: int


def _rebaseline_acceptance_log(
    log_path: Path,
    requirement_hashes_by_slug: dict[str, str],
) -> tuple[int, int]:
    if not log_path.exists():
        return (0, 0)

    rebaselined = 0
    already_current = 0
    next_lines: list[str] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        slug = entry["slug"]
        next_hash = requirement_hashes_by_slug.get(slug)
        if next_hash is None:
            next_lines.append(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
            continue
        if entry.get("requirements_hash") == next_hash:
            already_current += 1
        else:
            entry["requirements_hash"] = next_hash
            rebaselined += 1
        next_lines.append(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))

    log_path.write_text("\n".join(next_lines) + "\n", encoding="utf-8")
    return (rebaselined, already_current)


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
