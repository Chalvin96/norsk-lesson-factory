"""Entry point: tests for the write-judge calibration bank export."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest
import yaml

from scripts.export_write_judge_calibration import K_ALLOWED_ERROR_CLASSES
from scripts.export_write_judge_calibration import K_CALIBRATION_SCHEMA_VERSION
from scripts.export_write_judge_calibration import main
from scripts.export_write_judge_calibration import project_calibration_bank

K_REPO_ROOT = Path(__file__).resolve().parents[2]
K_WRITE_JUDGE_CASE_PATH = K_REPO_ROOT / "evals/promptfoo/cases/write_judge_calibration.yaml"


def test_write_judge_metadata_given_case_bank_expect_unique_ids_and_allowed_error_classes():
    cases = _bank_cases()

    case_ids = [case["metadata"]["case_id"] for case in cases]

    assert len(case_ids) == len(set(case_ids))
    for case in cases:
        metadata = case["metadata"]
        assert metadata["archetype"]
        assert metadata["error_class"] in K_ALLOWED_ERROR_CLASSES


def test_projection_given_pending_label_case_expect_fail_closed_refusal(tmp_path):
    bank_path = _bank_with_metadata_change(
        tmp_path,
        "wjc-workshop-email-skulle-report-valid-paraphrase",
        lambda metadata: metadata.update(label_provenance="seed_pending_human_review"),
    )

    with pytest.raises(ValueError, match="seed_pending_human_review"):
        project_calibration_bank(bank_path, allow_seed_labels=False)


def test_projection_given_reviewed_labels_expect_approvable_and_separated_splits():
    payload = project_calibration_bank(K_WRITE_JUDGE_CASE_PATH, allow_seed_labels=False)

    assert payload["schema_version"] == K_CALIBRATION_SCHEMA_VERSION
    assert payload["label_state"]["approvable"] is True
    assert payload["label_state"]["readiness"] == "human_reviewed"
    assert payload["label_state"]["pending_seed_case_ids"] == []
    assert payload["label_state"]["provenance_counts"] == {"human_reviewed": 28}
    anchors = payload["dev_anchors"]
    holdouts = payload["holdout_cases"]
    assert anchors and holdouts
    assert all(anchor["split"] == "dev" for anchor in anchors)
    assert all(case["split"] == "holdout" for case in holdouts)
    anchor_responses = {anchor["response"] for anchor in anchors}
    assert all(case["response"] not in anchor_responses for case in holdouts)


def test_projection_given_holdout_cases_expect_anchors_from_same_archetype_dev_only():
    payload = project_calibration_bank(K_WRITE_JUDGE_CASE_PATH, allow_seed_labels=True)
    anchor_by_id = {anchor["case_id"]: anchor for anchor in payload["dev_anchors"]}

    for case in payload["holdout_cases"]:
        assert case["anchor_case_ids"]
        for anchor_id in case["anchor_case_ids"]:
            assert anchor_id in anchor_by_id
            assert anchor_by_id[anchor_id]["archetype"] == case["archetype"]


def test_projection_given_bank_file_expect_hash_and_exact_structured_fields():
    payload = project_calibration_bank(K_WRITE_JUDGE_CASE_PATH, allow_seed_labels=True)

    assert payload["bank_sha256"] == hashlib.sha256(K_WRITE_JUDGE_CASE_PATH.read_bytes()).hexdigest()
    anchor = next(
        record
        for record in payload["dev_anchors"]
        if record["case_id"] == "wjc-workshop-email-skulle-report-valid-paraphrase"
    )
    assert anchor["task"].startswith("I går ledet du et verksted for kollegaen Eva.")
    assert anchor["judge_instructions"].startswith("Check the email against criteria room-arrangement")
    assert anchor["stimulus"] is None
    assert anchor["stressors"] == []
    assert anchor["primary_failed_criterion"] is None
    assert [criterion["id"] for criterion in anchor["criteria"]] == [
        "room-arrangement",
        "projector-arrangement",
        "email-purpose",
        "workshop-word-bounds",
        "skulle-ha-forms",
    ]
    assert all(criterion["gating"] is True for criterion in anchor["criteria"])
    assert anchor["response"].startswith("«Hei Eva! Rom 204 skulle ha vært bestilt")


def test_projection_given_stimulus_case_expect_stimulus_text_and_observation_criterion():
    payload = project_calibration_bank(K_WRITE_JUDGE_CASE_PATH, allow_seed_labels=True)
    case = next(record for record in payload["holdout_cases"] if record["archetype"] == "repair_payment_reminder")

    assert case["stimulus"].startswith("Ingrid: «Så hyggelig")
    marker = next(criterion for criterion in case["criteria"] if criterion["id"] == "marker-observation")
    assert marker["gating"] is False


def test_projection_given_positive_prompt_injection_stress_expect_no_failure_metadata():
    payload = project_calibration_bank(K_WRITE_JUDGE_CASE_PATH, allow_seed_labels=True)
    case = next(
        record
        for record in payload["holdout_cases"]
        if record["case_id"] == "wjc-repair-payment-reminder-prompt-injection"
    )

    assert case["stressors"] == ["prompt_injection"]
    assert case["primary_failed_criterion"] is None


def test_projection_given_negative_without_primary_failed_criterion_expect_refusal(tmp_path):
    bank_path = _bank_with_metadata_change(
        tmp_path,
        "wjc-workshop-email-skulle-repetition-gaming",
        lambda metadata: metadata.pop("primary_failed_criterion"),
    )

    with pytest.raises(TypeError, match="negative calibration case requires primary_failed_criterion"):
        project_calibration_bank(bank_path, allow_seed_labels=True)


def test_projection_given_positive_with_primary_failed_criterion_expect_refusal(tmp_path):
    bank_path = _bank_with_metadata_change(
        tmp_path,
        "wjc-workshop-email-skulle-report-valid-paraphrase",
        lambda metadata: metadata.update(primary_failed_criterion="two-arrangements-reported"),
    )

    with pytest.raises(TypeError, match="positive calibration case forbids primary_failed_criterion"):
        project_calibration_bank(bank_path, allow_seed_labels=True)


def test_projection_given_primary_failed_observation_criterion_expect_refusal(tmp_path):
    bank_path = _bank_with_metadata_change(
        tmp_path,
        "wjc-repair-payment-reminder-repeats-misunderstanding",
        lambda metadata: metadata.update(primary_failed_criterion="marker-observation"),
    )

    with pytest.raises(ValueError, match="primary_failed_criterion must identify a gating criterion"):
        project_calibration_bank(bank_path, allow_seed_labels=True)


def test_main_given_pending_label_case_expect_refusal_without_writing_output(tmp_path, capsys):
    output_path = tmp_path / "bank.json"
    bank_path = _bank_with_metadata_change(
        tmp_path,
        "wjc-regret-reply-burde-both-judgments",
        lambda metadata: metadata.update(label_provenance="seed_pending_human_review"),
    )

    result = main(["--bank", str(bank_path), "--output", str(output_path)])

    captured = capsys.readouterr()
    assert result == 1
    assert not output_path.exists()
    assert "seed_pending_human_review" in captured.err


def test_main_given_reviewed_labels_expect_written_json_matches_stdout(tmp_path, capsys):
    output_path = tmp_path / "bank.json"

    result = main(
        [
            "--bank",
            str(K_WRITE_JUDGE_CASE_PATH),
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert result == 0
    written = json.loads(output_path.read_text(encoding="utf-8"))
    printed = json.loads(captured.out)
    assert written == printed
    assert written["label_state"]["approvable"] is True


def _bank_cases() -> list[dict]:
    return yaml.safe_load(K_WRITE_JUDGE_CASE_PATH.read_text(encoding="utf-8"))


def _bank_with_metadata_change(tmp_path: Path, case_id: str, change: Callable[[dict], object]) -> Path:
    cases = _bank_cases()
    case = next(item for item in cases if item["metadata"]["case_id"] == case_id)
    change(case["metadata"])
    bank_path = tmp_path / "write_judge_calibration.yaml"
    bank_path.write_text(yaml.safe_dump(cases, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return bank_path
