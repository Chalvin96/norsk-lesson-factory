"""Evidence-pattern triage reports review candidates without claiming semantic passes."""

from lesson_builder.domain.lesson.validation.exercise_evidence import scan_exercise_evidence


def test_scan_exercise_evidence_given_missing_repair_sentence_expect_source_warning() -> None:
    exercise = {
        "op": "write",
        "prompt_md": "Correct the Norwegian sentence by changing the time word, then explain why.",
        "criteria": [{"instruction": "Changes Da to Når."}],
    }

    findings = scan_exercise_evidence(exercise, "repair_tense_based_choices")

    assert [finding.code for finding in findings] == ["evidence-source-object-missing"]


def test_scan_exercise_evidence_given_visible_repair_sentence_expect_no_source_warning() -> None:
    exercise = {
        "op": "write",
        "prompt_md": "Correct this sentence: «Da jeg var liten, besøkte jeg alltid tante Liv.» Explain why.",
    }

    assert scan_exercise_evidence(exercise, "repair_tense_based_choices") == []


def test_scan_exercise_evidence_given_missing_repair_message_expect_source_warning() -> None:
    exercise = {
        "op": "write",
        "prompt_md": "Correct the Norwegian message by replacing the connector, then explain why.",
        "criteria": [{"instruction": "Changes hvis to om."}],
    }

    findings = scan_exercise_evidence(exercise, "translation_error_repair")

    assert [finding.code for finding in findings] == ["evidence-source-object-missing"]


def test_scan_exercise_evidence_given_visible_repair_message_expect_no_source_warning() -> None:
    exercise = {
        "op": "write",
        "prompt_md": "Correct this Norwegian message: «Jeg vet ikke hvis bussen er forsinket.» Then explain why.",
        "criteria": [{"instruction": "Changes hvis to om."}],
    }

    assert scan_exercise_evidence(exercise, "translation_error_repair") == []


def test_scan_exercise_evidence_given_dialogue_stimulus_expect_no_source_warning() -> None:
    exercise = {
        "op": "write",
        "prompt_md": "Correct the Norwegian message, then explain why.",
        "stimulus": [
            {
                "kind": "dialogue",
                "turns": [{"speaker": "Lea", "text_md": "Jeg vet ikke hvis bussen er forsinket."}],
            }
        ],
    }

    assert scan_exercise_evidence(exercise, "translation_error_repair") == []


def test_scan_exercise_evidence_given_unsupported_sentence_field_expect_source_warning() -> None:
    exercise = {
        "op": "write",
        "prompt_md": "Correct the Norwegian sentence, then explain why.",
        "sentence_md": "Da jeg var liten, besøkte jeg alltid tante Liv.",
        "criteria": [{"instruction": "Changes Da to Når."}],
    }

    findings = scan_exercise_evidence(exercise, "repair_tense_based_choices")

    assert [finding.code for finding in findings] == ["evidence-source-object-missing"]


def test_scan_exercise_evidence_given_category_explanation_expect_leak_warning() -> None:
    exercise = {
        "op": "categorize",
        "items": [
            {
                "item_id": "experience",
                "text": "Har du vært på Fjordhuset før? (Experience at any time before now.)",
            }
        ],
    }

    findings = scan_exercise_evidence(exercise, "anchor_time_connections")

    assert [finding.code for finding in findings] == ["evidence-category-cue"]
    assert "Experience at any time before now" in findings[0].evidence


def test_scan_exercise_evidence_given_ordinary_translation_expect_no_leak_warning() -> None:
    exercise = {
        "op": "categorize",
        "items": [
            {
                "item_id": "friday",
                "text": "Da jeg kom hjem på fredag, ringte jeg pappa. (When I came home on Friday, I called Dad.)",
            }
        ],
    }

    assert scan_exercise_evidence(exercise, "time_connections") == []


def test_scan_exercise_evidence_given_modal_category_translation_expect_leak_warning() -> None:
    exercise = {
        "op": "categorize",
        "items": [
            {
                "item_id": "expectation",
                "text": "Jonas skulle ha kjøpt brød. (Jonas was supposed to buy bread.)",
            }
        ],
    }

    findings = scan_exercise_evidence(exercise, "contrast_expected_and_conditional_actions")

    assert [finding.code for finding in findings] == ["evidence-category-cue"]
    assert "was supposed to" in findings[0].evidence


def test_scan_exercise_evidence_given_correct_option_only_modal_glosses_expect_leak_warning() -> None:
    exercise = {
        "op": "choose",
        "options": [
            {
                "id": "swapped",
                "text": "skulle ha — advice; burde ha — expectation",
                "correct": False,
            },
            {
                "id": "correct",
                "text": "skulle ha — expectation (was supposed to); burde ha — advice (should have)",
                "correct": True,
            },
        ],
    }

    findings = scan_exercise_evidence(exercise, "notice_modal_perspective")

    assert [finding.code for finding in findings] == ["evidence-category-cue"]
    assert "was supposed to" in findings[0].evidence
    assert findings[0].location == "notice_modal_perspective.options[1].text"


