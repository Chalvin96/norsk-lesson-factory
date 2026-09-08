"""Entry point: tests for the mechanical agent-convention checker."""

import sys
from pathlib import Path

import scripts.check_agent_conventions as agent_conventions
from scripts.check_agent_conventions import convention_issues


def test_convention_issues_given_valid_module_and_test_expect_no_findings():
    source = '''"""Entry point: `thing_check`."""\nK_THING_LIMIT = 1\ndef thing_check(): return K_THING_LIMIT\n'''

    result = convention_issues(Path("src/thing.py"), source)

    assert result == []


def test_main_given_renamed_file_with_existing_content_issue_expect_no_new_finding(tmp_path, monkeypatch, capsys):
    new_path = Path("tests/test_new_name.py")
    old_path = Path("tests/test_old_name.py")
    source = "def test_existing_issue(): pass\n"
    target = tmp_path / new_path
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent_conventions, "_default_base", lambda: "base")
    monkeypatch.setattr(agent_conventions, "_changed_python_paths", lambda _base: [new_path])
    monkeypatch.setattr(agent_conventions, "_renamed_paths", lambda _base: {new_path: old_path})
    monkeypatch.setattr(
        agent_conventions,
        "_git_source",
        lambda _base, path: source if path == old_path else None,
    )
    monkeypatch.setattr(sys, "argv", ["check_agent_conventions.py"])

    result = agent_conventions.main()

    assert result == 0
    assert capsys.readouterr().out == ""


def test_main_given_rename_into_nested_service_expect_new_path_finding(tmp_path, monkeypatch, capsys):
    new_path = Path("src/lesson_builder/domain/lesson/services/group/example.py")
    old_path = Path("src/lesson_builder/domain/lesson/services/example.py")
    source = '''"""Entry point: `check_example`."""\ndef check_example(): pass\n'''
    target = tmp_path / new_path
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent_conventions, "_default_base", lambda: "base")
    monkeypatch.setattr(agent_conventions, "_changed_python_paths", lambda _base: [new_path])
    monkeypatch.setattr(agent_conventions, "_renamed_paths", lambda _base: {new_path: old_path})
    monkeypatch.setattr(
        agent_conventions,
        "_git_source",
        lambda _base, path: source if path == old_path else None,
    )
    monkeypatch.setattr(sys, "argv", ["check_agent_conventions.py"])

    result = agent_conventions.main()

    assert result == 1
    assert "nested-service-package" in capsys.readouterr().out


def test_convention_issues_given_invalid_test_name_expect_test_name_finding():
    source = "def test_thing(): pass\n"

    result = convention_issues(Path("tests/test_thing.py"), source)

    assert [(issue.rule, issue.message) for issue in result] == [("test-name", "test_thing")]


def test_convention_issues_given_private_before_public_expect_order_finding():
    source = '''"""Entry point: `thing_check`."""\ndef _helper(): pass\ndef thing_check(): pass\n'''

    result = convention_issues(Path("src/thing.py"), source)

    assert [(issue.rule, issue.message) for issue in result] == [("public-before-private", "thing_check")]


def test_convention_issues_given_private_support_type_before_public_contract_expect_no_findings():
    source = '''"""Not a check itself — typed contracts."""\nclass _Base: pass\nclass Contract(_Base): pass\n'''

    result = convention_issues(Path("src/contracts.py"), source)

    assert result == []


def test_convention_issues_given_private_type_after_private_helper_expect_no_findings():
    source = '''"""Entry point: `_helper`."""\ndef _helper(): pass\nclass _Support: pass\n'''

    result = convention_issues(Path("src/support.py"), source)

    assert result == []


def test_convention_issues_given_unprefixed_constant_expect_constant_finding():
    source = '''"""Entry point: `thing_check`."""\nLIMIT = 1\ndef thing_check(): return LIMIT\n'''

    result = convention_issues(Path("src/thing.py"), source)

    assert [(issue.rule, issue.message) for issue in result] == [("constant-name", "LIMIT")]


def test_convention_issues_given_workflow_application_import_expect_no_findings():
    source = (
        '"""Entry point: `thing_check`."""\nfrom lesson_builder.application.operations import export_distribution\n'
    )

    result = convention_issues(Path("src/lesson_builder/workflow/example.py"), source)

    assert not [issue for issue in result if issue.rule == "import-direction"]


def test_convention_issues_given_promote_release_import_expect_no_findings():
    source = '"""Entry point: `promote_lesson_batch`."""\nfrom lesson_builder.application.operations import export_distribution\n'

    result = convention_issues(Path("src/lesson_builder/workflow/lesson_generation/promotion.py"), source)

    assert not [issue for issue in result if issue.rule == "import-direction"]


