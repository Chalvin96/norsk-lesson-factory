#!/usr/bin/env python3
"""Entry point: `main` prints the report-only dead-code audit; `run_audit` builds it.

Ruff stays the deterministic local gate (F401/F841, undefined names). This
audit only discovers removal *candidates* with Vulture plus a first-party
import graph, and groups them as `unreachable module`, `test-only`,
`framework false positive`, or `unused symbol`. It never edits or deletes
code, always exits 0, and writes no manifest into the authoring surface.
Every candidate still needs a manual exact-name `rg -w <name>` cross-check
before any removal.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from lesson_builder.workspace.paths import get_workspace_root

# Roots of the production graph: the package console script, the two gate
# scripts, and this audit itself (invoked by `npm run dead-code:audit`).
K_PRODUCTION_ROOT_MODULES = (
    "lesson_builder.cli",
    "scripts.check_agent_conventions",
    "scripts.okf_validate",
    "scripts.dead_code_audit",
)
K_PRODUCTION_SCAN_PATHS = ("src", "scripts")
K_TEST_SCAN_PATHS = ("tests",)
# Non-Python surfaces where an exact identifier match means "referenced, keep
# until verified": configuration, packaging, CI, and durable documentation.
K_CONFIG_REFERENCE_GLOBS = ("config.yaml", "pyproject.toml", "package.json", ".github/**/*.yml")
K_DOC_REFERENCE_GLOBS = ("README.md", "AGENTS.md", "docs/**/*", "knowledge/**/*.md", "src/**/README.md")
K_VULTURE_DEFAULT_CONFIDENCE = 60
K_VULTURE_LINE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+): (?P<kind>unused [a-z ]+) '(?P<name>.+)' \((?P<confidence>\d+)% confidence\)$"
)
K_IDENTIFIER_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

K_GROUP_UNREACHABLE_MODULE = "unreachable module"
K_GROUP_TEST_ONLY = "test-only"
K_GROUP_FRAMEWORK = "framework false positive"
K_GROUP_UNUSED_SYMBOL = "unused symbol"
K_GROUP_ORDER = (K_GROUP_UNREACHABLE_MODULE, K_GROUP_TEST_ONLY, K_GROUP_FRAMEWORK, K_GROUP_UNUSED_SYMBOL)

# Decorated definitions are consumed by their framework, not by call sites.
K_FRAMEWORK_DECORATORS = frozenset(
    {
        "abstractmethod",
        "cached_property",
        "computed_field",
        "field_serializer",
        "field_validator",
        "model_serializer",
        "model_validator",
        "overload",
        "property",
        "validate_call",
    }
)
# Pydantic model configuration attribute assigned in class bodies.
K_PYDANTIC_MODEL_CONFIG_NAME = "model_config"
# Parameters whose names are fixed by Python or by interface signatures; Vulture
# cannot see that Protocol/classmethod surfaces consume them implicitly.
K_IMPLICIT_PARAMETER_NAMES = frozenset({"cls", "self", "args", "kwargs"})
K_PYDANTIC_MODULE_NAMES = frozenset({"pydantic"})
K_PYDANTIC_BASE_NAMES = frozenset({"BaseModel"})
K_DATACLASS_MODULE_NAMES = frozenset({"dataclasses"})
K_DATACLASS_DECORATOR_NAMES = frozenset({"dataclass"})
K_TYPED_DICT_MODULE_NAMES = frozenset({"typing", "typing_extensions"})
K_TYPED_DICT_BASE_NAMES = frozenset({"TypedDict"})
K_PROTOCOL_BASE_NAMES = frozenset({"Protocol"})

type VultureRunner = Callable[[tuple[str, ...], int], str]


@dataclass(frozen=True)
class SymbolFinding:
    """Not a check itself — one Vulture candidate with its audit group."""

    path: str
    line: int
    kind: str
    name: str
    confidence: int
    group: str
    reason: str


@dataclass(frozen=True)
class ModuleFinding:
    """Not a check itself — one first-party module not reachable from production roots."""

    path: str
    group: str
    reason: str


@dataclass(frozen=True)
class AuditReport:
    """Not a check itself — the grouped result returned by `run_audit`."""

    roots: tuple[str, ...]
    modules: tuple[ModuleFinding, ...]
    symbols: tuple[SymbolFinding, ...]

    def counts(self) -> dict[str, int]:
        """Return candidate counts per group, including module findings."""
        grouped: dict[str, int] = dict.fromkeys(K_GROUP_ORDER, 0)
        for module in self.modules:
            grouped[module.group] += 1
        for symbol in self.symbols:
            grouped[symbol.group] += 1
        return grouped


def run_audit(
    repo_root: Path,
    *,
    production_roots: tuple[str, ...] = K_PRODUCTION_ROOT_MODULES,
    production_paths: tuple[str, ...] = K_PRODUCTION_SCAN_PATHS,
    test_paths: tuple[str, ...] = K_TEST_SCAN_PATHS,
    reference_globs: tuple[str, ...] = (*K_CONFIG_REFERENCE_GLOBS, *K_DOC_REFERENCE_GLOBS),
    min_confidence: int = K_VULTURE_DEFAULT_CONFIDENCE,
    run_vulture: VultureRunner | None = None,
) -> AuditReport:
    """Group dead-code candidates for one repository without modifying anything."""
    vulture = run_vulture or _default_vulture_runner(repo_root)
    production_files = _collect_python_files(repo_root, production_paths)
    test_files = _collect_python_files(repo_root, test_paths)
    modules, edges = _module_graph(repo_root, {**production_files, **test_files})

    reachable_production = _reachable(edges, production_roots)
    test_roots = tuple(module for module in test_files.values() if module in modules)
    reachable_tests = _reachable(edges, test_roots)
    config_tokens, doc_tokens, doc_text = _reference_tokens(repo_root, reference_globs)

    module_findings_list: list[ModuleFinding] = []
    for path, module in sorted(production_files.items()):
        if module in reachable_production or module in production_roots:
            continue
        annotation = _module_annotation(path, module, config_tokens, doc_tokens, doc_text)
        if module in reachable_tests:
            group = K_GROUP_TEST_ONLY
            module_reason = "module is not reachable from production roots (tests import it)"
        else:
            group = K_GROUP_UNREACHABLE_MODULE
            module_reason = "module is not reachable from production roots"
        if annotation is not None:
            module_reason += f"; annotation: {annotation}"
        module_findings_list.append(ModuleFinding(path=path, group=group, reason=module_reason))
    module_findings = tuple(module_findings_list)
    flagged_module_paths = {finding.path for finding in module_findings}

    production_findings = parse_vulture_output(vulture(production_paths, min_confidence))
    test_aware_findings = {
        (finding.path, finding.kind, finding.name)
        for finding in parse_vulture_output(vulture((*production_paths, *test_paths), min_confidence))
    }
    framework_reasons = _framework_name_reasons(repo_root, {**production_files, **test_files})

    symbol_findings: list[SymbolFinding] = []
    for finding in production_findings:
        if finding.path in flagged_module_paths:
            continue  # Subsumed: removing the module removes its symbols.
        framework_reason = _framework_reason(
            finding.path,
            finding.line,
            finding.name,
            framework_reasons,
            config_tokens,
            doc_tokens,
        )
        if framework_reason is not None:
            symbol_findings.append(
                SymbolFinding(**{**finding.__dict__, "group": K_GROUP_FRAMEWORK, "reason": framework_reason})
            )
        elif (finding.path, finding.kind, finding.name) in test_aware_findings:
            symbol_findings.append(
                SymbolFinding(
                    **{**finding.__dict__, "group": K_GROUP_UNUSED_SYMBOL, "reason": "unused even with tests in scope"}
                )
            )
        else:
            symbol_findings.append(
                SymbolFinding(
                    **{**finding.__dict__, "group": K_GROUP_TEST_ONLY, "reason": "referenced only from tests"}
                )
            )
    return AuditReport(
        roots=production_roots,
        modules=module_findings,
        symbols=tuple(symbol_findings),
    )


def parse_vulture_output(output: str) -> tuple[SymbolFinding, ...]:
    """Parse Vulture's `<path>:<line>: unused <kind> '<name>' (N% confidence)` lines."""
    findings = []
    for line in output.splitlines():
        match = K_VULTURE_LINE.match(line)
        if match is None:
            continue
        findings.append(
            SymbolFinding(
                path=match["path"],
                line=int(match["line"]),
                kind=match["kind"],
                name=match["name"],
                confidence=int(match["confidence"]),
                group="",
                reason="",
            )
        )
    return tuple(findings)


