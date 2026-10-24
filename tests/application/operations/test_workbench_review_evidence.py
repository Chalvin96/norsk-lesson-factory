"""Regression evidence for actionable exercise review findings."""

from __future__ import annotations

import hashlib
import json
import shutil
from _thread import RLock
from collections.abc import Callable
from pathlib import Path

import pytest

from lesson_builder.application.operations import workbench_review_calls
from lesson_builder.application.operations.workbench_chat import build_workbench_finding_fix_request
from lesson_builder.application.operations.workbench_editing import WorkbenchSource
from lesson_builder.application.operations.workbench_editing import read_workbench_source
from lesson_builder.application.operations.workbench_proposals import digest_workbench_source
from lesson_builder.application.operations.workbench_records import ReviewFinding
from lesson_builder.application.operations.workbench_records import ReviewLog
from lesson_builder.application.operations.workbench_review import ReviewLedger
from lesson_builder.application.operations.workbench_review import block_content_hash
from lesson_builder.application.operations.workbench_review import resolve_workbench_findings
from lesson_builder.application.operations.workbench_review_calls import map_review_finding
from lesson_builder.application.operations.workbench_review_calls import run_workbench_review_calls
from lesson_builder.application.operations.workbench_review_evidence import populate_exercise_finding
from lesson_builder.application.operations.workbench_settings_operations import default_workbench_settings
from lesson_builder.application.operations.workbench_state import build_workbench_lesson_state
from lesson_builder.clients.cancellation import CancellationToken
from lesson_builder.formats.workbench_source import SourceBlock
from lesson_builder.formats.workbench_source import split_exercise_source
from lesson_builder.workspace.workbench_store import read_workbench_record
from lesson_builder.workspace.workbench_store import write_workbench_record
from tests.application.factories import ReviewRunFactory
from tests.clients.llm.fakes import FakeLlmClient
from tests.clients.llm.fakes import fake_job_runner


@pytest.mark.parametrize(
    "code",
    [
        "answer_review.semantic_issues",
        "answer_review.ambiguous",
        "open_semantics.semantic_issues",
        "attempt_surface.ambiguous",
        "open_rubrics.ambiguous",
    ],
)
def test_exercise_projection_given_nested_issues_expect_labeled_reasons_and_evidence(
    code: str, project_report: ProjectReport
) -> None:
    name, category = code.split(".")
    raw = {
        "id": "keep_present",
        "issues": [
            {
                "category": "supplied_answer_retrieval",
                "reason": "The source supplies the answer.",
                "evidence": "Jeg jobber her.",
                "quote_english": "I work here.",
                "suggested_change": "Remove the supplied form.",
            },
            {"category": "ambiguous_target", "reason": "Two forms fit.", "evidence": "Vi ___ her."},
        ],
    }
    blocks = split_exercise_source('- id: keep_present\n  prompt: "Vi ___ her."\n')

    findings = project_report(blocks, name, {category: [raw]})

    assert len(findings) == 1
    finding = findings[0]
    assert (
        finding.message
        == "Supplied answer retrieval: The source supplies the answer.\nAmbiguous target: Two forms fit."
    )
    assert finding.explanation == finding.message
    assert finding.excerpt == "Jeg jobber her.\nVi ___ her."
    assert finding.quote_english == "I work here."
    assert finding.suggested_change == "Remove the supplied form."
    assert json.loads(finding.details) == raw
    assert finding.key == "keep_present"


def test_exercise_excerpt_given_no_evidence_expect_short_reason_quote(project_report: ProjectReport) -> None:
    blocks = split_exercise_source('- id: keep_present\n  prompt: "Vi ___ her."\n  answer: jobber\n')

    findings = project_report(
        blocks, "attempt_surface", {"ambiguous": [{"id": "keep_present", "reason": 'Two forms fit "Vi ___ her.".'}]}
    )

    assert findings[0].excerpt == "Vi ___ her."


def test_exercise_excerpt_given_no_quote_expect_no_serialized_exercise(project_report: ProjectReport) -> None:
    blocks = split_exercise_source('- id: keep_present\n  prompt: "Vi ___ her."\n  answer: jobber\n')

    findings = project_report(
        blocks, "attempt_surface", {"ambiguous": [{"id": "keep_present", "reason": "Two forms fit."}]}
    )

    assert findings[0].excerpt == ""
    assert not findings[0].citation_exact


