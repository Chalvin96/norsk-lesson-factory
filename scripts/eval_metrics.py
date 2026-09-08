"""Entry point: `build_report` turns labeled evaluation observations into evidence."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from typing import Literal

K_EVAL_REPORT_SCHEMA_VERSION = 2
K_EVAL_LABELS = frozenset({"bad", "good"})
K_EVAL_OUTCOMES = frozenset({"evaluated", "not_evaluated", "invalid", "outage"})

EvalOutcome = Literal["evaluated", "not_evaluated", "invalid", "outage"]


@dataclass(frozen=True)
class EvalObservation:
    """One labeled case result, including enough metadata to audit a run."""

    case_id: str
    family: str
    expected: str
    predicted: str | None = None
    outcome: EvalOutcome = "evaluated"
    adversarial_category: str | None = None

    def __post_init__(self) -> None:
        if not self.case_id or not self.family:
            raise ValueError("evaluation observations require case_id and family")
        if self.expected not in K_EVAL_LABELS:
            raise ValueError(f"unsupported expected evaluation label: {self.expected!r}")
        if self.outcome not in K_EVAL_OUTCOMES:
            raise ValueError(f"unsupported evaluation outcome: {self.outcome!r}")
        if self.outcome == "evaluated" and self.predicted not in K_EVAL_LABELS:
            raise ValueError("evaluated observations require a bad or good prediction")
        if self.outcome != "evaluated" and self.predicted is not None:
            raise ValueError("non-evaluated observations cannot carry a prediction")
        if self.adversarial_category is not None and not self.adversarial_category.strip():
            raise ValueError("adversarial_category must be non-empty when provided")


def count_confusion_matrix(observations: Sequence[EvalObservation]) -> dict[str, int]:
    """Return binary bad-case confusion counts for evaluated observations only."""
    counts = {"true_positive": 0, "false_positive": 0, "true_negative": 0, "false_negative": 0}
    for observation in observations:
        if observation.outcome != "evaluated":
            continue
        expected_bad = observation.expected == "bad"
        predicted_bad = observation.predicted == "bad"
        if expected_bad and predicted_bad:
            counts["true_positive"] += 1
        elif not expected_bad and predicted_bad:
            counts["false_positive"] += 1
        elif not expected_bad and not predicted_bad:
            counts["true_negative"] += 1
        else:
            counts["false_negative"] += 1
    return counts


def calculate_classification_metrics(observations: Sequence[EvalObservation]) -> dict[str, float | None]:
    """Calculate precision, recall, specificity, and F1 without hiding empty sets."""
    counts = count_confusion_matrix(observations)
    true_positive = counts["true_positive"]
    false_positive = counts["false_positive"]
    true_negative = counts["true_negative"]
    false_negative = counts["false_negative"]
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    specificity = _ratio(true_negative, true_negative + false_positive)
    f1 = _ratio(2 * true_positive, 2 * true_positive + false_positive + false_negative)
    return {"precision": precision, "recall": recall, "specificity": specificity, "f1": f1}


def calculate_group_metrics(observations: Sequence[EvalObservation], *, by: str) -> dict[str, dict[str, Any]]:
    """Return outcome, confusion, and classification evidence for each group."""
    if by not in {"family", "adversarial_category"}:
        raise ValueError("grouping must be family or adversarial_category")
    groups: dict[str, list[EvalObservation]] = {}
    for observation in observations:
        group = getattr(observation, by)
        if group is None:
            continue
        groups.setdefault(group, []).append(observation)
    return {
        group: {
            "counts": _observation_counts(group_observations),
            "confusion": count_confusion_matrix(group_observations),
            "classification": calculate_classification_metrics(group_observations),
        }
        for group, group_observations in sorted(groups.items())
    }


def build_report(
    observations: Sequence[EvalObservation],
    *,
    corpus_hash: str,
    config_hash: str | None = None,
    prompt_hash: str | None = None,
    model: str | None = None,
    expected_case_ids: Sequence[str] = (),
    returned_case_ids: Sequence[str] = (),
    thresholds: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Build the JSON-serializable report used by the offline evaluator."""
    if not corpus_hash:
        raise ValueError("corpus_hash is required for an evaluation report")
    report: dict[str, Any] = {
        "schema_version": K_EVAL_REPORT_SCHEMA_VERSION,
        "provenance": {
            "corpus_sha256": corpus_hash,
            "config_sha256": config_hash,
            "prompt_sha256": prompt_hash,
            "model": model,
        },
        "counts": _observation_counts(observations),
        "confusion": count_confusion_matrix(observations),
        "classification": calculate_classification_metrics(observations),
        "by_family": calculate_group_metrics(observations, by="family"),
        "by_adversarial_category": calculate_group_metrics(observations, by="adversarial_category"),
        "returned_id_coverage": _id_coverage(expected_case_ids, returned_case_ids),
    }
    report["gate"] = evaluate_thresholds(report, thresholds or {})
    return report


