"""Entry point: `main` runs the offline exercise-quality regression corpus."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import yaml

if __package__:
    from .eval_corpus import K_EXERCISE_QUALITY_FIXTURE
    from .eval_corpus import load_consensus_cases
    from .eval_corpus import project_semantic_case
    from .eval_metrics import EvalObservation
    from .eval_metrics import build_report
    from .eval_metrics import render_markdown_report
else:
    from eval_corpus import K_EXERCISE_QUALITY_FIXTURE
    from eval_corpus import load_consensus_cases
    from eval_corpus import project_semantic_case
    from eval_metrics import EvalObservation
    from eval_metrics import build_report
    from eval_metrics import render_markdown_report

from lesson_builder.application.operations.audit_source import audit_source_directory
from lesson_builder.domain.lesson.validation.review_payloads import extract_answer_questions
from lesson_builder.domain.lesson.validation.review_payloads import extract_attempt_questions
from lesson_builder.domain.lesson.validation.review_payloads import extract_open_rubric_questions

K_DEFAULT_OUTPUT = Path("store/evals/exercise-quality/report.json")
K_DEFAULT_THRESHOLDS = {
    "min_precision": 1.0,
    "min_recall": 1.0,
    "min_specificity": 1.0,
}
K_EVALUATION_CONFIG_KEY = "evaluation"
K_EXERCISE_QUALITY_CONFIG_KEY = "exercise_quality"
K_THRESHOLDS_KEY = "thresholds"
K_EVALUATION_THRESHOLD_KEYS = frozenset({"min_precision", "min_recall", "min_specificity"})
K_EVALUATION_MODES = ("all", "mechanical", "semantic")
K_EVALUATION_FAMILY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def main(argv: list[str] | None = None) -> int:
    """Run deterministic corpus checks and write JSON/Markdown evidence."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    corpus_path = args.corpus.resolve()
    config_path = args.config.resolve() if args.config else None
    all_cases = load_consensus_cases(corpus_path)
    cases = _select_cases(all_cases, args.mode)
    observations: list[EvalObservation] = []
    returned_case_ids: list[str] = []
    errors: list[dict[str, str]] = []
    with TemporaryDirectory(prefix="exercise-quality-eval-") as temporary_root:
        scratch_root = Path(temporary_root)
        for case in cases:
            _evaluate_case(case, scratch_root, observations, returned_case_ids, errors)

    report = build_report(
        observations,
        corpus_hash=_sha256_file(corpus_path),
        config_hash=_sha256_file(config_path) if config_path and config_path.is_file() else None,
        model="offline-deterministic",
        expected_case_ids=_expected_case_ids(cases),
        returned_case_ids=returned_case_ids,
        thresholds=_load_thresholds(config_path) if config_path else K_DEFAULT_THRESHOLDS,
    )
    report["corpus"] = _corpus_summary(cases, errors, selected_mode=args.mode)
    report["errors"] = errors
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path = output_path.with_suffix(".md")
    markdown_path.write_text(render_markdown_report(report), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 1 if args.strict and not report["gate"]["passed"] else 0


def _evaluate_case(
    case: dict[str, Any],
    scratch_root: Path,
    observations: list[EvalObservation],
    returned_case_ids: list[str],
    errors: list[dict[str, str]],
) -> None:
    """Evaluate both labeled states of one corpus case without touching the repository."""
    family = str(case["family"])
    handle = str(case["handle"])
    for state in ("bad", "good"):
        case_id = f"{handle}__{state}"
        try:
            if case["mode"] == "mechanical":
                predicted = _evaluate_mechanical_state(case, state, scratch_root)
                observations.append(
                    EvalObservation(
                        case_id,
                        family,
                        state,
                        predicted=predicted,
                        adversarial_category=str(case["adversarial_category"]),
                    )
                )
            else:
                _validate_semantic_state(case, state)
                observations.append(
                    EvalObservation(
                        case_id,
                        family,
                        state,
                        outcome="not_evaluated",
                        adversarial_category=str(case["adversarial_category"]),
                    )
                )
            returned_case_ids.append(case_id)
        except (OSError, TypeError, ValueError, KeyError) as exc:
            observations.append(
                EvalObservation(
                    case_id,
                    family,
                    state,
                    outcome="invalid",
                    adversarial_category=str(case["adversarial_category"]),
                )
            )
            returned_case_ids.append(case_id)
            errors.append({"case_id": case_id, "error": f"{type(exc).__name__}: {exc}"})


def _select_cases(cases: list[dict[str, Any]], mode: str) -> list[dict[str, Any]]:
    """Select the corpus slice whose evidence is required by this run."""
    if mode == "all":
        return cases
    return [case for case in cases if case.get("mode") == mode]


def _expected_case_ids(cases: list[dict[str, Any]]) -> list[str]:
    """Derive the required response IDs from corpus inputs, not evaluator output."""
    return [f"{case['handle']}__{state}" for case in cases for state in ("bad", "good")]


def _evaluate_mechanical_state(case: dict[str, Any], state: str, scratch_root: Path) -> str:
    """Run the production deterministic source audit and return its binary prediction."""
    snapshot = case[state]
    if not isinstance(snapshot, dict):
        raise TypeError(f"{case['family']} {state} snapshot must be a mapping")
    root = _case_scratch_root(scratch_root, case.get("family"), state)
    for filename, content in snapshot.items():
        if not isinstance(filename, str) or not isinstance(content, str):
            raise TypeError(f"{case['family']} {state} snapshot files must be text")
        target = _safe_snapshot_path(root, filename)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    audit = audit_source_directory(root)
    return "bad" if audit.material_findings else "good"


def _case_scratch_root(scratch_root: Path, family: object, state: str) -> Path:
    """Return a contained per-case root before creating any corpus-controlled path."""
    if not isinstance(family, str) or K_EVALUATION_FAMILY_RE.fullmatch(family) is None:
        raise ValueError(f"evaluation case family must be a safe identifier: {family!r}")
    root = scratch_root / family / state
    resolved_root = root.resolve(strict=False)
    try:
        resolved_root.relative_to(scratch_root.resolve())
    except ValueError as exc:
        raise ValueError(f"evaluation case family escapes the evaluation sandbox: {family!r}") from exc
    family_root = scratch_root / family
    if any(path.is_symlink() for path in (scratch_root, family_root, root)):
        raise ValueError(f"evaluation case family traverses a symlink: {family!r}")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_snapshot_path(root: Path, filename: str) -> Path:
    """Resolve one fixture filename without permitting traversal or symlink escape."""
    relative = Path(filename)
    if relative.is_absolute() or relative.anchor or not relative.parts or ".." in relative.parts:
        raise ValueError(f"snapshot filename must be a safe relative path: {filename!r}")
    resolved_root = root.resolve()
    target = root / relative
    resolved_target = target.resolve(strict=False)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"snapshot filename escapes evaluation sandbox: {filename!r}") from exc
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"snapshot filename traverses a symlink: {filename!r}")
    return target