@pytest.mark.parametrize("details", ['{"issues": null}', "not json", "[]"])
def test_legacy_finding_given_invalid_details_expect_readable_fallback(tmp_path: Path, details: str) -> None:
    _assert_legacy_prompt(tmp_path, details, "The reviewer flagged a possible exercise issue.", "")


def test_fix_prompt_given_legacy_nested_finding_expect_reviewer_reason_and_evidence(tmp_path: Path) -> None:
    details = json.dumps(
        {
            "issues": [
                {
                    "category": "supplied_answer_retrieval",
                    "reason": "The source supplies the answer.",
                    "evidence": "Jeg jobber her.",
                    "suggested_change": "Remove the supplied form.",
                    "quote_english": "I work here.",
                }
            ]
        }
    )
    _assert_legacy_prompt(
        tmp_path, details, "Supplied answer retrieval: The source supplies the answer.", "Jeg jobber her."
    )


def _assert_legacy_prompt(tmp_path: Path, details: str, message: str, excerpt: str) -> None:
    lesson_id = "greet_and_introduce_yourself"
    shutil.copytree(
        Path(__file__).parents[3] / "content" / "lessons" / lesson_id, tmp_path / "content" / "lessons" / lesson_id
    )
    source = read_workbench_source(tmp_path, lesson_id)
    block = next(block for block in source.blocks if block.kind == "exercise")
    finding = ReviewFinding(
        finding_id="old-finding",
        key=block.key,
        criterion="answer_review.semantic_issues",
        excerpt=block.source.strip(),
        explanation="The reviewer flagged a possible exercise issue.",
        message="The reviewer flagged a possible exercise issue.",
        severity="major",
        advisory=True,
        details=details,
    )
    run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        findings=[finding],
        block_hashes={block.key: block_content_hash(block.source)},
    )
    write_workbench_record(tmp_path, "reviews", lesson_id, ReviewLog(runs=[run]))

    prompt, chips = build_workbench_finding_fix_request(tmp_path, lesson_id, "old-finding", RLock())
    state = build_workbench_lesson_state(tmp_path, lesson_id, source, (), False)

    assert message in prompt
    assert message in chips[0].detail
    assert state.findings[0].message == message
    assert state.findings[0].excerpt == excerpt
    assert block.source.strip() not in prompt
    if excerpt:
        assert f"Quoted source: {excerpt}" in prompt
        assert "English gloss: I work here." in prompt
        assert "Suggested change: Remove the supplied form." in prompt


def test_exercise_projection_given_flat_issue_expect_category_and_reason(project_report: ProjectReport) -> None:
    blocks = split_exercise_source('- id: keep_present\n  prompt: "Vi ___ her."\n')

    findings = project_report(
        blocks,
        "attempt_surface",
        {
            "ambiguous": [
                {
                    "id": "keep_present",
                    "category": "ambiguous_target",
                    "reason": "Two forms fit.",
                    "evidence": "Vi ___ her.",
                }
            ]
        },
    )

    assert findings[0].message == "Ambiguous target: Two forms fit."


def test_exercise_excerpt_given_reason_quotes_entire_block_expect_excerpt_omitted(
    project_report: ProjectReport,
) -> None:
    serialized = "- {id: keep_present, prompt: Hei}"
    blocks = split_exercise_source(serialized)

    findings = project_report(
        blocks,
        "attempt_surface",
        {"ambiguous": [{"id": "keep_present", "reason": f'Fix "{serialized}".'}]},
    )

    assert findings[0].excerpt == ""


def test_exercise_excerpt_given_long_reason_quote_without_evidence_expect_excerpt_omitted(
    project_report: ProjectReport,
) -> None:
    quote = "Hei " * 80
    blocks = split_exercise_source(f'- id: keep_present\n  prompt: "{quote}"\n  answer: hei\n')

    findings = project_report(
        blocks,
        "attempt_surface",
        {"ambiguous": [{"id": "keep_present", "reason": f'Fix "{quote}".'}]},
    )

    assert findings[0].excerpt == ""


