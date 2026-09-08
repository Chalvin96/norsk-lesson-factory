"""Behavior tests for the report-only dead-code audit."""

from __future__ import annotations

from pathlib import Path

from scripts.dead_code_audit import run_audit


def test_run_audit_given_unreachable_module_and_test_used_symbol_expect_grouped_candidates(tmp_path: Path):
    source_root = tmp_path / "src" / "app"
    source_root.mkdir(parents=True)
    (source_root / "__init__.py").write_text("", encoding="utf-8")
    (source_root / "main.py").write_text("from app.live import helper\n", encoding="utf-8")
    (source_root / "live.py").write_text("def helper():\n    return 1\n", encoding="utf-8")
    (source_root / "orphan.py").write_text("def orphan():\n    return 2\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("Documented modules: app.live and app.orphan.\n", encoding="utf-8")
    test_root = tmp_path / "tests"
    test_root.mkdir()
    (test_root / "test_app.py").write_text("from app.live import helper\n", encoding="utf-8")

    def fake_vulture(scan_paths: tuple[str, ...], _min_confidence: int) -> str:
        if "tests" in scan_paths:
            return ""
        return "src/app/live.py:1: unused function 'helper' (60% confidence)\n"

    report = run_audit(
        tmp_path,
        production_roots=("app.main",),
        production_paths=("src",),
        test_paths=("tests",),
        reference_globs=("README.md",),
        run_vulture=fake_vulture,
    )

    assert report.counts()["unreachable module"] == 1
    assert report.counts()["test-only"] == 1
    assert report.counts()["unused symbol"] == 0
    assert any("annotation:" in finding.reason for finding in report.modules)


def test_run_audit_given_documented_unreachable_empty_package_expect_unreachable_annotation(tmp_path: Path):
    source_root = tmp_path / "src" / "app"
    (source_root / "__init__.py").parent.mkdir(parents=True)
    (source_root / "__init__.py").write_text("", encoding="utf-8")
    (source_root / "main.py").write_text("", encoding="utf-8")
    empty_package = source_root / "empty"
    empty_package.mkdir()
    (empty_package / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "README.md").write_text("The app.empty package is documented for future use.\n", encoding="utf-8")

    report = run_audit(
        tmp_path,
        production_roots=("app.main",),
        production_paths=("src",),
        test_paths=(),
        reference_globs=("README.md",),
        run_vulture=lambda _paths, _min_confidence: "",
    )

    finding = next(item for item in report.modules if item.path.endswith("empty/__init__.py"))
    assert finding.group == "unreachable module"
    assert "annotation:" in finding.reason


def test_run_audit_given_same_name_framework_candidate_in_other_file_expect_unused_symbol(tmp_path: Path):
    source_root = tmp_path / "src" / "app"
    source_root.mkdir(parents=True)
    (source_root / "__init__.py").write_text("", encoding="utf-8")
    (source_root / "main.py").write_text(
        "from app.decorated import Decorated\nfrom app.live import same\n", encoding="utf-8"
    )
    (source_root / "decorated.py").write_text(
        "class Decorated:\n    @property\n    def same(self):\n        return 1\n", encoding="utf-8"
    )
    (source_root / "live.py").write_text("def same():\n    return 2\n", encoding="utf-8")
    candidate = "src/app/live.py:1: unused function 'same' (60% confidence)\n"

    report = run_audit(
        tmp_path,
        production_roots=("app.main",),
        production_paths=("src",),
        test_paths=(),
        reference_globs=(),
        run_vulture=lambda _paths, _min_confidence: candidate,
    )

    symbol = report.symbols[0]
    assert symbol.group == "unused symbol"
    assert symbol.reason == "unused even with tests in scope"


def test_run_audit_given_ordinary_annotated_class_candidate_expect_unused_symbol(tmp_path: Path):
    source_root = tmp_path / "src" / "app"
    source_root.mkdir(parents=True)
    (source_root / "__init__.py").write_text("", encoding="utf-8")
    (source_root / "main.py").write_text("from app.ordinary import Ordinary\n", encoding="utf-8")
    (source_root / "ordinary.py").write_text("class Ordinary:\n    value: str\n", encoding="utf-8")
    candidate = "src/app/ordinary.py:2: unused variable 'value' (60% confidence)\n"

    report = run_audit(
        tmp_path,
        production_roots=("app.main",),
        production_paths=("src",),
        test_paths=(),
        reference_globs=(),
        run_vulture=lambda _paths, _min_confidence: candidate,
    )

    symbol = report.symbols[0]
    assert symbol.group == "unused symbol"
    assert symbol.reason == "unused even with tests in scope"
