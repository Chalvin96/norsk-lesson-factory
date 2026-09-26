"""Entry points: tests for Promptfoo calibration case-file invariants."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

K_REPO_ROOT = Path(__file__).resolve().parents[2]
K_CALIBRATION_CASE_PATHS = (
    K_REPO_ROOT / "evals/promptfoo/cases/judge_calibration.yaml",
    K_REPO_ROOT / "evals/promptfoo/cases/write_judge_calibration.yaml",
)
K_WRITE_JUDGE_CASE_PATH = K_REPO_ROOT / "evals/promptfoo/cases/write_judge_calibration.yaml"
K_REQUIRED_METADATA_FIELDS = ("split", "expected_label", "dimension", "source", "label_provenance")
K_ALLOWED_SPLITS = frozenset({"dev", "holdout"})
K_ALLOWED_LABELS = frozenset({"pass", "fail"})
K_SEED_PROVENANCE = "seed_pending_human_review"
K_REVIEWED_PROVENANCE = "human_reviewed"
K_DESCRIPTION_PATTERN = re.compile(r"^(?P<dimension>[a-z_]+) (holdout )?(positive( with observation)?|negative) - \S")
K_WORD_BOUNDS_PATTERN = re.compile(r"\((\d+)\s*[–-]\s*(\d+)\s*(?:words|ord)\)")
K_WORD_TOKEN_PATTERN = re.compile(r"[^\wåøæÅØÆ]")
K_HOLDOUT_NEARDUP_THRESHOLD = 0.8
K_OBSERVATION_PREFIX = "Observation, not gating"
K_INTENTIONAL_MINIMAL_PAIR_CASE_IDS = frozenset({"wjc-regret-reply-burde-reversed-polarity"})
K_BOUNDARY_CASE_COUNTS = {
    "wjc-repair-payment-reminder-boundary-18": 18,
    "wjc-repair-payment-reminder-boundary-17": 17,
    "wjc-repair-payment-reminder-boundary-40": 40,
    "wjc-repair-payment-reminder-boundary-41": 41,
}


def test_case_metadata_given_calibration_case_files_expect_required_seed_fields():
    for path in K_CALIBRATION_CASE_PATHS:
        for case in _load_cases(path):
            metadata = case["metadata"]
            for field in K_REQUIRED_METADATA_FIELDS:
                assert metadata.get(field), f"{path.name}: missing metadata field {field}"
            assert metadata["split"] in K_ALLOWED_SPLITS, f"{path.name}: bad split"
            assert metadata["expected_label"] in K_ALLOWED_LABELS, f"{path.name}: bad label"
            assert metadata["label_provenance"] in {K_SEED_PROVENANCE, K_REVIEWED_PROVENANCE}, (
                f"{path.name}: bad provenance"
            )


def test_assertion_types_given_expected_labels_expect_paired_rubric_kinds():
    for path in K_CALIBRATION_CASE_PATHS:
        for case in _load_cases(path):
            assertion = case["assert"][0]
            expected = case["metadata"]["expected_label"]
            dimension = case["metadata"]["dimension"]
            if expected == "pass":
                assert assertion["type"] == "llm-rubric", f"{path.name}: pass needs llm-rubric"
            else:
                assert assertion["type"] == "not-llm-rubric", f"{path.name}: fail needs not-llm-rubric"
            assert assertion["metric"] == dimension, f"{path.name}: metric must equal dimension"


def test_case_rubrics_given_shared_dimension_and_source_expect_single_rubric_text():
    for path in K_CALIBRATION_CASE_PATHS:
        rubrics_by_group: dict[tuple[str, str], set[str]] = {}
        for case in _load_cases(path):
            metadata = case["metadata"]
            group = (metadata["dimension"], re.sub(r"\s*\(.*$", "", metadata["source"]))
            rubrics_by_group.setdefault(group, set()).add(_normalize(assertion_value(case)))
        for group, rubrics in rubrics_by_group.items():
            assert len(rubrics) == 1, f"{path.name}: group {group} has {len(rubrics)} rubric texts"


def test_case_descriptions_given_calibration_cases_expect_dimension_label_pattern():
    for path in K_CALIBRATION_CASE_PATHS:
        for case in _load_cases(path):
            description = case["description"]
            match = K_DESCRIPTION_PATTERN.match(description)
            assert match, f"{path.name}: description does not follow the dimension label pattern"
            assert match.group("dimension") == case["metadata"]["dimension"], (
                f"{path.name}: description dimension differs from metadata"
            )


def test_embedded_contracts_given_write_judge_cases_expect_complete_authored_contract():
    for case in _load_cases(K_WRITE_JUDGE_CASE_PATH):
        source = case["metadata"]["source"]
        relative_path, handle = _source_path_and_handle(source)
        exercise = _lesson_exercise(relative_path, handle)
        candidate = case["vars"]["candidate"]
        assert _normalize(_learner_task(candidate)) == _normalize(exercise["prompt_md"]), (
            f"embedded task differs from authored prompt_md for {handle}"
        )
        assert _normalize(_judge_instructions(candidate)) == _normalize(exercise["judge_prompt"]), (
            f"embedded judge instructions differ from authored judge_prompt for {handle}"
        )
        assert _embedded_criteria(candidate) == _authored_criteria(exercise), (
            f"embedded criteria differ from authored criteria for {handle}"
        )
        embedded_stimulus = _optional_candidate_section(candidate, "Stimulus")
        authored_stimulus = _authored_stimulus_text(exercise)
        if authored_stimulus:
            assert embedded_stimulus is not None, f"candidate is missing the authored stimulus for {handle}"
            assert _normalize(embedded_stimulus) == authored_stimulus, (
                f"embedded stimulus differs from authored dialogue stimulus for {handle}"
            )
        else:
            assert embedded_stimulus is None, f"candidate has an unexpected stimulus for {handle}"


def test_pass_responses_given_stated_word_bounds_expect_responses_within_bounds():
    for case in _load_cases(K_WRITE_JUDGE_CASE_PATH):
        candidate = case["vars"]["candidate"]
        bounds = K_WORD_BOUNDS_PATTERN.search(candidate)
        if case["metadata"]["expected_label"] != "pass" or not bounds:
            continue
        minimum, maximum = (int(value) for value in bounds.groups())
        words = _counted_response_tokens(candidate)
        assert minimum <= len(words) <= maximum, f"pass response has {len(words)} words, outside {minimum}-{maximum}"


def test_boundary_responses_given_ranked_stress_cases_expect_exact_whitespace_token_counts():
    cases_by_id = {case["metadata"]["case_id"]: case for case in _load_cases(K_WRITE_JUDGE_CASE_PATH)}

    for case_id, expected_count in K_BOUNDARY_CASE_COUNTS.items():
        candidate = cases_by_id[case_id]["vars"]["candidate"]
        assert len(_counted_response_tokens(candidate)) == expected_count


def test_holdout_responses_given_same_archetype_dev_responses_expect_no_near_duplicates():
    cases = _load_cases(K_WRITE_JUDGE_CASE_PATH)
    dev_responses: dict[str, list[frozenset[str]]] = {}
    for case in cases:
        if case["metadata"]["split"] != "dev":
            continue
        _, handle = _source_path_and_handle(case["metadata"]["source"])
        dev_responses.setdefault(handle, []).append(frozenset(_response_words(case["vars"]["candidate"])))
    for case in cases:
        if case["metadata"]["split"] != "holdout":
            continue
        if case["metadata"]["case_id"] in K_INTENTIONAL_MINIMAL_PAIR_CASE_IDS:
            continue
        _, handle = _source_path_and_handle(case["metadata"]["source"])
        response_words = frozenset(_response_words(case["vars"]["candidate"]))
        assert response_words, "holdout case has no learner response"
        for dev_words in dev_responses.get(handle, []):
            overlap = len(response_words & dev_words) / len(response_words | dev_words)
            containment = len(response_words & dev_words) / len(dev_words)
            assert overlap < K_HOLDOUT_NEARDUP_THRESHOLD and containment < K_HOLDOUT_NEARDUP_THRESHOLD, (
                f"holdout response is a near-duplicate of a {handle} dev response"
            )


def _load_cases(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def assertion_value(case: dict) -> str:
    return case["assert"][0]["value"]


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _source_path_and_handle(source: str) -> tuple[Path, str]:
    relative, parenthetical = source.split(" (", 1)
    return K_REPO_ROOT / relative, parenthetical.split(",")[0].rstrip(")")


def _lesson_exercise(relative_path: Path, handle: str) -> dict:
    exercises = yaml.safe_load(relative_path.read_text(encoding="utf-8"))
    for exercise in exercises:
        if exercise["handle"] == handle:
            return exercise
    raise AssertionError(f"no exercise {handle} in {relative_path}")


def _optional_candidate_section(candidate: str, heading: str) -> str | None:
    match = re.search(rf"{heading}:[ \n](.*?)(?=\n\n|\Z)", candidate, flags=re.DOTALL)
    return match.group(1) if match else None


def _candidate_section(candidate: str, heading: str) -> str:
    section = _optional_candidate_section(candidate, heading)
    assert section is not None, f"candidate is missing the {heading} section"
    return section


def _learner_task(candidate: str) -> str:
    return _candidate_section(candidate, "Learner task")


def _judge_instructions(candidate: str) -> str:
    return _candidate_section(candidate, "Judge instructions")


def _embedded_criteria(candidate: str) -> list[tuple[str, str, bool]]:
    section = _candidate_section(candidate, "Criteria")
    entries: list[str] = []
    for line in section.splitlines():
        if line.startswith("- "):
            entries.append(line[2:].strip())
        elif entries and line.strip():
            entries[-1] = f"{entries[-1]} {line.strip()}"
    criteria: list[tuple[str, str, bool]] = []
    for entry in entries:
        match = re.match(r"^(?P<id>[a-z0-9-]+) \((?P<kind>[^)]+)\): (?P<instruction>.+)$", entry)
        assert match, f"malformed embedded criterion: {entry}"
        criteria.append(
            (
                match.group("id"),
                _normalize(match.group("instruction")),
                match.group("kind").strip().startswith("observation"),
            )
        )
    return criteria


def _authored_criteria(exercise: dict) -> list[tuple[str, str, bool]]:
    criteria: list[tuple[str, str, bool]] = []
    for criterion in exercise["criteria"]:
        instruction = _normalize(criterion["instruction"])
        is_observation = instruction.startswith(K_OBSERVATION_PREFIX)
        if is_observation:
            instruction = re.sub(rf"^{re.escape(K_OBSERVATION_PREFIX)}\s*[—–-]\s*", "", instruction)
        criteria.append((criterion["id"], instruction, is_observation))
    return criteria


def _authored_stimulus_text(exercise: dict) -> str:
    return " ".join(
        f"{turn['speaker']}: {_normalize(turn['text_md'])}"
        for block in exercise.get("stimulus") or []
        for turn in block.get("turns", [])
    )


def _response_words(candidate: str) -> list[str]:
    response = _candidate_section(candidate, "Learner response")
    return _word_tokens(response.replace("«", " ").replace("»", " "))


def _counted_response_tokens(candidate: str) -> list[str]:
    response = _candidate_section(candidate, "Learner response")
    return [token for token in response.split() if any(character.isalnum() for character in token)]


def _word_tokens(text: str) -> list[str]:
    tokens = K_WORD_TOKEN_PATTERN.sub(" ", text).split()
    return [token for token in tokens if any(character.isalnum() for character in token)]
