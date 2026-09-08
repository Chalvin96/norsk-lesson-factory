"""Entry point: check_environment reports local capability readiness.

The operation is read-only and never installs packages, invokes a provider,
performs synthesis, or writes a workspace artifact.
"""

from __future__ import annotations

import importlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from lesson_builder.application.operations.synthesize_audio import validate_audio_configuration
from lesson_builder.application.settings import K_APPLICATION_MIN_PROMPTFOO_NODE_VERSION
from lesson_builder.clients.llm.config import load_llm_jobs
from lesson_builder.clients.llm.config import load_llm_timeout_seconds
from lesson_builder.formats.markdown.pandoc import compiler_fingerprint
from lesson_builder.workspace.paths import WorkspacePaths

Capability = Literal["offline", "generation", "audio", "eval"]
FindingStatus = Literal["passed", "failed", "incomplete", "unverified", "skipped"]
ReportStatus = Literal["ready", "blocked", "incomplete"]
K_REQUIRED_IMPORTS = ("yaml", "pydantic", "pypandoc")
K_AUDIO_ENV = "LESSON_AUDIO_SERVICE_ACCOUNT"
K_PROBE_TIMEOUT_SECONDS = 3
K_DOCTOR_NODE_VERSION_PART_COUNT = 3


@dataclass(frozen=True)
class EnvironmentFinding:
    """One stable capability check and its actionable interpretation."""

    check_id: str
    capability: Capability
    status: FindingStatus
    summary: str
    next_action: str | None = None


@dataclass(frozen=True)
class EnvironmentReport:
    """The complete deterministic doctor report."""

    schema_version: int
    workspace_root: str
    capabilities: tuple[Capability, ...]
    status: ReportStatus
    findings: tuple[EnvironmentFinding, ...]


def check_environment(root: Path, *, capabilities: tuple[Capability, ...] = ("offline",)) -> EnvironmentReport:
    """Check selected local capabilities without external side effects."""
    selected = _normalize_capabilities(capabilities)
    paths = WorkspacePaths(Path(root).resolve())
    offline = _check_offline(paths)
    findings: list[EnvironmentFinding] = list(offline)
    if "generation" in selected:
        findings.extend(_check_generation(paths, offline))
    if "audio" in selected:
        findings.extend(_check_audio(paths, offline))
    if "eval" in selected:
        findings.extend(_check_eval(paths, offline))
    visible = tuple(
        finding for finding in findings if finding.capability == "offline" or finding.capability in selected
    )
    report_capabilities = tuple(dict.fromkeys(("offline", *selected)))
    return EnvironmentReport(1, str(paths.root), report_capabilities, _aggregate_status(visible), visible)


def _check_offline(paths: WorkspacePaths) -> list[EnvironmentFinding]:
    """Run prerequisites shared by every capability."""
    return [_check_python(), _check_imports(), _check_paths(paths), _check_config(paths), _check_pandoc()]


def _check_generation(paths: WorkspacePaths, offline: list[EnvironmentFinding]) -> list[EnvironmentFinding]:
    """Check generation-only local tools and routing configuration."""
    if any(f.status == "failed" for f in offline):
        return [
            _skip_blocked_capability(
                "generation.prerequisites", "generation", "generation checks are blocked by offline prerequisites"
            )
        ]
    locking = (
        EnvironmentFinding(
            "generation.platform_locking",
            "generation",
            "failed",
            "native Windows generation is unsupported; use WSL",
            "use WSL for generation, or native Windows for offline editing only",
        )
        if platform.system() == "Windows"
        else EnvironmentFinding(
            "generation.platform_locking", "generation", "passed", f"POSIX locking is available on {platform.system()}"
        )
    )
    findings = [
        locking,
        _probe_executable(
            "generation.opencode", "generation", "opencode", "install OpenCode and authenticate separately"
        ),
    ]
    try:
        jobs = load_llm_jobs(repo_root=paths.root)
    except (FileNotFoundError, TypeError, ValueError, yaml.YAMLError) as exc:
        findings.append(
            EnvironmentFinding(
                "generation.routing",
                "generation",
                "failed",
                f"LLM routing configuration is not usable ({type(exc).__name__})",
                "fix config.yaml tiers, jobs, or referenced agent templates",
            )
        )
    else:
        findings.append(
            EnvironmentFinding(
                "generation.routing",
                "generation",
                "passed",
                f"LLM routing and {len(jobs)} agent templates are structurally valid",
                "provider login, model access, and quota remain unverified",
            )
        )
    findings.append(_check_generation_timeout(paths))
    findings.append(
        EnvironmentFinding(
            "generation.provider_access",
            "generation",
            "unverified",
            "provider authentication and model access were not tested",
            "run a deliberate generation smoke test when authorized",
        )
    )
    return findings


