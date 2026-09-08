"""Behavior tests for deterministic exercise diagnostics."""

from lesson_builder.domain.lesson.services.exercise_diagnostics import analyze_exercise_diagnostics
from tests.domain.lesson.models.fakes import lesson_with_exercises
from tests.domain.lesson.models.fakes import valid_exercise


def test_analyze_exercise_diagnostics_given_mixed_operations_expect_response_counts():
    lesson = lesson_with_exercises(
        [
            valid_exercise(
                "choose",
                bloom_level="understand",
                payload={
                    "options": [
                        {"option_id": "a", "text": "a"},
                        {"option_id": "b", "text": "b"},
                    ],
                    "answer_id": "a",
                    "stem": None,
                },
            ),
            valid_exercise(
                "recall_fill",
                payload={
                    "audio_target": "Hei.",
                    "segments": [{"kind": "blank", "blank_id": "slot", "options": ["a", "b"], "answer_index": 0}],
                },
            ),
            valid_exercise(
                "build",
                bloom_level="apply",
                payload={
                    "tokens": [
                        {"token_id": "a", "text": "Jeg", "fixed": False},
                        {"token_id": "b", "text": "jobber", "fixed": False},
                    ],
                    "answer_order": ["a", "b"],
                },
            ),
        ]
    )

    evidence = analyze_exercise_diagnostics(lesson)

    assert evidence.total_exercises == 3
    assert evidence.response_opportunities == 3
    assert evidence.operation_counts == {"build": 1, "choose": 1, "recall_fill": 1}
    assert evidence.phase_counts == {"controlled": 1, "notice": 1, "retrieve": 1}
    assert evidence.status == "observed"


def test_analyze_exercise_diagnostics_given_empty_lesson_expect_factory_finding():
    evidence = analyze_exercise_diagnostics(
        lesson_with_exercises([]),
        expected_objective_ids=(),
    )

    assert evidence.status == "needs_human"
    assert evidence.findings == ["no_exercises"]


def test_analyze_exercise_diagnostics_given_missing_objective_coverage_expect_review_finding():
    lesson = lesson_with_exercises(
        [
            valid_exercise(
                "choose",
                objective_id="obj-other",
                bloom_level="understand",
                payload={
                    "options": [
                        {"option_id": "a", "text": "a"},
                        {"option_id": "b", "text": "b"},
                    ],
                    "answer_id": "a",
                    "stem": None,
                },
            )
        ],
        objective_ids=("obj-uncovered",),
    )

    evidence = analyze_exercise_diagnostics(lesson)

    assert evidence.status == "needs_human"
    assert evidence.findings == ["objective_without_exercise:obj-uncovered"]


def test_analyze_exercise_diagnostics_given_match_pairs_expect_notice_phase_and_pairs_count():
    lesson = lesson_with_exercises(
        [
            valid_exercise(
                "match_pairs",
                payload={
                    "left": [{"left_id": "a", "text": "A"}],
                    "right": [{"right_id": "b", "text": "B"}],
                    "pairs": [{"left_id": "a", "right_id": "b"}],
                },
            )
        ]
    )

    evidence = analyze_exercise_diagnostics(lesson)

    assert evidence.response_opportunities == 1
    assert evidence.phase_counts == {"notice": 1}
    assert evidence.status == "observed"


def test_analyze_exercise_diagnostics_given_open_operations_expect_unverified_observations():
    lesson = lesson_with_exercises(
        [
            valid_exercise("speak", exercise_id="say-it", payload={"target": "Hei."}),
            valid_exercise(
                "write",
                exercise_id="write-it",
                bloom_level="apply",
                payload={
                    "response_language": "no",
                    "judge_prompt": "Check it.",
                    "criteria": [{"id": "clear", "instruction": "Use a clear sentence."}],
                },
            ),
        ]
    )

    evidence = analyze_exercise_diagnostics(lesson)

    assert evidence.status == "observed"
    assert "unverified_open:speak:say-it" in evidence.diagnostics
    assert "unverified_open:write:write-it" in evidence.diagnostics


def test_analyze_exercise_diagnostics_given_binary_recall_heavy_set_expect_review_signals():
    lesson = lesson_with_exercises(
        [
            valid_exercise(
                "recall_fill",
                payload={
                    "audio_target": "Hei.",
                    "segments": [
                        {"kind": "blank", "blank_id": "one", "options": ["a", "b"], "answer_index": 0},
                        {"kind": "blank", "blank_id": "two", "options": ["c", "d"], "answer_index": 0},
                    ],
                },
            )
            for _ in range(4)
        ]
    )

    evidence = analyze_exercise_diagnostics(lesson)

    assert evidence.dominant_operation_share == 1
    assert evidence.binary_option_saturation == 1
    assert "single_operation_concentration" in evidence.diagnostics
    assert "dominant_operation_concentration" in evidence.diagnostics
    assert "binary_option_saturation" in evidence.diagnostics
    assert evidence.findings == []
    assert evidence.status == "observed"