def evaluate_thresholds(report: Mapping[str, Any], thresholds: Mapping[str, float]) -> dict[str, Any]:
    """Evaluate configured floors and ceilings, returning explicit failure reasons."""
    failures = _collect_classification_threshold_failures(report, thresholds)
    failures.extend(_collect_evidence_threshold_failures(report))
    return {"passed": not failures, "failures": failures, "thresholds": dict(thresholds)}


def render_markdown_report(report: Mapping[str, Any]) -> str:
    """Render the machine report as a concise reviewer-readable Markdown summary."""
    provenance = report["provenance"]
    counts = report["counts"]
    classification = report["classification"]
    gate = report["gate"]
    category_reports = report.get("by_adversarial_category", {})
    category_lines = [
        "## Adversarial categories",
        "",
        "| Category | Evaluated | Not evaluated | Invalid | Outage | Recall | Specificity |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for category, category_report in sorted(category_reports.items()):
        category_counts = category_report["counts"]
        category_classification = category_report["classification"]
        category_lines.append(
            f"| {category} | {category_counts['evaluated']} | {category_counts['not_evaluated']} | "
            f"{category_counts['invalid']} | {category_counts['outage']} | "
            f"{_format_metric(category_classification['recall'])} | "
            f"{_format_metric(category_classification['specificity'])} |"
        )
    return "\n".join(
        [
            "# Exercise-quality evaluation",
            "",
            f"- Gate: **{'PASS' if gate['passed'] else 'FAIL'}**",
            f"- Model: `{provenance.get('model') or 'offline-deterministic'}`",
            f"- Corpus SHA-256: `{provenance['corpus_sha256']}`",
            f"- Observations: {counts['observations']} ({counts['evaluated']} evaluated, "
            f"{counts['not_evaluated']} not evaluated, {counts['invalid']} invalid, {counts['outage']} outage)",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            f"| Precision | {_format_metric(classification['precision'])} |",
            f"| Recall | {_format_metric(classification['recall'])} |",
            f"| Specificity | {_format_metric(classification['specificity'])} |",
            f"| F1 | {_format_metric(classification['f1'])} |",
            "",
            *category_lines,
            "",
            "## Gate failures",
            "",
            *(["- None"] if gate["passed"] else [f"- {failure}" for failure in gate["failures"]]),
            "",
        ]
    )


def _collect_classification_threshold_failures(report: Mapping[str, Any], thresholds: Mapping[str, float]) -> list[str]:
    """Collect configured binary metric floor failures."""
    failures: list[str] = []
    classification = report.get("classification", {})
    for key in ("precision", "recall", "specificity"):
        minimum = thresholds.get(f"min_{key}")
        if minimum is None:
            continue
        actual = classification.get(key)
        if actual is None or actual < minimum:
            failures.append(f"{key} below minimum {minimum}")
    return failures


def _collect_evidence_threshold_failures(report: Mapping[str, Any]) -> list[str]:
    """Collect failures where required evaluation evidence is absent or incomplete."""
    failures: list[str] = []
    coverage = report.get("returned_id_coverage", {})
    if not coverage.get("complete", False):
        failures.append("returned case IDs are incomplete or duplicated")
    counts = report.get("counts", {})
    if counts.get("not_evaluated", 0):
        failures.append("not-evaluated observations are present")
    if counts.get("outage", 0):
        failures.append("provider outages are present")
    if counts.get("invalid", 0):
        failures.append("invalid observations are present")
    return failures


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _observation_counts(observations: Sequence[EvalObservation]) -> dict[str, int]:
    """Count every outcome without treating unevaluated work as a quality result."""
    return {
        "observations": len(observations),
        "evaluated": sum(item.outcome == "evaluated" for item in observations),
        "not_evaluated": sum(item.outcome == "not_evaluated" for item in observations),
        "invalid": sum(item.outcome == "invalid" for item in observations),
        "outage": sum(item.outcome == "outage" for item in observations),
    }


def _id_coverage(expected_case_ids: Sequence[str], returned_case_ids: Sequence[str]) -> dict[str, Any]:
    expected = Counter(expected_case_ids)
    returned = Counter(returned_case_ids)
    missing = sorted(case_id for case_id in expected if case_id not in returned)
    unexpected = sorted(case_id for case_id in returned if case_id not in expected)
    duplicates = sorted(case_id for case_id, count in returned.items() if count > 1)
    return {
        "expected": len(expected_case_ids),
        "returned": len(returned_case_ids),
        "missing": missing,
        "unexpected": unexpected,
        "duplicates": duplicates,
        "complete": not missing and not unexpected and not duplicates,
    }


def _format_metric(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _format_optional(value: object) -> str:
    return "n/a" if value is None else str(value)


__all__ = [
    "EvalObservation",
    "build_report",
    "calculate_classification_metrics",
    "count_confusion_matrix",
    "calculate_group_metrics",
    "evaluate_thresholds",
    "render_markdown_report",
]