def test_convention_issues_given_relative_upward_import_expect_direction_finding():
    source = '"""Entry point: `thing_check`."""\nfrom ..workflow import example\n'

    result = convention_issues(Path("src/lesson_builder/application/example.py"), source)

    assert [(issue.rule, issue.message) for issue in result] == [
        (
            "import-direction",
            "application may not import workflow: lesson_builder.workflow",
        )
    ]


def test_convention_issues_given_relative_domain_import_expect_allowed_boundary():
    source = '"""Entry point: `thing_check`."""\nfrom ..domain import lesson\n'

    result = convention_issues(Path("src/lesson_builder/workflow/example.py"), source)

    assert not [issue for issue in result if issue.rule == "import-direction"]


def test_convention_issues_given_domain_infrastructure_import_expect_direction_finding():
    source = '"""Entry point: `thing_check`."""\nfrom lesson_builder.clients.llm.base import BaseLlmClient\n'

    result = convention_issues(Path("src/lesson_builder/domain/example.py"), source)

    assert [(issue.rule, issue.message) for issue in result] == [
        (
            "import-direction",
            "domain may not import clients: lesson_builder.clients.llm.base",
        )
    ]


def test_convention_issues_given_class_in_conftest_expect_placement_finding():
    source = "class FakeClient: pass\n"

    result = convention_issues(Path("tests/conftest.py"), source)

    assert {issue.rule for issue in result} == {"conftest-class", "test-helper-placement"}


def test_convention_issues_given_unknown_domain_context_expect_ownership_finding():
    source = '''"""Entry point: `thing_check`."""\ndef thing_check(): pass\n'''

    result = convention_issues(Path("src/lesson_builder/domain/payments/thing.py"), source)

    assert [(issue.rule, issue.message) for issue in result] == [
        (
            "unknown-domain-context",
            "payments is not an approved domain context; update the codebase guide after explicit approval",
        )
    ]


def test_convention_issues_given_nested_service_package_expect_boundary_finding():
    source = '''"""Entry point: `audit_source`."""\ndef audit_source(): pass\n'''

    result = convention_issues(
        Path("src/lesson_builder/domain/lesson/services/exercises/source_audit.py"),
        source,
    )

    assert [(issue.rule, issue.message) for issue in result] == [
        (
            "nested-service-package",
            (
                "lesson/services/exercises is not allowed; route the responsibility "
                "to models, flat services, validation, application, workflow, or infrastructure"
            ),
        )
    ]


def test_convention_issues_given_nested_compile_package_expect_boundary_finding():
    source = '''"""Entry point: `compile_source`."""\ndef compile_source(): pass\n'''

    result = convention_issues(
        Path("src/lesson_builder/domain/lesson/services/compile/source.py"),
        source,
    )

    assert [(issue.rule, issue.message) for issue in result] == [
        (
            "nested-service-package",
            (
                "lesson/services/compile is not allowed; route the responsibility "
                "to models, flat services, validation, application, workflow, or infrastructure"
            ),
        )
    ]


def test_convention_issues_given_public_model_in_service_expect_model_placement_finding():
    source = '''"""Not a check itself — audit result."""\nfrom pydantic import BaseModel\nclass AuditResult(BaseModel): pass\n'''

    result = convention_issues(
        Path("src/lesson_builder/domain/lesson/services/audit_service.py"),
        source,
    )

    assert [(issue.rule, issue.message) for issue in result] == [
        (
            "model-in-service",
            "AuditResult belongs in domain/lesson/models, not services",
        )
    ]


def test_convention_issues_given_public_dataclass_in_service_expect_model_placement_finding():
    source = '''"""Not a check itself — audit result."""\nfrom dataclasses import dataclass\n@dataclass(frozen=True)\nclass AuditResult:\n    count: int\n'''

    result = convention_issues(
        Path("src/lesson_builder/domain/lesson/services/audit_service.py"),
        source,
    )

    assert [(issue.rule, issue.message) for issue in result] == [
        (
            "model-in-service",
            "AuditResult belongs in domain/lesson/models, not services",
        )
    ]


def test_convention_issues_given_private_service_dataclass_expect_model_placement_finding():
    source = '''"""Not a check itself — private service state."""\nfrom dataclasses import dataclass\n@dataclass\nclass _AuditState:\n    count: int\n'''

    result = convention_issues(
        Path("src/lesson_builder/domain/lesson/services/audit_service.py"),
        source,
    )

    assert [(issue.rule, issue.message) for issue in result] == [
        (
            "model-in-service",
            "_AuditState belongs in domain/lesson/models, not services",
        )
    ]