def _check_audio(paths: WorkspacePaths, offline: list[EnvironmentFinding]) -> list[EnvironmentFinding]:
    """Check complete provider-free audio configuration and credential structure."""
    if any(f.status == "failed" for f in offline):
        return [
            _skip_blocked_capability(
                "audio.prerequisites", "audio", "audio checks are blocked by offline prerequisites"
            )
        ]
    configuration = _check_audio_configuration(paths.root)
    configured = os.environ.get(K_AUDIO_ENV, "").strip()
    credential = Path(configured).expanduser() if configured else None
    if credential and not credential.is_absolute():
        credential = paths.root / credential
    return [configuration, _inspect_audio_credential(credential), _report_provider_access_unverified()]


def _check_audio_configuration(root: Path) -> EnvironmentFinding:
    """Reuse production audio validation without credentials or synthesis."""
    try:
        validate_audio_configuration(repo_root=root)
    except (FileNotFoundError, OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        return EnvironmentFinding(
            "audio.configuration",
            "audio",
            "failed",
            f"audio configuration is not usable ({type(exc).__name__})",
            "fix config.yaml audio fields, voice profiles, or character references",
        )
    return EnvironmentFinding(
        "audio.configuration",
        "audio",
        "passed",
        "complete audio configuration is structurally valid",
    )


def _report_provider_access_unverified() -> EnvironmentFinding:
    """Record that remote TTS permissions are outside a read-only doctor."""
    return EnvironmentFinding(
        "audio.provider_access",
        "audio",
        "unverified",
        "credentials were inspected structurally; provider permissions were not tested",
        "run a deliberate synthesis smoke test when authorized",
    )


def _check_generation_timeout(paths: WorkspacePaths) -> EnvironmentFinding:
    """Validate the production LLM timeout setting without starting a client."""
    try:
        timeout_seconds = load_llm_timeout_seconds(repo_root=paths.root)
    except (FileNotFoundError, TypeError, ValueError, yaml.YAMLError) as exc:
        return EnvironmentFinding(
            "generation.timeout",
            "generation",
            "failed",
            f"LLM timeout configuration is not usable ({type(exc).__name__})",
            "set config.yaml llm.timeout_seconds to a positive integer",
        )
    return EnvironmentFinding(
        "generation.timeout",
        "generation",
        "passed",
        f"LLM timeout is configured ({timeout_seconds} seconds)",
    )


def _check_eval(paths: WorkspacePaths, offline: list[EnvironmentFinding]) -> list[EnvironmentFinding]:
    """Check the local runtime used by optional evaluation scripts."""
    if any(f.status == "failed" for f in offline):
        return [
            _skip_blocked_capability(
                "eval.prerequisites", "eval", "evaluation checks are blocked by offline prerequisites"
            )
        ]
    generation = _check_generation(paths, offline)
    generation_findings = [
        EnvironmentFinding(
            f"eval.{finding.check_id}",
            "eval",
            finding.status,
            finding.summary,
            finding.next_action,
        )
        for finding in generation
    ]
    node = _check_node_version()
    npm = _probe_executable("eval.npm", "eval", "npm", "install npm with Node.js for evaluation")
    passed = node.status == npm.status == "passed"
    runner_status: FindingStatus = (
        "passed" if passed else "incomplete" if "incomplete" in {node.status, npm.status} else "failed"
    )
    return [
        *generation_findings,
        node,
        npm,
        EnvironmentFinding(
            "eval.runner",
            "eval",
            runner_status,
            "Node.js and npm are available for evaluation" if passed else "evaluation runner is incomplete",
            None if passed else "install or verify Node.js and npm, then rerun the doctor",
        ),
    ]


def _check_python() -> EnvironmentFinding:
    """Check the active interpreter version."""
    version = sys.version_info
    ok = (version.major, version.minor) >= (3, 12)
    return EnvironmentFinding(
        "offline.python",
        "offline",
        "passed" if ok else "failed",
        f"Python {version.major}.{version.minor} is supported"
        if ok
        else f"Python {version.major}.{version.minor} is below required 3.12",
        None if ok else "install Python 3.12+ and rerun bootstrap",
    )


def _check_imports() -> EnvironmentFinding:
    """Check imports required by provider-free parsing and validation."""
    missing: list[str] = []
    for name in K_REQUIRED_IMPORTS:
        try:
            importlib.import_module(name)
        except ImportError:
            missing.append(name)
    return EnvironmentFinding(
        "offline.imports",
        "offline",
        "failed" if missing else "passed",
        "required Python imports are available" if not missing else "missing imports: " + ", ".join(missing),
        None if not missing else "run uv sync --locked, then rerun the doctor",
    )


def _check_paths(paths: WorkspacePaths) -> EnvironmentFinding:
    """Check repository paths needed for offline authoring."""
    required = (paths.content_root, paths.catalog_file, paths.curriculum_plan, paths.lessons_root)
    missing = [str(path.relative_to(paths.root)) for path in required if not path.exists()]
    return EnvironmentFinding(
        "offline.workspace_paths",
        "offline",
        "failed" if missing else "passed",
        "canonical content and lesson paths are present" if not missing else "missing paths: " + ", ".join(missing),
        None if not missing else "run from the repository root and restore the missing content paths",
    )


def _check_config(paths: WorkspacePaths) -> EnvironmentFinding:
    """Check provider-free configuration parsing."""
    try:
        payload = yaml.safe_load((paths.root / "config.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return EnvironmentFinding(
            "offline.configuration",
            "offline",
            "failed",
            f"config.yaml cannot be read or parsed ({type(exc).__name__})",
            "restore config.yaml and rerun the doctor",
        )
    ok = isinstance(payload, dict)
    return EnvironmentFinding(
        "offline.configuration",
        "offline",
        "passed" if ok else "failed",
        "config.yaml is readable" if ok else "config.yaml must contain a mapping",
        None if ok else "fix config.yaml YAML structure",
    )


def _check_pandoc() -> EnvironmentFinding:
    """Resolve Pandoc through the production parser dependency."""
    try:
        fingerprint = compiler_fingerprint()
    except Exception as exc:
        return EnvironmentFinding(
            "offline.pandoc",
            "offline",
            "failed",
            f"bundled Pandoc resolver is unavailable: {type(exc).__name__}",
            "run uv sync --locked and check pypandoc-binary",
        )
    return EnvironmentFinding(
        "offline.pandoc",
        "offline",
        "passed",
        f"Pandoc {fingerprint['pandoc']} is available through the production resolver",
    )


def _check_node_version() -> EnvironmentFinding:
    """Check Node.js output against the pinned Promptfoo engine floor."""
    executable = shutil.which("node")
    if executable is None:
        return EnvironmentFinding(
            "eval.node", "eval", "failed", "node executable was not found", "install Node.js 22.22.0+ for evaluation"
        )
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, check=False, text=True, timeout=K_PROBE_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return EnvironmentFinding(
            "eval.node",
            "eval",
            "incomplete",
            "node version probe timed out",
            "verify Node.js 22.22.0+ manually and rerun the doctor",
        )
    except OSError as exc:
        return EnvironmentFinding(
            "eval.node",
            "eval",
            "incomplete",
            f"node version probe could not run: {type(exc).__name__}",
            "verify Node.js 22.22.0+ and rerun the doctor",
        )
    if result.returncode != 0:
        return EnvironmentFinding(
            "eval.node", "eval", "failed", "node version probe failed", "install Node.js 22.22.0+ for evaluation"
        )
    version = _parse_node_version(result.stdout or result.stderr)
    if version is None:
        return EnvironmentFinding(
            "eval.node",
            "eval",
            "incomplete",
            "node version output was not parseable",
            "verify Node.js 22.22.0+ and rerun the doctor",
        )
    if version < K_APPLICATION_MIN_PROMPTFOO_NODE_VERSION:
        required = ".".join(str(part) for part in K_APPLICATION_MIN_PROMPTFOO_NODE_VERSION)
        actual = ".".join(str(part) for part in version)
        return EnvironmentFinding(
            "eval.node",
            "eval",
            "failed",
            f"Node.js {actual} is below the Promptfoo minimum {required}",
            f"install Node.js {required}+ for evaluation",
        )
    actual = ".".join(str(part) for part in version)
    return EnvironmentFinding("eval.node", "eval", "passed", f"Node.js {actual} meets the Promptfoo minimum")


def _parse_node_version(output: str) -> tuple[int, int, int] | None:
    """Parse the common vMAJOR.MINOR.PATCH Node.js version form."""
    token = output.strip().splitlines()[0] if output.strip() else ""
    if not token.startswith("v"):
        return None
    parts = token[1:].split(".")
    if len(parts) != K_DOCTOR_NODE_VERSION_PART_COUNT:
        return None
    try:
        parsed = tuple(int(part) for part in parts)
    except ValueError:
        return None
    return (parsed[0], parsed[1], parsed[2]) if len(parsed) == K_DOCTOR_NODE_VERSION_PART_COUNT else None


def _probe_executable(check_id: str, capability: Capability, name: str, action: str) -> EnvironmentFinding:
    """Run a bounded executable version probe without invoking a provider."""
    executable = shutil.which(name)
    if executable is None:
        return EnvironmentFinding(check_id, capability, "failed", f"{name} executable was not found", action)
    try:
        result = subprocess.run(
            [executable, "--version"], capture_output=True, check=False, text=True, timeout=K_PROBE_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        return EnvironmentFinding(
            check_id,
            capability,
            "incomplete",
            f"{name} version probe timed out",
            f"verify {name} manually and rerun the doctor",
        )
    except OSError as exc:
        return EnvironmentFinding(
            check_id, capability, "incomplete", f"{name} version probe could not run: {type(exc).__name__}", action
        )
    ok = result.returncode == 0
    return EnvironmentFinding(
        check_id,
        capability,
        "passed" if ok else "failed",
        f"{name} is available" if ok else f"{name} version probe failed",
        None if ok else action,
    )


def _inspect_audio_credential(path: Path | None) -> EnvironmentFinding:
    """Check credential JSON shape without exposing its contents."""
    if path is None:
        return EnvironmentFinding(
            "audio.credentials",
            "audio",
            "failed",
            f"audio service-account path is not configured ({K_AUDIO_ENV})",
            f"set {K_AUDIO_ENV} to a readable JSON file; credentials are never printed",
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return EnvironmentFinding(
            "audio.credentials",
            "audio",
            "failed",
            "configured audio service-account JSON is missing or unreadable",
            f"set {K_AUDIO_ENV} to a readable JSON file with client_email and private_key",
        )
    ok = (
        isinstance(payload, dict)
        and isinstance(payload.get("client_email"), str)
        and bool(payload["client_email"].strip())
        and isinstance(payload.get("private_key"), str)
        and bool(payload["private_key"].strip())
    )
    return EnvironmentFinding(
        "audio.credentials",
        "audio",
        "passed" if ok else "failed",
        "configured audio service-account JSON is structurally usable"
        if ok
        else "configured service-account JSON has an incomplete structure",
        None if ok else f"provide client_email and private_key in the file referenced by {K_AUDIO_ENV}",
    )


def _skip_blocked_capability(check_id: str, capability: Capability, summary: str) -> EnvironmentFinding:
    """Build a blocked capability summary."""
    return EnvironmentFinding(
        check_id, capability, "skipped", summary, "resolve offline findings, then rerun the doctor"
    )


def _normalize_capabilities(capabilities: tuple[Capability, ...]) -> tuple[Capability, ...]:
    """Return canonical capability order and reject unsupported values."""
    allowed = {"offline", "generation", "audio", "eval"}
    invalid = set(capabilities) - allowed
    if invalid:
        raise ValueError("unsupported capability: " + ", ".join(sorted(invalid)))
    if not capabilities:
        raise ValueError("at least one capability is required")
    return tuple(capability for capability in ("offline", "generation", "audio", "eval") if capability in capabilities)


def _aggregate_status(findings: tuple[EnvironmentFinding, ...]) -> ReportStatus:
    """Apply deterministic precedence to finding statuses."""
    if any(f.status == "failed" for f in findings):
        return "blocked"
    return "incomplete" if any(f.status == "incomplete" for f in findings) else "ready"


__all__ = ["EnvironmentFinding", "EnvironmentReport", "check_environment"]