def format_report(report: AuditReport) -> str:
    """Render the grouped audit as the human-readable, report-only output."""
    lines = [
        "Dead-code audit (report-only; candidates, never deletions)",
        f"Production roots: {', '.join(report.roots)}",
        "",
    ]
    counts = report.counts()
    for group in K_GROUP_ORDER:
        lines.append(f"== {group} ({counts[group]}) ==")
        module_lines = sorted(finding.path for finding in report.modules if finding.group == group)
        for path in module_lines:
            reason = next(
                finding.reason for finding in report.modules if finding.path == path and finding.group == group
            )
            lines.append(f"{path}  ({reason})")
        for finding in sorted(
            (symbol for symbol in report.symbols if symbol.group == group),
            key=lambda symbol: (symbol.path, symbol.line, symbol.name),
        ):
            lines.append(f"{finding.path}:{finding.line}: {finding.kind} '{finding.name}' ({finding.reason})")
        lines.append("")
    total = sum(counts.values())
    lines.append(f"Summary: {total} candidates across {len(counts)} groups.")
    lines.append(
        "Symbols inside flagged modules are subsumed by that module's finding. "
        "Before removing anything: cross-check the exact name with `rg -w <name>`, "
        "verify it is not a framework/dynamic surface or documented export, and "
        "require a second independent dead-code signal. One cluster at a time."
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print the grouped audit report. Always exits 0; this is not a gate."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-confidence", type=int, default=K_VULTURE_DEFAULT_CONFIDENCE)
    parser.add_argument("--repo-root", type=Path, default=get_workspace_root())
    args = parser.parse_args(argv)
    report = run_audit(repo_root=args.repo_root, min_confidence=args.min_confidence)
    print(format_report(report))
    return 0


