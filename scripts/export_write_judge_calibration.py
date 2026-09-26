"""Entry point: `main` exports the write-judge calibration bank as portable JSON.

`project_calibration_bank` validates the committed case file and returns the
schema-versioned projection that the consuming learner application reads; it
fails closed while any label still carries seed provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from lesson_builder.workspace.paths import get_workspace_root

K_DEFAULT_BANK_PATH = get_workspace_root() / "evals" / "promptfoo" / "cases" / "write_judge_calibration.yaml"
K_CALIBRATION_SCHEMA_VERSION = "1.1"
K_SEED_PROVENANCE = "seed_pending_human_review"
K_REVIEWED_PROVENANCE = "human_reviewed"
K_ALLOWED_PROVENANCE = frozenset({K_SEED_PROVENANCE, K_REVIEWED_PROVENANCE})
K_ALLOWED_SPLITS = frozenset({"dev", "holdout"})
K_ALLOWED_LABELS = frozenset({"pass", "fail"})
K_ALLOWED_ERROR_CLASSES = frozenset(
    {
        "valid_paraphrase",
        "correct_form_wrong_meaning",
        "missing_required_content",
        "good_message_minor_error",
        "fluent_off_task",
    }
)
K_REQUIRED_CASE_FIELDS = (
    "case_id",
    "archetype",
    "error_class",
    "split",
    "expected_label",
    "dimension",
    "source",
    "label_provenance",
)
K_CANDIDATE_HEADINGS = ("Stimulus", "Learner task", "Criteria", "Judge instructions", "Learner response")
K_REQUIRED_CANDIDATE_HEADINGS = ("Learner task", "Criteria", "Judge instructions", "Learner response")
K_HEADING_PATTERN = re.compile(rf"^({'|'.join(K_CANDIDATE_HEADINGS)}):[ \t]*(.*)$")
K_CRITERION_PATTERN = re.compile(r"^(?P<id>[a-z0-9-]+) \((?P<kind>[^)]+)\): (?P<instruction>.+)$")
K_OBSERVATION_KIND_PREFIX = "observation"


def main(argv: list[str] | None = None) -> int:
    """Project the bank to JSON on stdout and optionally to ``--output``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        payload = project_calibration_bank(args.bank, allow_seed_labels=args.allow_seed_labels)
    except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    rendered = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


def project_calibration_bank(bank_path: Path, *, allow_seed_labels: bool) -> dict[str, Any]:
    """Validate one calibration bank and return its deterministic projection."""
    records = [_project_case(case) for case in _load_cases(bank_path)]
    _validate_unique_case_ids(records)
    pending = sorted(record["case_id"] for record in records if record["label_provenance"] == K_SEED_PROVENANCE)
    if pending and not allow_seed_labels:
        raise ValueError(
            f"{len(pending)} case(s) still carry {K_SEED_PROVENANCE}; "
            "re-run with --allow-seed-labels to export non-approvable labels"
        )
    anchors = [record for record in records if record["split"] == "dev"]
    holdouts = [_attach_anchor_ids(record, anchors) for record in records if record["split"] == "holdout"]
    _reject_holdout_anchor_overlap(anchors, holdouts)
    return {
        "schema_version": K_CALIBRATION_SCHEMA_VERSION,
        "bank_sha256": hashlib.sha256(bank_path.read_bytes()).hexdigest(),
        "label_state": {
            "readiness": K_SEED_PROVENANCE if pending else K_REVIEWED_PROVENANCE,
            "approvable": not pending,
            "pending_seed_case_ids": pending,
            "provenance_counts": _count_provenances(records),
        },
        "dev_anchors": anchors,
        "holdout_cases": holdouts,
    }


def _build_parser() -> argparse.ArgumentParser:
    """Build the parser without reading the bank or calling any provider."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bank", type=Path, default=K_DEFAULT_BANK_PATH)
    parser.add_argument("--output", type=Path, default=None, help="also write the projection to this path")
    parser.add_argument(
        "--allow-seed-labels",
        action="store_true",
        help="export while labels are still seed-pending; output stays non-approvable",
    )
    return parser


def _load_cases(path: Path) -> list[dict[str, Any]]:
    """Read the calibration bank as a list of case mappings."""
    document: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, list) or not all(isinstance(case, dict) for case in document):
        raise TypeError("write-judge calibration bank must be a YAML list of mappings")
    return list(document)


def _project_case(case: dict[str, Any]) -> dict[str, Any]:
    """Validate one case and return its exact structured calibration record."""
    metadata = _require_mapping(case.get("metadata"), "metadata")
    missing = [field for field in K_REQUIRED_CASE_FIELDS if not metadata.get(field)]
    if missing:
        raise ValueError(f"calibration case is missing metadata: {', '.join(missing)}")
    if metadata["split"] not in K_ALLOWED_SPLITS:
        raise ValueError(f"unsupported split: {metadata['split']!r}")
    if metadata["expected_label"] not in K_ALLOWED_LABELS:
        raise ValueError(f"unsupported expected_label: {metadata['expected_label']!r}")
    if metadata["error_class"] not in K_ALLOWED_ERROR_CLASSES:
        raise ValueError(f"unsupported error_class: {metadata['error_class']!r}")
    if metadata["label_provenance"] not in K_ALLOWED_PROVENANCE:
        raise ValueError(f"unsupported label_provenance: {metadata['label_provenance']!r}")
    variables = _require_mapping(case.get("vars"), "vars")
    sections = _split_candidate(_require_text(variables.get("candidate"), "candidate"))
    for heading in K_REQUIRED_CANDIDATE_HEADINGS:
        if heading not in sections:
            raise ValueError(f"candidate is missing the {heading} section")
    criteria = _parse_criteria(sections["Criteria"])
    stressors, primary_failed_criterion = _case_stress_metadata(metadata, criteria)
    return {
        "case_id": metadata["case_id"],
        "archetype": metadata["archetype"],
        "error_class": metadata["error_class"],
        "split": metadata["split"],
        "expected_label": metadata["expected_label"],
        "dimension": metadata["dimension"],
        "source": metadata["source"],
        "label_provenance": metadata["label_provenance"],
        "stressors": stressors,
        "primary_failed_criterion": primary_failed_criterion,
        "task": _normalize(sections["Learner task"]),
        "judge_instructions": _normalize(sections["Judge instructions"]),
        "criteria": criteria,
        "stimulus": _normalize(sections["Stimulus"]) if "Stimulus" in sections else None,
        "response": _normalize(sections["Learner response"]),
    }


def _split_candidate(candidate: str) -> dict[str, str]:
    """Split one embedded candidate into its named authored sections."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in candidate.splitlines():
        match = K_HEADING_PATTERN.match(line)
        if match is not None:
            current = match.group(1)
            if current in sections:
                raise ValueError(f"candidate repeats the {current} section")
            sections[current] = [match.group(2)]
        elif current is not None:
            sections[current].append(line)
    return {heading: "\n".join(lines).strip() for heading, lines in sections.items()}