def test_resolution_given_enriched_rereview_of_legacy_finding_expect_owner_decision_preserved(
    tmp_path: Path, project_report: ProjectReport
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    shutil.copytree(
        Path(__file__).parents[3] / "content" / "lessons" / lesson_id, tmp_path / "content" / "lessons" / lesson_id
    )
    source = read_workbench_source(tmp_path, lesson_id)
    block = next(block for block in source.blocks if block.kind == "exercise")
    reason = 'Two forms fit "Choose what Mina is asking.".'
    issues = {"id": block.key, "issues": [{"category": "ambiguous_target", "reason": reason}]}
    legacy = map_review_finding(
        (block,),
        "attempt_surface.ambiguous",
        "",
        "The reviewer flagged a possible exercise issue.",
        unit=block.key,
        severity="P1",
        details=json.dumps(issues, ensure_ascii=False, sort_keys=True),
    )
    legacy.status = "resolved"
    legacy.resolution_note = "keep as is"
    run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        findings=[legacy],
        block_hashes={block.key: block_content_hash(block.source)},
    )
    re_review = ReviewRunFactory(
        digest=run.digest,
        plan_sha256=run.plan_sha256,
        findings=[project_report((block,), "attempt_surface", {"ambiguous": [issues]})[0]],
        block_hashes=dict(run.block_hashes),
    )

    ReviewLedger(source, ReviewLog(runs=[run])).record(re_review, {block.key})

    assert re_review.findings[0].status == "resolved"
    assert re_review.findings[0].previously_resolved


ProjectReport = Callable[[tuple[SourceBlock, ...], str, dict[str, list[dict[str, object]]]], list[ReviewFinding]]