def _default_vulture_runner(repo_root: Path) -> VultureRunner:
    """Run the pinned local Vulture via the current interpreter; no network."""

    def run(scan_paths: tuple[str, ...], min_confidence: int) -> str:
        result = subprocess.run(
            [sys.executable, "-m", "vulture", *scan_paths, "--min-confidence", str(min_confidence)],
            capture_output=True,
            text=True,
            check=False,
            cwd=repo_root,
        )
        if result.returncode not in (0, 3):  # 3 means "dead code found", the normal outcome.
            raise RuntimeError(f"vulture exited {result.returncode}: {result.stderr.strip()}")
        return result.stdout

    return run


def _collect_python_files(repo_root: Path, path_roots: tuple[str, ...]) -> dict[str, str]:
    """Map each scanned Python file to its module name, keyed by relative path."""
    files: dict[str, str] = {}
    for path_root in path_roots:
        root = repo_root / path_root
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            relative = path.relative_to(repo_root).as_posix()
            module = _module_name(relative)
            if module is not None:
                files[relative] = module
    return files


def _module_annotation(
    path: str,
    module: str,
    config_tokens: set[str],
    doc_tokens: set[str],
    doc_text: str,
) -> str | None:
    """Return a non-classifying config or documentation annotation for a module."""
    stem = module.rsplit(".", 1)[-1]
    if stem in config_tokens:
        return "module name appears in configuration or packaging"
    if _looks_like_identifier(stem) and stem in doc_tokens:
        return "module name appears in documentation"
    if module in doc_text or Path(path).as_posix() in doc_text or Path(path).name in doc_text:
        return "module file or import path appears in documentation"
    return None


