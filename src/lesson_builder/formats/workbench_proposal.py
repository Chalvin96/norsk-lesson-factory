"""Entry point: `render_proposal_text` projects authored Markdown/YAML into comparison text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import yaml

from lesson_builder.formats.markdown import panflute
from lesson_builder.formats.markdown.pandoc import parse_markdown


@dataclass(frozen=True)
class ProposalText:
    """One semantic text region retains its authored language for the rendered comparison."""

    text: str
    lang: str = "en"


def render_proposal_text(
    source: str, *, kind: Literal["markdown", "exercise"] = "markdown"
) -> tuple[ProposalText, ...]:
    """Remove format syntax through the actual parser; raw source remains separately inspectable."""
    try:
        if kind == "exercise":
            values = yaml.safe_load(source)
            return tuple(_exercise_text(values))
        document = parse_markdown(source)
        result: list[ProposalText] = []
        for block in document.content:
            _render_block(block, result)
        return tuple(result)
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return (ProposalText("Rendered comparison unavailable. Inspect raw source."),)


def _render_block(block: panflute.Element, result: list[ProposalText], lang: str = "en") -> None:
    if isinstance(block, panflute.Div):
        reading = "reading" in block.classes
        if reading and block.attributes.get("speaker_name"):
            result.append(ProposalText(block.attributes["speaker_name"]))
        for child in block.content:
            _render_block(child, result, "nb" if reading else block.attributes.get("lang", lang))
        if reading and block.attributes.get("translation"):
            result.append(ProposalText(panflute.stringify(parse_markdown(block.attributes["translation"])).strip()))
        return
    text = panflute.stringify(block).strip()
    if text:
        result.append(ProposalText(text, lang))


def _exercise_text(value: object, lang: str = "en") -> list[ProposalText]:
    if isinstance(value, str):
        return [ProposalText(panflute.stringify(parse_markdown(value)).strip(), lang)]
    if isinstance(value, list):
        return [text for item in value for text in _exercise_text(item, lang)]
    if isinstance(value, dict):
        authored = (
            "prompt",
            "prompt_md",
            "stem",
            "stem_md",
            "stimulus",
            "turns",
            "speaker",
            "text",
            "text_md",
            "value",
            "spans",
            "body",
            "sentence",
            "sentence_md",
            "sentences",
            "audio_target",
            "segments",
            "tokens",
            "options",
            "left",
            "right",
            "buckets",
            "items",
            "label",
            "answer",
            "content",
            "feedback",
            "explanation",
            "explanation_md",
            "why",
            "judge_prompt",
            "criteria",
            "instruction",
        )
        result = []
        explicit = value.get("lang", lang)
        for key in authored:
            if key not in value:
                continue
            child_lang = _exercise_language(key, str(explicit))
            result.extend(_exercise_text(value[key], child_lang))
        return result
    return []


def _exercise_language(key: str, fallback: str) -> str:
    return (
        "nb"
        if key
        in (
            "tokens",
            "left",
            "stimulus",
            "segments",
            "items",
            "sentence",
            "sentence_md",
            "audio_target",
        )
        else "en"
        if key
        in (
            "prompt",
            "prompt_md",
            "explanation",
            "explanation_md",
            "why",
            "speaker",
            "right",
            "buckets",
            "stem",
            "stem_md",
            "feedback",
            "judge_prompt",
            "criteria",
            "instruction",
        )
        else fallback
    )