def test_scan_exercise_evidence_given_parallel_modal_glosses_expect_no_leak_warning() -> None:
    exercise = {
        "op": "choose",
        "options": [
            {
                "id": "first",
                "text": "skulle ha — expectation (was supposed to); burde ha — advice (should have)",
                "correct": True,
            },
            {
                "id": "second",
                "text": "skulle ha — advice (should have); burde ha — expectation (was supposed to)",
                "correct": False,
            },
        ],
    }

    assert scan_exercise_evidence(exercise, "parallel_modal_glosses") == []


def test_scan_exercise_evidence_given_underdetermined_modal_result_expect_review_candidate() -> None:
    exercise = {
        "op": "recall_fill",
        "segments": [
            {"text_md": "If Jonas had known about the problem, Jonas ville ha "},
            {
                "blank_id": "result",
                "options": ["tatt bussen", "kjøpt brød", "kommet alene"],
                "answer_index": 0,
            },
            {"text_md": " hvis han hadde visst om problemet."},
        ],
    }

    findings = scan_exercise_evidence(exercise, "build_unreal_past_outcomes")

    assert [finding.code for finding in findings] == ["evidence-action-options-review"]
    assert "kjøpt brød" in findings[0].evidence


def test_scan_exercise_evidence_given_modal_form_options_expect_no_action_warning() -> None:
    exercise = {
        "op": "recall_fill",
        "segments": [
            {"text_md": "Jonas "},
            {
                "blank_id": "result",
                "options": ["ville ha tatt", "skulle ha tatt", "burde ha tatt"],
                "answer_index": 0,
            },
            {"text_md": " bussen hvis han hadde visst om problemet."},
        ],
    }

    assert scan_exercise_evidence(exercise, "build_unreal_past_outcomes") == []


def test_scan_exercise_evidence_given_participle_form_options_expect_no_action_warning() -> None:
    exercise = {
        "op": "recall_fill",
        "segments": [
            {"text_md": "The plan was to ta bussen: Jonas skulle ha "},
            {
                "blank_id": "result",
                "options": ["tatt bussen", "ta bussen", "tok bussen"],
                "answer_index": 0,
            },
            {"text_md": " klokka fire."},
        ],
    }

    assert scan_exercise_evidence(exercise, "complete_unfulfilled_arrangements") == []


def test_scan_exercise_evidence_given_same_verb_with_different_objects_expect_review_candidate() -> None:
    exercise = {
        "op": "recall_fill",
        "segments": [
            {"text_md": "Jonas kunne ha "},
            {
                "blank_id": "transport",
                "options": ["tatt bussen", "tatt toget", "tatt bilen"],
                "answer_index": 0,
            },
            {"text_md": "."},
        ],
    }

    findings = scan_exercise_evidence(exercise, "choose_unused_transport")

    assert [finding.code for finding in findings] == ["evidence-action-options-review"]


def test_scan_exercise_evidence_given_burde_ha_with_different_actions_expect_review_candidate() -> None:
    exercise = {
        "op": "recall_fill",
        "segments": [
            {"text_md": "Jonas burde ha "},
            {
                "blank_id": "advice",
                "options": ["ringt verkstedet", "tatt bussen", "bedt om hjelp"],
                "answer_index": 0,
            },
            {"text_md": "."},
        ],
    }

    findings = scan_exercise_evidence(exercise, "choose_retrospective_advice")

    assert [finding.code for finding in findings] == ["evidence-action-options-review"]


def test_scan_exercise_evidence_given_parallel_complete_mapping_options_expect_composite_warning() -> None:
    exercise = {
        "op": "choose",
        "options": [
            {
                "id": "swapped-statement-question",
                "text": "Hei! = greeting; Jeg heter Mina. and Jeg heter Jonas. = ask for a name; "
                "Hva heter du? = state a name; Ha det! = close.",
            },
            {
                "id": "complete-mapping",
                "text": "Hei! = greeting; Jeg heter Mina. and Jeg heter Jonas. = state a name; "
                "Hva heter du? = ask for a name; Ha det! = close.",
            },
        ],
    }

    findings = scan_exercise_evidence(exercise, "notice_four_jobs")

    assert [finding.code for finding in findings] == ["evidence-composite-choice-mapping"]
    finding = findings[0]
    assert finding.severity == "minor"
    assert finding.location == "notice_four_jobs.options"
    assert "swapped-statement-question=4" in finding.evidence
    assert "complete-mapping=4" in finding.evidence
    assert "match_pairs" in finding.explanation


def test_scan_exercise_evidence_given_long_single_decision_options_expect_no_composite_warning() -> None:
    exercise = {
        "op": "choose",
        "options": [
            {
                "id": "correct",
                "text": "The speaker gives her name and then asks the other person to give theirs, "
                "which is the balanced way to open a first meeting at the language school.",
            },
            {
                "id": "distractor",
                "text": "The speaker only closes the conversation with a goodbye and does not "
                "exchange any identity information with the other person in the exchange.",
            },
        ],
    }

    assert scan_exercise_evidence(exercise, "notice_exchange_opening") == []


def test_scan_exercise_evidence_given_single_equals_expression_expect_no_composite_warning() -> None:
    exercise = {
        "op": "choose",
        "options": [
            {"id": "first", "text": "jeg = I"},
            {"id": "second", "text": "jeg = you"},
        ],
    }

    assert scan_exercise_evidence(exercise, "notice_subject_pronouns") == []