def _module_name(relative_path: str) -> str | None:
    """Dotted module name for a relative path, or None for stray top-level files."""
    parts = relative_path.removesuffix(".py").split("/")
    if parts and parts[0] == "src":
        parts = parts[1:]
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


def _module_graph(repo_root: Path, files: dict[str, str]) -> tuple[dict[str, str], dict[str, set[str]]]:
    """Build the first-party import graph from AST imports in every scanned file."""
    modules = {module: path for path, module in files.items()}
    edges: dict[str, set[str]] = {module: set() for module in modules}
    for path, module in files.items():
        source = (repo_root / path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=path)
        is_package = Path(path).name == "__init__.py"
        for node in ast.walk(tree):
            for target in _import_targets(node, module, is_package):
                edges[module].update(prefix for prefix in _dotted_prefixes(target) if prefix in modules)
    return modules, edges


def _import_targets(node: ast.AST, module: str, is_package: bool) -> set[str]:
    """Resolve one import statement to candidate first-party module names."""
    if isinstance(node, ast.Import):
        return {alias.name for alias in node.names}
    if not isinstance(node, ast.ImportFrom):
        return set()
    if node.level == 0:
        base_parts = node.module.split(".") if node.module else []
    else:
        package = module if is_package else module.rsplit(".", 1)[0]
        for _ in range(node.level - 1):
            if "." not in package:
                return set()
            package = package.rsplit(".", 1)[0]
        base_parts = package.split(".") + (node.module.split(".") if node.module else [])
    targets = {".".join(base_parts)} if base_parts else set()
    targets.update(".".join([*base_parts, alias.name]) for alias in node.names)
    return targets


def _dotted_prefixes(module: str) -> list[str]:
    """All non-empty dotted prefixes, longest first (the module itself first)."""
    parts = module.split(".")
    return [".".join(parts[: index + 1]) for index in range(len(parts) - 1, -1, -1)]


def _reachable(edges: dict[str, set[str]], roots: tuple[str, ...]) -> set[str]:
    """Modules reachable from the given roots (and their package prefixes) over the graph."""
    seen: set[str] = set()
    seeds = [prefix for root in roots for prefix in _dotted_prefixes(root) if prefix in edges]
    queue = list(dict.fromkeys(seeds))
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        queue.extend(edges[current] - seen)
    return seen


def _framework_name_reasons(repo_root: Path, files: dict[str, str]) -> dict[tuple[str, int, str], str]:
    """Return location-specific reasons for framework-consumed symbols."""
    reasons: dict[tuple[str, int, str], str] = {}

    def claim(path: str, node: ast.expr | ast.stmt, name: str, reason: str) -> None:
        reasons.setdefault((path, node.lineno, name), reason)

    trees = [(path, ast.parse((repo_root / path).read_text(encoding="utf-8"), filename=path)) for path in files]
    for path, tree in trees:
        _claim_exported_definitions(path, tree, claim)
        _claim_framework_interfaces(path, tree, claim)
        _claim_framework_model_members(path, tree, claim)
    return reasons