def _validate_semantic_state(case: dict[str, Any], state: str) -> None:
    """Exercise the learner-visible projections while leaving semantic labeling to a model judge."""
    lesson = project_semantic_case(case, state)
    attempt = extract_attempt_questions(lesson)
    answers = extract_answer_questions(lesson)
    rubrics = extract_open_rubric_questions(lesson)
    if len(attempt) != 1 or len(answers) != 1:
        raise ValueError(f"{case['family']} {state} projection must contain one attempt and answer")
    if (
        case["handle"]
        in {
            "isolating-an-unfamiliar-word",
            "reconstruct_the_meeting_route",
            "practice_4_understand_and_accept",
        }
        and len(rubrics) != 1
    ):
        raise ValueError(f"{case['family']} {state} projection must contain one open rubric")


def _load_thresholds(config_path: Path) -> dict[str, float]:
    """Read numeric exercise-quality thresholds, falling back to conservative defaults."""
    if not config_path.is_file():
        return dict(K_DEFAULT_THRESHOLDS)
    document = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise TypeError("evaluation config must be a mapping")
    evaluation = document.get(K_EVALUATION_CONFIG_KEY, {})
    if not isinstance(evaluation, dict):
        raise TypeError("config.yaml evaluation must be a mapping")
    quality = evaluation.get(K_EXERCISE_QUALITY_CONFIG_KEY, {})
    if not isinstance(quality, dict):
        raise TypeError("config.yaml evaluation.exercise_quality must be a mapping")
    raw_thresholds = quality.get(K_THRESHOLDS_KEY, K_DEFAULT_THRESHOLDS)
    if not isinstance(raw_thresholds, dict):
        raise TypeError("exercise-quality thresholds must be a mapping")
    thresholds: dict[str, float] = {}
    for key, value in raw_thresholds.items():
        number = _validate_config_number(key, value, K_EVALUATION_THRESHOLD_KEYS, "threshold")
        if key.startswith("min_") and number > 1:
            raise ValueError(f"exercise-quality threshold {key!r} must be between 0 and 1")
        thresholds[key] = number
    return thresholds


def _validate_config_number(
    key: object,
    value: object,
    allowed_keys: frozenset[str],
    kind: str,
) -> float:
    """Validate one named evaluation limit before the gate can consume it."""
    if not isinstance(key, str) or key not in allowed_keys:
        raise ValueError(f"unsupported exercise-quality {kind} key: {key!r}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"exercise-quality {kind}s must map names to numbers")
    number = float(value)
    _validate_finite_number(number, key, kind)
    if number < 0:
        raise ValueError(f"exercise-quality {kind} {key!r} must be non-negative")
    return number


def _validate_finite_number(number: float, key: object, kind: str) -> None:
    """Reject non-finite evaluation limits before threshold comparisons."""
    if not math.isfinite(number):
        raise ValueError(f"exercise-quality {kind} {key!r} must be finite")


def _corpus_summary(cases: list[dict[str, Any]], errors: list[dict[str, str]], *, selected_mode: str) -> dict[str, Any]:
    """Return provenance fields for the committed labeled corpus."""
    return {
        "case_count": len(cases),
        "selected_mode": selected_mode,
        "families": sorted(str(case["family"]) for case in cases),
        "modes": {mode: sum(case.get("mode") == mode for case in cases) for mode in ("mechanical", "semantic")},
        "invalid_case_count": len(errors),
        "metadata": [
            {
                "family": case["family"],
                "handle": case["handle"],
                "mode": case["mode"],
                "surfaces": case.get("surfaces", []),
                "label_source": case["label_source"],
                "captured_at": case["captured_at"],
                "adversarial_category": case["adversarial_category"],
            }
            for case in cases
        ],
    }


def _sha256_file(path: Path) -> str:
    """Return the content hash used to identify a corpus or config input."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser without loading providers or changing content."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=K_EXERCISE_QUALITY_FIXTURE)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--output", type=Path, default=K_DEFAULT_OUTPUT)
    parser.add_argument(
        "--mode",
        choices=K_EVALUATION_MODES,
        default="all",
        help="evaluate all cases, or only mechanically/model-evaluable cases",
    )
    parser.add_argument("--strict", action="store_true", help="exit non-zero when configured quality gates fail")
    return parser


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
