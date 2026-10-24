"""Entry point: exercise regeneration translation coverage behavior tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lesson_builder.application.operations.workbench_regeneration import assemble_regeneration
from lesson_builder.application.operations.workbench_regeneration import generate_regeneration_item
from lesson_builder.clients.llm.invocation import JobRunner
from tests.application.fakes import FakeLlmClient


def test_generate_regeneration_item_given_changed_text_without_translation_expect_one_corrective_retry(
    tmp_path: Path, monkeypatch
) -> None:
    before = _greeting_exercise_source(reply_text="Hei!", prompt="Choose the appropriate reply.")
    after = _greeting_exercise_source(reply_text="God dag!", prompt="Choose the appropriate reply.")
    captured = _captured_regeneration(before)
    replies = [
        _greeting_edit_response(after, translations=None),
        _greeting_edit_response(after, translations=[{"norwegian": "God dag!", "english": "Good day!"}]),
    ]
    client = FakeLlmClient.from_fn(lambda _prompt, _model: json.dumps(replies.pop(0)))
    monkeypatch.setattr(
        "lesson_builder.application.operations.workbench_regeneration.workbench_runner_for_role",
        lambda _root, _role: JobRunner(name=client.name, client=client),
    )

    edit = generate_regeneration_item(tmp_path, captured, "greeting-choice")

    assert len(client.calls) == 2
    assert "God dag!" in client.calls[1][0]
    assert "translation" in client.calls[1][0]
    assert [item.model_dump() for item in edit.translations] == [{"norwegian": "God dag!", "english": "Good day!"}]


def test_generate_regeneration_item_given_retry_still_missing_translations_expect_job_failure_error(
    tmp_path: Path, monkeypatch
) -> None:
    before = _greeting_exercise_source(reply_text="Hei!", prompt="Choose the appropriate reply.")
    after = _greeting_exercise_source(reply_text="God dag!", prompt="Choose the appropriate reply.")
    captured = _captured_regeneration(before)
    client = FakeLlmClient.responding(json.dumps(_greeting_edit_response(after, translations=None)))
    monkeypatch.setattr(
        "lesson_builder.application.operations.workbench_regeneration.workbench_runner_for_role",
        lambda _root, _role: JobRunner(name=client.name, client=client),
    )

    with pytest.raises(
        ValueError, match=re.escape("Regenerated exercise is missing English translations for: God dag!")
    ):
        generate_regeneration_item(tmp_path, captured, "greeting-choice")

    assert len(client.calls) == 2


def test_generate_regeneration_item_given_every_changed_text_translated_expect_proposal_with_translations(
    tmp_path: Path, monkeypatch
) -> None:
    before = _greeting_exercise_source(reply_text="Hei!", prompt="Choose the appropriate reply.")
    after = _greeting_exercise_source(reply_text="God dag!", prompt="Choose the polite reply.")
    captured = _captured_regeneration(before)
    client = FakeLlmClient.responding(
        json.dumps(
            _greeting_edit_response(
                after,
                translations=[
                    {"norwegian": "Choose the polite reply.", "english": "Choose the polite reply."},
                    {"norwegian": "God dag!", "english": "Good day!"},
                ],
            )
        )
    )
    monkeypatch.setattr(
        "lesson_builder.application.operations.workbench_regeneration.workbench_runner_for_role",
        lambda _root, _role: JobRunner(name=client.name, client=client),
    )

    edit = generate_regeneration_item(tmp_path, captured, "greeting-choice")
    proposal = assemble_regeneration(captured, [edit])

    assert len(client.calls) == 1
    assert proposal is not None
    block = proposal.blocks[0]
    assert block.after == after
    assert [item.model_dump() for item in block.translations] == [
        {"norwegian": "Choose the polite reply.", "english": "Choose the polite reply."},
        {"norwegian": "God dag!", "english": "Good day!"},
    ]
    assert block.pin is not None
    assert block.pin["checked_against"] == {"greetings": "9a41"}


def _greeting_exercise_source(reply_text: str, prompt: str) -> str:
    """Return one choose exercise with the given reply option text and prompt."""
    return (
        "- handle: greeting-choice\n"
        "  op: choose\n"
        "  objective: obj-greeting\n"
        "  bloom: understand\n"
        f"  prompt_md: {prompt}\n"
        "  options:\n"
        "    - id: reply\n"
        f"      text: {reply_text}\n"
        "      correct: true\n"
        "    - id: other\n"
        "      text: God kveld!\n"
        "      correct: false\n"
    )


def _greeting_edit_response(after: str, translations: list[dict[str, str]] | None) -> dict[str, object]:
    """Return one ChatResponse-shaped reply for the greeting exercise."""
    edit: dict[str, object] = {
        "key": "greeting-choice",
        "after_source": after,
        "note": "Updated reply",
        "change_summary": "The correct reply now matches the revised section.",
        "answer_impact": "The correct option is unchanged.",
    }
    if translations is not None:
        edit["translations"] = translations
    return {"reply": "Updated the reply.", "edits": [edit]}


def _captured_regeneration(before: str) -> dict[str, object]:
    """Return the minimal captured state for regenerating the greeting exercise."""
    return {
        "lesson_id": "demo",
        "files": {"lesson.md": "", "exercises.yaml": before},
        "exercise_keys": ["greeting-choice"],
        "exercise_context": {
            "greeting-choice": {
                "exercise_key": "greeting-choice",
                "checked_against": {"greetings": "9a41"},
                "section_sources": {"greetings": "## Greetings\n"},
                "changed_sections": [
                    {
                        "section_id": "greetings",
                        "before_text": "Hei!",
                        "after_text": "Hallo!",
                        "summary": "The section now models an evening greeting.",
                    }
                ],
                "current_exercise_source": before,
            }
        },
        "prompt": "Regenerate the stale exercise.",
    }