@pytest.fixture
def project_report(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ProjectReport:
    monkeypatch.setattr(
        workbench_review_calls,
        "workbench_runner_for_role",
        lambda *_: fake_job_runner(FakeLlmClient.responding('{"passed": true, "summary": "Aligned.", "issues": []}')),
    )

    def review(
        blocks: tuple[SourceBlock, ...], name: str, report: dict[str, list[dict[str, object]]]
    ) -> list[ReviewFinding]:
        def reported_issues(*_args: object, **_kwargs: object) -> dict[str, list[dict[str, object]]]:
            return report

        def no_issues(*_args: object, **_kwargs: object) -> dict[str, list[dict[str, object]]]:
            return {}

        operations = {
            "answer_review": "verify_exercises",
            "attempt_surface": "verify_attempt_surface",
            "open_rubrics": "verify_open_rubrics",
            "open_semantics": "verify_open_semantics",
            "standalone": "review_standalone_exercises",
        }
        for surface, operation in operations.items():
            monkeypatch.setattr(workbench_review_calls, operation, reported_issues if surface == name else no_issues)
        source = WorkbenchSource({}, blocks, False, None, False, None)
        lesson = {"elements": [{"element_kind": "exercise", "id": block.key} for block in blocks]}
        findings, _, _, _ = run_workbench_review_calls(
            tmp_path, source, lesson, blocks, lesson, default_workbench_settings(tmp_path), "", CancellationToken()
        )
        return findings

    return review


@pytest.mark.parametrize("long_quote", [False, True], ids=["enriched-evidence", "whole-block-citation"])
def test_resolution_given_legacy_citation_enrichment_expect_owner_decision_preserved(
    tmp_path: Path, long_quote: bool
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    shutil.copytree(
        Path(__file__).parents[3] / "content" / "lessons" / lesson_id, tmp_path / "content" / "lessons" / lesson_id
    )
    source = read_workbench_source(tmp_path, lesson_id)
    block = next(block for block in source.blocks if block.kind == "exercise")
    quote = block.source.strip() if long_quote else "Choose what Mina is asking."
    issue = {
        "id": block.key,
        "issues": [{"category": "ambiguous_target", "reason": "Two forms fit.", "evidence": "Hei."}],
    }
    legacy = map_review_finding(
        (block,),
        "attempt_surface.ambiguous",
        quote,
        "The reviewer flagged a possible exercise issue.",
        unit=block.key,
        details=json.dumps(issue),
    )
    run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        findings=[legacy],
        block_hashes={block.key: block_content_hash(block.source)},
    )
    legacy.cited_excerpt = None
    legacy.status = "resolved"
    legacy.resolution_note = "keep as is"
    write_workbench_record(tmp_path, "reviews", lesson_id, ReviewLog(runs=[run]))
    log = read_workbench_record(tmp_path, "reviews", lesson_id, ReviewLog)
    repeated = map_review_finding(
        (block,), legacy.criterion, quote, legacy.explanation, unit=block.key, details=legacy.details
    )
    repeated = populate_exercise_finding(repeated, (block,), from_report=True)
    re_review = ReviewRunFactory(
        digest=run.digest, plan_sha256=run.plan_sha256, findings=[repeated], block_hashes=run.block_hashes
    )

    ReviewLedger(source, log).record(re_review, {block.key})

    assert repeated.finding_id == legacy.finding_id
    assert repeated.excerpt != quote
    assert repeated.status == "resolved"
    assert repeated.resolution_note == "keep as is"
    assert repeated.previously_resolved


@pytest.mark.parametrize("change", ["same", "reordered", "reworded", "whitespace", "added", "category", "evidence"])
def test_resolution_given_grouped_issue_rereview_expect_decision_bound_to_issue_set(
    tmp_path: Path, change: str, project_report: ProjectReport
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    shutil.copytree(
        Path(__file__).parents[3] / "content" / "lessons" / lesson_id, tmp_path / "content" / "lessons" / lesson_id
    )
    source = read_workbench_source(tmp_path, lesson_id)
    block = next(block for block in source.blocks if block.kind == "exercise")
    issues = [
        {"category": "ambiguous_target", "reason": "Two forms fit.", "evidence": "Hei."},
        {"category": "answer_leak", "reason": "The answer is supplied.", "evidence": "Hei."},
    ]
    first = project_report((block,), "attempt_surface", {"ambiguous": [{"id": block.key, "issues": issues}]})[0]
    first.status = "resolved"
    first.resolution_note = "keep as is"
    run = ReviewRunFactory(findings=[first])
    new_issues = {
        "same": issues,
        "reordered": list(reversed(issues)),
        "added": issues + [{"category": "missing_context", "reason": "Context missing.", "evidence": "Hei."}],
        "reworded": [
            {**issue, "reason": "Rephrased reason.", "suggested_change": "Another suggestion.", "quote_english": "Hi."}
            for issue in issues
        ],
        "whitespace": [{**issue, "evidence": "  Hei. \n"} for issue in issues],
        "category": [{**issues[0], "category": "missing_context"}, issues[1]],
        "evidence": [{**issues[0], "evidence": "Hallo."}, issues[1]],
    }[change]
    repeated = project_report((block,), "attempt_surface", {"ambiguous": [{"id": block.key, "issues": new_issues}]})[0]
    re_review = ReviewRunFactory(findings=[repeated])

    ReviewLedger(source, ReviewLog(runs=[run])).record(re_review, {block.key})

    assert repeated.finding_id == first.finding_id
    assert repeated.status == ("resolved" if change in ("same", "reordered", "reworded", "whitespace") else "open")
    assert repeated.previously_resolved == (change in ("same", "reordered", "reworded", "whitespace"))


def test_resolution_given_distinct_omitted_long_quotes_expect_only_selected_finding_resolved(
    tmp_path: Path, project_report: ProjectReport
) -> None:
    lesson_id = "greet_and_introduce_yourself"
    shutil.copytree(
        Path(__file__).parents[3] / "content" / "lessons" / lesson_id, tmp_path / "content" / "lessons" / lesson_id
    )
    exercise_path = tmp_path / "content" / "lessons" / lesson_id / "exercises.yaml"
    quotes = ["Hei " * 80, "Hallo " * 80]
    exercise_path.write_text(f'- id: keep_present\n  prompt: "{quotes[0]}"\n  answer: "{quotes[1]}"\n')
    source = read_workbench_source(tmp_path, lesson_id)
    block = next(block for block in source.blocks if block.kind == "exercise")
    findings = project_report(
        (block,), "attempt_surface", {"ambiguous": [{"id": block.key, "reason": f'Fix "{quote}".'} for quote in quotes]}
    )
    run = ReviewRunFactory(
        digest=digest_workbench_source(source),
        plan_sha256=hashlib.sha256(source.files["plan.md"].encode()).hexdigest(),
        findings=findings,
        block_hashes={block.key: block_content_hash(block.source)},
    )
    write_workbench_record(tmp_path, "reviews", lesson_id, ReviewLog(runs=[run]))
    resolve_workbench_findings(tmp_path, lesson_id, [findings[0].finding_id], "keep first", reason="intended")
    log = read_workbench_record(tmp_path, "reviews", lesson_id, ReviewLog)
    re_review = ReviewRunFactory(findings=findings)

    ReviewLedger(source, log).record(re_review, {block.key})

    assert findings[0].excerpt == findings[1].excerpt == ""
    assert findings[0].finding_id != findings[1].finding_id
    assert findings[0].status == "resolved"
    assert findings[1].status == "open"