def _case_stress_metadata(metadata: dict[str, Any], criteria: list[dict[str, Any]]) -> tuple[list[str], str | None]:
    """Validate optional stress metadata and return normalized defaults."""
    stressors = metadata.get("stressors", [])
    if not isinstance(stressors, list) or not all(isinstance(stressor, str) and stressor for stressor in stressors):
        raise TypeError("calibration stressors must be a list of non-empty strings")
    primary_failed_criterion = metadata.get("primary_failed_criterion")
    if metadata["expected_label"] == "fail" and not isinstance(primary_failed_criterion, str):
        raise TypeError("negative calibration case requires primary_failed_criterion")
    if metadata["expected_label"] == "pass" and primary_failed_criterion is not None:
        raise TypeError("positive calibration case forbids primary_failed_criterion")
    if isinstance(primary_failed_criterion, str):
        if not primary_failed_criterion:
            raise TypeError("calibration primary_failed_criterion must be non-empty text")
        gating_ids = {criterion["id"] for criterion in criteria if criterion["gating"]}
        if primary_failed_criterion not in gating_ids:
            raise ValueError("calibration primary_failed_criterion must identify a gating criterion")
    return stressors, primary_failed_criterion


def _parse_criteria(section: str) -> list[dict[str, Any]]:
    """Parse the criteria section into ordered exactly-structured entries."""
    entries: list[str] = []
    for line in section.splitlines():
        if line.startswith("- "):
            entries.append(line[2:].strip())
        elif entries and line.strip():
            entries[-1] = f"{entries[-1]} {line.strip()}"
    criteria: list[dict[str, Any]] = []
    for entry in entries:
        match = K_CRITERION_PATTERN.match(entry)
        if match is None:
            raise ValueError(f"malformed embedded criterion: {entry}")
        criteria.append(
            {
                "id": match.group("id"),
                "instruction": _normalize(match.group("instruction")),
                "gating": not match.group("kind").strip().startswith(K_OBSERVATION_KIND_PREFIX),
            }
        )
    if not criteria:
        raise ValueError("criteria section is empty")
    return criteria


def _attach_anchor_ids(record: dict[str, Any], anchors: list[dict[str, Any]]) -> dict[str, Any]:
    """Return one holdout record linked to the dev anchors of its archetype."""
    anchor_ids = sorted(anchor["case_id"] for anchor in anchors if anchor["archetype"] == record["archetype"])
    return {**record, "anchor_case_ids": anchor_ids}


def _reject_holdout_anchor_overlap(anchors: list[dict[str, Any]], holdouts: list[dict[str, Any]]) -> None:
    """Fail closed if any held-out response also appears among the anchors."""
    anchor_responses = {anchor["response"] for anchor in anchors}
    for holdout in holdouts:
        if holdout["response"] in anchor_responses:
            raise ValueError(f"holdout response duplicated in anchors: {holdout['case_id']}")


def _validate_unique_case_ids(records: list[dict[str, Any]]) -> None:
    """Reject duplicate stable identifiers before they reach the projection."""
    seen: set[str] = set()
    for record in records:
        case_id = record["case_id"]
        if case_id in seen:
            raise ValueError(f"duplicate case_id: {case_id}")
        seen.add(case_id)


def _count_provenances(records: list[dict[str, Any]]) -> dict[str, int]:
    """Return the label-provenance counts for the bank."""
    counts: dict[str, int] = {}
    for record in records:
        provenance = record["label_provenance"]
        counts[provenance] = counts.get(provenance, 0) + 1
    return dict(sorted(counts.items()))


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    """Require one calibration field to be a mapping."""
    if not isinstance(value, dict):
        raise TypeError(f"calibration {label} must be a mapping")
    return value


def _require_text(value: object, label: str) -> str:
    """Require one calibration field to be non-empty text."""
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"calibration {label} must be non-empty text")
    return value


def _normalize(text: str) -> str:
    """Collapse whitespace so projection output is byte-deterministic."""
    return " ".join(text.split())


__all__ = ["main", "project_calibration_bank"]


if __name__ == "__main__":
    raise SystemExit(main())
