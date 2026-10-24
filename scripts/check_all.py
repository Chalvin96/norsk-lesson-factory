"""Entry point: `main` runs the ordered repository validation gate table."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from collections.abc import Sequence
from concurrent.futures import Future
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from dataclasses import dataclass
from functools import partial
from pathlib import Path

K_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
K_DIST_DIFF_PATHS = ("dist/lessons", "dist/catalog.json", "dist/schema/lesson.schema.json")
K_SERIAL_GATE_NAMES = frozenset({"regenerate-dist", "lesson-data check"})


@dataclass(frozen=True)
class Gate:
    """One named command in the repository validation table."""

    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class GateResult:
    """The captured result and elapsed time for one validation gate."""

    name: str
    returncode: int
    seconds: float
    output: str

    @property
    def is_passed(self) -> bool:
        """Return whether the gate exited successfully."""
        return self.returncode == 0


GateExecutor = Callable[[Gate, Path], GateResult]
CommandRunner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


def main(argv: Sequence[str] | None = None, *, execute_gate: GateExecutor | None = None) -> int:
    """Parse gate selection arguments and run the complete validation table."""
    arguments = _parse_arguments(argv)
    gates = build_gates(arguments.conventions_base)
    selected = _select_gates(gates, arguments.only, arguments.skip)
    return run_checks(selected, repo_root=K_REPOSITORY_ROOT, execute_gate=execute_gate)


def build_gates(conventions_base: str | None = None) -> tuple[Gate, ...]:
    """Build the ordered gate table, optionally using a CI convention baseline."""
    conventions = ["uv", "run", "python", "scripts/check_agent_conventions.py"]
    if conventions_base:
        conventions.extend(("--changed-from", conventions_base))
    return (
        Gate("conventions", tuple(conventions)),
        Gate("format:check", ("npm", "run", "format:check")),
        Gate("ruff check", ("uv", "run", "ruff", "check", ".")),
        Gate("mypy", ("uv", "run", "mypy", "src/")),
        Gate("frontend lint", ("npm", "run", "lint:frontend")),
        Gate("workbench:types:check", ("npm", "run", "workbench:types:check")),
        Gate("workbench:test", ("npm", "run", "workbench:test")),
        Gate("workbench:build", ("npm", "run", "workbench:build")),
        Gate("pytest", ("uv", "run", "pytest", "-q", "-n", "auto")),
        Gate(
            "check_authoring_prose_baseline.py",
            ("uv", "run", "python", "scripts/check_authoring_prose_baseline.py"),
        ),
        Gate("eval:exercise-quality", ("npm", "run", "eval:exercise-quality")),
        Gate(
            "regenerate-dist",
            ("uv", "run", "lesson-data", "regenerate-dist", "--repo-root", "."),
        ),
        Gate(
            "lesson-data check",
            ("uv", "run", "lesson-data", "check", "--workspace-root", ".", "--format", "json"),
        ),
        Gate("knowledge:validate", ("npm", "run", "knowledge:validate")),
    )


def run_checks(
    gates: Sequence[Gate],
    *,
    repo_root: Path,
    execute_gate: GateExecutor | None = None,
    run_command: CommandRunner | None = None,
) -> int:
    """Run every gate, print one ordered result per gate, and return its aggregate status."""
    executor = execute_gate or partial(_execute_gate, run_command=run_command)
    results = _collect_parallel_results(gates, executor, repo_root)
    for gate in gates:
        if gate.name in K_SERIAL_GATE_NAMES:
            results[gate.name] = _call_executor(executor, gate, repo_root)
    _print_results(gates, results)
    return int(any(not result.is_passed for result in results.values()))


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the optional CI baseline and local gate selection filters."""
    parser = argparse.ArgumentParser(description="Run the repository validation gates.")
    parser.add_argument("--conventions-base", help="git revision used as the convention-check baseline")
    parser.add_argument("--only", help="comma-separated gate names to run")
    parser.add_argument("--skip", help="comma-separated gate names to omit")
    arguments = parser.parse_args(argv)
    known = {gate.name for gate in build_gates()}
    for option in ("only", "skip"):
        value = getattr(arguments, option)
        if value is None:
            continue
        names = _split_names(value)
        unknown = sorted(set(names) - known)
        if unknown:
            parser.error(f"unknown {option} gate(s): {', '.join(unknown)}")
        if not names:
            parser.error(f"--{option} needs at least one gate name")
        setattr(arguments, option, tuple(names))
    return arguments


def _select_gates(
    gates: Sequence[Gate], only: tuple[str, ...] | None, skip: tuple[str, ...] | None
) -> tuple[Gate, ...]:
    """Apply local selection filters while retaining the table's declared order."""
    allowed = set(only) if only is not None else {gate.name for gate in gates}
    omitted = set(skip or ())
    return tuple(gate for gate in gates if gate.name in allowed and gate.name not in omitted)


def _split_names(value: str) -> tuple[str, ...]:
    """Split a comma-separated filter and discard surrounding whitespace."""
    return tuple(name.strip() for name in value.split(",") if name.strip())


