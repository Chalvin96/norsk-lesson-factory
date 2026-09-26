"""Entry point: `main` lists long exercise prompts and likely review problems."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from lesson_builder.domain.lesson.validation.exercise_evidence import scan_exercise_evidence
from lesson_builder.formats.yaml import load_unique_yaml

K_DEFAULT_ROOT = Path("content/lessons")
K_DEFAULT_MIN_WORDS = 90
K_DEFAULT_LIMIT = 50
K_WORD_RE = re.compile(r"\b[\w]+(?:[-’'][\w]+)*\b", re.UNICODE)
K_NUMBERED_RE = re.compile(r"(?<!\w)(?:part\s+\d+|\d+[.):])(?=\s)", re.IGNORECASE)
K_QUANTITY_RE = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+)"
    r"(?:\s*(?:to|[-–])\s*(?:one|two|three|four|five|six|seven|eight|nine|ten|\d+))?\s+"
    r"(?:(?:short|complete|natural|new|separate|numbered|connected|resulting|target|"
    r"different|positive|negative|Norwegian|English)\s+)*"
    r"(?:sentence|sentences|response|responses|reply|replies|contribution|contributions|"
    r"line|lines|version|versions|message|messages|question|questions|clause|clauses|"
    r"part|parts|item|items|text|texts|exchange|exchanges|notice|notices|report|reports|"
    r"update|updates|situation|situations|meaning|meanings|function|functions|fact|facts|"
    r"form|forms|marker|markers|pattern|patterns|detail|details|relationship|relationships|"
    r"point|points|choice|choices|answer|answers)\b",
    re.IGNORECASE,
)
K_REPEAT_RE = re.compile(
    r"\b(?:for\s+each|each\s+of|all\s+(?:four|five|six|seven|eight|nine|ten|\d+)\b)"
    r"|(?<!\w)-\s+(?=[A-Z])",
    re.IGNORECASE,
)
K_DIRECTIVE_RE = re.compile(
    r"\b(?:write|use|include|keep|make|choose|answer|state|mention|add|begin|end|put|"
    r"place|identify|explain|report|describe|turn|return|do\s+not|avoid|select|give|"
    r"provide|cover|follow|preserve|control|paraphrase|draw|connect|complete|switch|"
    r"mark|check|read|rewrite|create|formulate|compare|express|retell|present|continue|"
    r"change|supply|introduce)\b",
    re.IGNORECASE,
)
K_CONSTRAINT_RE = re.compile(
    r"\b(?:exactly|at\s+least|at\s+most|must|only|do\s+not|don['’]t|never|without|"
    r"rather\s+than|instead\s+of|before\s+submitting|may\s+reuse|you\s+may|"
    r"keep\s+the|make\s+sure|ensure)\b",
    re.IGNORECASE,
)
K_CONTEXT_RE = re.compile(
    r"\b(?:situation|situations|context|fact\s+bank|source\s+conversation|supplied|"
    r"provided|scenario|dialogue|the\s+fact|source\s+excerpts|original\s+room|"
    r"revised\s+version|given\s+sentence)\b",
    re.IGNORECASE,
)
K_META_RE = re.compile(
    r"\b(?:without\s+looking\s+back|do\s+not\s+copy|rather\s+than\s+copying|"
    r"before\s+submitting|successful\s+retrieval|ordinary\s+vocabulary|use\s+your\s+own\s+wording|"
    r"original\s+wording|only\s+response|not\s+assessed|do\s+not\s+rewrite)\b",
    re.IGNORECASE,
)
K_LONG_SENTENCE_WORDS = 40
K_MULTI_OUTPUT_CUE_MIN = 2
K_DIRECTIVE_STACK_MIN = 6
K_CONSTRAINT_STACK_MIN = 5
K_CONTEXT_LOAD_MIN = 3
K_META_STACK_MIN = 2


def main(argv: list[str] | None = None) -> int:
    """Scan authored exercise YAML and print candidates without changing files."""
    parser = argparse.ArgumentParser(description="Find wordy exercises and explain likely problems")
    parser.add_argument("--root", type=Path, default=K_DEFAULT_ROOT)
    parser.add_argument("--min-words", type=int, default=K_DEFAULT_MIN_WORDS)
    parser.add_argument("--limit", type=int, default=K_DEFAULT_LIMIT, help="0 prints every candidate")
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    args = parser.parse_args(argv)
    if args.min_words < 1 or args.limit < 0:
        parser.error("--min-words must be positive and --limit must be non-negative")

    report = _scan(args.root, args.min_words)
    candidates = report["candidates"] if args.limit == 0 else report["candidates"][: args.limit]
    if args.json:
        report["candidates"] = candidates
        print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    else:
        _print_report(report, candidates)
    return 1 if report["errors"] else 0


def _scan(root: Path, min_words: int) -> dict[str, Any]:
    """Return long-prompt candidates and scan errors for one lesson root."""
    paths = _scan_paths(root)
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    exercise_count = 0
    for path in paths:
        file_candidates, file_errors, file_exercise_count = _scan_file(path, min_words)
        candidates.extend(file_candidates)
        errors.extend(file_errors)
        exercise_count += file_exercise_count
    candidates.sort(key=lambda item: (-item["words"], item["source_path"], item["handle"]))
    signals = Counter(signal["code"] for item in candidates for signal in item["signals"])
    return {
        "root": str(root),
        "threshold_words": min_words,
        "files": len(paths),
        "exercises": exercise_count,
        "candidate_count": len(candidates),
        "signal_counts": dict(sorted(signals.items())),
        "errors": errors,
        "candidates": candidates,
    }


def _scan_paths(root: Path) -> list[Path]:
    """Return exercise files below a validated scan root."""
    if not root.is_dir():
        raise OSError(f"scan root is not a directory: {root}")
    paths = sorted(root.rglob("exercises.yaml"))
    if not paths:
        raise OSError(f"no exercises.yaml files found below {root}")
    return paths


def _scan_file(path: Path, min_words: int) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    """Scan one exercise file and return candidates, errors, and item count."""
    raw, error = _load_exercise_source(path)
    if error is not None:
        return [], [error], 0
    if raw is None:
        raise RuntimeError(f"exercise source loader returned no data for {path}")
    return _scan_items(path, raw, min_words)


def _load_exercise_source(path: Path) -> tuple[list[Any] | None, dict[str, str] | None]:
    """Load one exercise YAML list or return its readable scan error."""
    try:
        raw = load_unique_yaml(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TypeError, ValueError, yaml.YAMLError) as exc:
        return None, {"source_path": str(path), "error": f"{type(exc).__name__}: {exc}"}
    if not isinstance(raw, list):
        return None, {"source_path": str(path), "error": "exercise source is not a YAML list"}
    return raw, None


def _scan_items(path: Path, items: list[Any], min_words: int) -> tuple[list[dict[str, Any]], list[dict[str, str]], int]:
    """Scan exercise items from one valid YAML list."""
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    exercise_count = 0
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append({"source_path": str(path), "error": f"item[{index}] is not a mapping"})
            continue
        exercise_count += 1
        candidate = _build_candidate(path, index, item, min_words)
        if candidate is not None:
            candidates.append(candidate)
    return candidates, errors, exercise_count


def _build_candidate(path: Path, index: int, item: dict[str, Any], min_words: int) -> dict[str, Any] | None:
    """Return one candidate with burden and existing evidence signals."""
    prompt = item.get("prompt_md")
    if not isinstance(prompt, str):
        return None
    words = len(K_WORD_RE.findall(prompt))
    if words < min_words:
        return None
    handle = item.get("handle") if isinstance(item.get("handle"), str) else f"item[{index}]"
    operation = item.get("op") if isinstance(item.get("op"), str) else "unknown"
    signals = _detect_burden_signals(prompt, words)
    signals.extend(
        {"code": finding.code, "evidence": finding.evidence, "why": finding.explanation}
        for finding in scan_exercise_evidence(item, handle)
    )
    return {
        "source_path": str(path),
        "lesson": path.parent.name,
        "handle": handle,
        "operation": operation,
        "words": words,
        "signals": signals,
        "prompt": prompt,
    }


def _detect_burden_signals(prompt: str, words: int) -> list[dict[str, str]]:
    """Explain deterministic signs that a long prompt may be worth manual review."""
    numbered = len(K_NUMBERED_RE.findall(prompt))
    quantified = len(K_QUANTITY_RE.findall(prompt))
    repeats = len(K_REPEAT_RE.findall(prompt))
    directives = len(K_DIRECTIVE_RE.findall(prompt))
    constraints = len(K_CONSTRAINT_RE.findall(prompt))
    context = len(K_CONTEXT_RE.findall(prompt))
    meta = len(K_META_RE.findall(prompt))
    sentences = [part for part in re.split(r"[.!?…]+\s*", prompt) if part.strip()]
    longest_sentence = max((len(K_WORD_RE.findall(part)) for part in sentences), default=0)
    signals: list[dict[str, str]] = []
    if numbered >= K_MULTI_OUTPUT_CUE_MIN or quantified >= K_MULTI_OUTPUT_CUE_MIN or repeats >= K_MULTI_OUTPUT_CUE_MIN:
        signals.append(
            {
                "code": "multi_output_cues",
                "evidence": f"numbered={numbered}; quantified={quantified}; repeat_cues={repeats}",
                "why": (
                    "More than one output cue is present; counts are triage evidence only and do not "
                    "prove the outputs are independently judgeable. Multiple cues may be one cohesive "
                    "submission, so confirm each output can be attempted, omitted, judged, and "
                    "remediated independently before considering a split."
                ),
            }
        )
    if directives >= K_DIRECTIVE_STACK_MIN or constraints >= K_CONSTRAINT_STACK_MIN:
        signals.append(
            {
                "code": "constraint_stack",
                "evidence": f"directives={directives}; constraint_cues={constraints}; words={words}",
                "why": (
                    "Review whether every constraint is essential to the target, context, or success criterion; "
                    "the count alone does not establish that any rule should be removed."
                ),
            }
        )
    if context >= K_CONTEXT_LOAD_MIN:
        signals.append(
            {
                "code": "context_load",
                "evidence": f"context_cues={context}; words={words}",
                "why": (
                    "Review whether every supplied dialogue, fact-bank, or background detail is needed for the answer; "
                    "context cues may belong to one cohesive task."
                ),
            }
        )
    if longest_sentence >= K_LONG_SENTENCE_WORDS:
        signals.append(
            {
                "code": "long_sentence",
                "evidence": f"longest_sentence_words={longest_sentence}",
                "why": (
                    "Review whether the long sentence can be clarified without separating constraints that belong "
                    "to one cohesive submission."
                ),
            }
        )
    if meta >= K_META_STACK_MIN:
        signals.append(
            {
                "code": "meta_instruction_stack",
                "evidence": f"meta_cues={meta}; words={words}",
                "why": (
                    "Review whether each process reminder is necessary for the learner action; the cue count alone "
                    "does not establish that a reminder should be removed."
                ),
            }
        )
    return signals


def _print_report(report: dict[str, Any], candidates: list[dict[str, Any]]) -> None:
    """Print the short terminal report used for manual exercise triage."""
    print(f"{report['candidate_count']} candidates >= {report['threshold_words']} words")
    print(f"Scanned {report['exercises']} exercises in {report['files']} files")
    print("Signals: " + ", ".join(f"{code}={count}" for code, count in report["signal_counts"].items()))
    for item in candidates:
        codes = ", ".join(signal["code"] for signal in item["signals"]) or "manual review only"
        print(f"{item['words']:>3}  {item['source_path']}  {item['handle']} [{item['operation']}]  {codes}")
        for signal in item["signals"]:
            print(f"     {signal['code']}: {signal['why']} ({signal['evidence']})")
    for error in report["errors"]:
        print(f"ERROR {error['source_path']}: {error['error']}")


if __name__ == "__main__":
    raise SystemExit(main())