def _claim_exported_definitions(
    path: str,
    tree: ast.Module,
    claim: Callable[[str, ast.expr | ast.stmt, str, str], None],
) -> None:
    """Claim definitions listed in the module's documented ``__all__`` surface."""
    definitions = {
        node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    for node in tree.body:
        if not (
            isinstance(node, ast.Assign)
            and any(target.id == "__all__" for target in node.targets if isinstance(target, ast.Name))
        ):
            continue
        for element in getattr(node.value, "elts", []):
            if isinstance(element, ast.Constant) and isinstance(element.value, str):
                definition = definitions.get(element.value)
                if definition is not None:
                    claim(path, definition, element.value, "listed in __all__")


def _claim_framework_interfaces(
    path: str,
    tree: ast.Module,
    claim: Callable[[str, ast.expr | ast.stmt, str, str], None],
) -> None:
    """Claim decorators, dunder hooks, and implicit interface parameters."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for decorator in node.decorator_list:
            decorator_name = _attribute_text(decorator)
            if decorator_name in K_FRAMEWORK_DECORATORS:
                claim(path, node, node.name, f"framework decorator @{decorator_name}")
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _claim_function_interface(path, node, claim)


def _claim_function_interface(
    path: str,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    claim: Callable[[str, ast.expr | ast.stmt, str, str], None],
) -> None:
    """Claim dunder hooks and conventional implicit function parameters."""
    if node.name.startswith("__") and node.name.endswith("__"):
        claim(path, node, node.name, "dunder hook")
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    if node.args.vararg is not None:
        arguments.append(node.args.vararg)
    if node.args.kwarg is not None:
        arguments.append(node.args.kwarg)
    for argument in arguments:
        if argument.arg in K_IMPLICIT_PARAMETER_NAMES:
            claim(path, node, argument.arg, "implicit interface parameter")


def _claim_framework_model_members(
    path: str,
    tree: ast.Module,
    claim: Callable[[str, ast.expr | ast.stmt, str, str], None],
) -> None:
    """Claim fields and members consumed by proven framework model classes."""
    class_kinds = _framework_class_kinds(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and class_kinds.get(node.name) is not None:
            _claim_model_members(path, node, class_kinds[node.name], claim)


def _claim_model_members(
    path: str,
    node: ast.ClassDef,
    class_kind: str,
    claim: Callable[[str, ast.expr | ast.stmt, str, str], None],
) -> None:
    """Claim declarative fields and Protocol members from one model class."""
    for member in node.body:
        if isinstance(member, ast.AnnAssign) and isinstance(member.target, ast.Name):
            claim(path, member, member.target.id, f"annotated {class_kind} field")
        if class_kind == "Pydantic model" and isinstance(member, ast.Assign):
            for target in member.targets:
                if isinstance(target, ast.Name) and target.id == K_PYDANTIC_MODEL_CONFIG_NAME:
                    claim(path, member, target.id, "Pydantic model_config")
        if class_kind == "Protocol" and isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
            claim(path, member, member.name, "Protocol member")


def _framework_class_kinds(tree: ast.AST) -> dict[str, str]:
    """Map local classes to proven Pydantic, dataclass, TypedDict, or Protocol kinds."""
    classes = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)}
    pydantic_names = _imported_framework_names(tree, K_PYDANTIC_MODULE_NAMES, K_PYDANTIC_BASE_NAMES)
    dataclass_names = _imported_framework_names(tree, K_DATACLASS_MODULE_NAMES, K_DATACLASS_DECORATOR_NAMES)
    typed_dict_names = _imported_framework_names(tree, K_TYPED_DICT_MODULE_NAMES, K_TYPED_DICT_BASE_NAMES)
    protocol_names = _imported_framework_names(tree, K_TYPED_DICT_MODULE_NAMES, K_PROTOCOL_BASE_NAMES)
    class_kinds: dict[str, str] = {}
    visiting: set[str] = set()
    for node in classes.values():
        _classify_framework_class(
            node,
            classes,
            pydantic_names,
            dataclass_names,
            typed_dict_names,
            protocol_names,
            class_kinds,
            visiting,
        )
    return class_kinds


def _classify_framework_class(
    node: ast.ClassDef,
    classes: dict[str, ast.ClassDef],
    pydantic_names: set[str],
    dataclass_names: set[str],
    typed_dict_names: set[str],
    protocol_names: set[str],
    class_kinds: dict[str, str],
    visiting: set[str],
) -> str | None:
    """Classify one class, following local inheritance without cycles."""
    if node.name in class_kinds:
        return class_kinds[node.name]
    if node.name in visiting:
        return None
    visiting.add(node.name)
    kind = _framework_class_kind_from_bases(
        node,
        classes,
        pydantic_names,
        dataclass_names,
        typed_dict_names,
        protocol_names,
        class_kinds,
        visiting,
    )
    visiting.remove(node.name)
    if kind is not None:
        class_kinds[node.name] = kind
    return kind


def _framework_class_kind_from_bases(
    node: ast.ClassDef,
    classes: dict[str, ast.ClassDef],
    pydantic_names: set[str],
    dataclass_names: set[str],
    typed_dict_names: set[str],
    protocol_names: set[str],
    class_kinds: dict[str, str],
    visiting: set[str],
) -> str | None:
    """Resolve direct framework bases or a local framework-derived parent."""
    decorator_names = {_attribute_text(dec) for dec in node.decorator_list}
    kind = "dataclass" if decorator_names & dataclass_names else None
    for base in node.bases:
        base_name = _attribute_text(base)
        if base_name in pydantic_names:
            return "Pydantic model"
        if base_name in typed_dict_names:
            return "TypedDict"
        if base_name in protocol_names:
            return "Protocol"
        parent = classes.get(base_name)
        if parent is not None:
            parent_kind = _classify_framework_class(
                parent,
                classes,
                pydantic_names,
                dataclass_names,
                typed_dict_names,
                protocol_names,
                class_kinds,
                visiting,
            )
            if parent_kind is not None:
                return parent_kind
    return kind


def _imported_framework_names(tree: ast.AST, modules: frozenset[str], members: frozenset[str]) -> set[str]:
    """Return local aliases imported from one of the named framework modules."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in modules:
            for alias in node.names:
                if alias.name in members:
                    names.add(alias.asname or alias.name)
    return names


def _reference_tokens(repo_root: Path, reference_globs: tuple[str, ...]) -> tuple[set[str], set[str], str]:
    """Exact identifier tokens from config files and documentation, plus raw doc text."""
    config_tokens: set[str] = set()
    doc_tokens: set[str] = set()
    doc_text: list[str] = []
    config_globs = set(K_CONFIG_REFERENCE_GLOBS)
    for pattern in reference_globs:
        is_config = pattern in config_globs
        bucket = config_tokens if is_config else doc_tokens
        for path in repo_root.glob(pattern):
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if not is_config:
                doc_text.append(text)
            for token in K_IDENTIFIER_TOKEN.findall(text):
                if is_config or _looks_like_identifier(token):
                    bucket.add(token)
    return config_tokens, doc_tokens, "\n".join(doc_text)


def _looks_like_identifier(token: str) -> bool:
    """Filter prose words out of documentation matches; keep Python-style names."""
    return "_" in token or any(character.isupper() for character in token)


def _attribute_text(node: ast.AST) -> str:
    """Final identifier of a Name/Attribute/Call expression, e.g. `pydantic.field_validator(...)`."""
    if isinstance(node, ast.Call):
        return _attribute_text(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _framework_reason(
    path: str,
    line: int,
    name: str,
    framework_reasons: dict[tuple[str, int, str], str],
    config_tokens: set[str],
    doc_tokens: set[str],
) -> str | None:
    """Reason a Vulture candidate is a preserved dynamic/documented surface, if any."""
    location = (path, line, name)
    if location in framework_reasons:
        return framework_reasons[location]
    if name in config_tokens:
        return "exact name appears in configuration or packaging"
    if name in doc_tokens:
        return "exact name appears in documentation"
    return None


if __name__ == "__main__":
    sys.exit(main())