def _collect_parallel_results(gates: Sequence[Gate], executor: GateExecutor, repo_root: Path) -> dict[str, GateResult]:
    """Run independent gates concurrently and retain results by gate name."""
    parallel_gates = [gate for gate in gates if gate.name not in K_SERIAL_GATE_NAMES]
    if not parallel_gates:
        return {}
    worker_count = min(len(parallel_gates), 8)
    results: dict[str, GateResult] = {}
    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="check") as pool:
        futures: dict[Future[GateResult], Gate] = {
            pool.submit(_call_executor, executor, gate, repo_root): gate for gate in parallel_gates
        }
        for future in as_completed(futures):
            gate = futures[future]
            results[gate.name] = _completed_result(gate, future)
    return results


def _call_executor(executor: GateExecutor, gate: Gate, repo_root: Path) -> GateResult:
    """Turn an unexpected executor exception into a failed gate result."""
    started = time.perf_counter()
    try:
        return executor(gate, repo_root)
    except Exception as error:
        return GateResult(gate.name, 1, time.perf_counter() - started, f"gate runner failed: {error}\n")


def _completed_result(gate: Gate, result: Future[GateResult] | GateResult) -> GateResult:
    """Resolve a future or pass through a direct result from the gate executor."""
    return result.result() if isinstance(result, Future) else result


def _print_results(gates: Sequence[Gate], results: dict[str, GateResult]) -> None:
    """Print ordered summaries and failed-gate output."""
    for gate in gates:
        result = results[gate.name]
        status = "PASS" if result.is_passed else "FAIL"
        print(f"{status} {result.name} {result.seconds:.2f}s")
        if not result.is_passed and result.output:
            print(result.output, end="" if result.output.endswith("\n") else "\n")


def _execute_gate(gate: Gate, repo_root: Path, *, run_command: CommandRunner | None = None) -> GateResult:
    """Run one command and add the derived distribution checks to its gate."""
    started = time.perf_counter()
    runner = run_command or _run_command
    try:
        if gate.name == "regenerate-dist":
            returncode, output = _run_regenerate_dist(gate.command, repo_root, run_command=runner)
        else:
            completed = runner(gate.command, repo_root)
            returncode, output = completed.returncode, _combined_output(completed)
    except OSError as error:
        returncode, output = 1, f"could not start gate: {error}\n"
    return GateResult(gate.name, returncode, time.perf_counter() - started, output)


def _run_command(command: Sequence[str], repo_root: Path) -> subprocess.CompletedProcess[str]:
    """Run one command without a shell and capture both output streams."""
    return subprocess.run(command, cwd=repo_root, capture_output=True, text=True, check=False)


def _run_regenerate_dist(
    command: Sequence[str],
    repo_root: Path,
    *,
    run_command: CommandRunner | None = None,
) -> tuple[int, str]:
    """Regenerate exports, then verify tracked, untracked, filename, and schema state."""
    runner = run_command or _run_command
    output: list[str] = []
    regenerated = runner(command, repo_root)
    output.append(_combined_output(regenerated))
    if regenerated.returncode:
        return regenerated.returncode, "".join(output)

    diff = runner(("git", "diff", "--exit-code", "--", *K_DIST_DIFF_PATHS), repo_root)
    output.append(_combined_output(diff))
    if diff.returncode:
        return diff.returncode, "".join(output)
    untracked = runner(("git", "status", "--porcelain", "--untracked-files=all", "--", "dist/lessons"), repo_root)
    output.append(_combined_output(untracked))
    if untracked.returncode or untracked.stdout.strip():
        return 1, "".join(output) + "regeneration created untracked dist/lessons files\n"
    structure_error = _check_dist_structure(repo_root, run_command=runner)
    if structure_error:
        return 1, "".join(output) + structure_error
    return 0, ""


def _check_dist_structure(repo_root: Path, *, run_command: CommandRunner | None = None) -> str:
    """Check that exported lesson names and schema versions match current source."""
    runner = run_command or _run_command
    source_root = repo_root / "content" / "lessons"
    dist_root = repo_root / "dist" / "lessons"
    expected = sorted(path.name + ".json" for path in source_root.iterdir() if path.is_dir())
    actual = sorted(path.name for path in dist_root.glob("*.json")) if dist_root.is_dir() else []
    if expected != actual:
        return (
            f"dist/lessons filenames do not match content/lessons directories\nexpected: {expected}\nactual: {actual}\n"
        )
    version_process = runner(
        (
            "uv",
            "run",
            "python",
            "-c",
            "from lesson_builder.domain.lesson.settings import K_LESSON_SCHEMA_VERSION; print(K_LESSON_SCHEMA_VERSION)",
        ),
        repo_root,
    )
    if version_process.returncode:
        return _combined_output(version_process)
    expected_version = version_process.stdout.strip()
    for path in sorted(dist_root.glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            return f"could not read {path}: {error}\n"
        if document.get("schema_version") != expected_version:
            return f"retired or missing lesson schema version in {path}\n"
    return ""


def _combined_output(completed: subprocess.CompletedProcess[str]) -> str:
    """Keep captured stdout and stderr available for a failed gate report."""
    return completed.stdout + completed.stderr


if __name__ == "__main__":
    sys.exit(main())
