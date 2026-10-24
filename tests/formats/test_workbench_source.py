"""Entry point: source editing behavior and corpus alignment tests."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from lesson_builder.application.operations.author_preview import build_package_preview
from lesson_builder.formats.workbench_source import emit_block_fields
from lesson_builder.formats.workbench_source import replace_source_span
from lesson_builder.formats.workbench_source import split_exercise_source
from lesson_builder.formats.workbench_source import split_lesson_source
from lesson_builder.formats.workbench_source import summary_source_text
from lesson_builder.formats.workbench_source import visible_source_text


def test_source_blocks_given_all_canonical_lessons_expect_exact_roundtrip_and_packet_alignment() -> None:
    packages = sorted((Path(__file__).parents[2] / "content" / "lessons").iterdir())
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_assert_packet_alignment, packages))


@pytest.mark.parametrize(
    ("source", "fields", "expected"),
    [
        (
            '::: {.reading translation="Hi" speaker_name="Jonas" speaker_id=jonas}\nHei.\n:::\n\n',
            {"translation": "**Hello**"},
            '::: {.reading translation="**Hello**" speaker_name="Jonas" speaker_id=jonas}\nHei.\n:::\n\n',
        ),
        ("::: {.rule}\nUse *x*.\n:::\n\n", {"statement": "Use **y**."}, "::: {.rule}\nUse **y**.\n:::\n\n"),
        (
            "::: example\n- no: Hei.\n- en: Hi.\n:::\n\n",
            {"en": "Hello."},
            "::: example\n- no: Hei.\n- en: Hello.\n:::\n\n",
        ),
        (
            "::: examples\n- no: Hei.\n\n- en: Hi.\n- no: Ha det.\n- en: Bye.\n:::\n\n",
            {"en_1": "Goodbye."},
            "::: examples\n- no: Hei.\n\n- en: Hi.\n- no: Ha det.\n- en: Goodbye.\n:::\n\n",
        ),
        ("A **bold** paragraph.\n\n", {"text": "An *italic* paragraph."}, "An *italic* paragraph.\n\n"),
    ],
)
def test_block_fields_given_supported_kind_expect_only_target_span_changed(
    source: str, fields: dict[str, str], expected: str
) -> None:
    prefix = "---\nslug: demo\n---\n\n## Intro {#intro role=orient}\n\n"
    suffix = "Untouched paragraph.\n"
    text = prefix + source + suffix
    block = split_lesson_source(text).blocks[1]
    result = replace_source_span(text, block, emit_block_fields(block, fields))
    assert result.encode() == (prefix + expected + suffix).encode()


def test_visible_source_text_given_markdown_span_expect_format_syntax_dropped() -> None:
    source = (
        "## A weekend {#a-weekend role=orient}\n\n"
        '::: {.reading translation="Gloss." speaker_name=Thomas}\n'
        "Greit. Og du bør ta med *støvler* — nå.\n"
        ":::\n\n"
        "- *må* — necessity\n"
    )
    assert visible_source_text(source) == "A weekend Greit. Og du bør ta med støvler — nå. må — necessity"


def test_visible_source_text_given_reading_metadata_changes_expect_excerpt_bytes_unchanged() -> None:
    before = '::: {.reading translation="right now" speaker_name=Thomas}\nHei *nå*.\n:::\n'
    after = '::: {.reading translation="just now" speaker_name=Mari}\nHei *nå*.\n:::\n'

    before_excerpt = visible_source_text(before).encode("utf-8")
    after_excerpt = visible_source_text(after).encode("utf-8")

    assert before_excerpt == after_excerpt == "Hei nå.".encode()


def test_summary_source_text_given_supported_markdown_and_reading_metadata_expect_authored_text() -> None:
    source = (
        "## A weekend {#a-weekend}\n\n"
        '::: {.reading translation="**right now**" speaker_name=Thomas}\n'
        "**Greit**. [kan]{lang=nb} — _støvler_\n"
        ":::\n"
    )

    assert summary_source_text(source) == "A weekend Thomas Greit. kan — støvler right now"


def test_heading_fields_given_title_change_expect_attributes_unchanged() -> None:
    text = "## Old *title* {#intro role=model objectives=o}\n\nBody.\n"
    block = split_lesson_source(text).blocks[0]
    assert (
        replace_source_span(text, block, emit_block_fields(block, {"title": "New **title**"}))
        == "## New **title** {#intro role=model objectives=o}\n\nBody.\n"
    )


def test_exercise_spans_given_curated_id_and_comments_expect_only_item_changed() -> None:
    text = "# header\n- handle: first\n  id: curated\n  prompt_md: 'Old' # note\n# next\n- handle: second\n  prompt_md: \"Keep\"\n"
    first, second = split_exercise_source(text)
    assert first.key == "curated"
    result = replace_source_span(text, first, first.source.replace("'Old'", "'New'"))
    assert result == text.replace("'Old'", "'New'")
    assert split_exercise_source(result)[1].source == second.source


def test_splitter_given_unclosed_fence_expect_next_section_kept_and_exact_roundtrip() -> None:
    text = "## Intro {#intro}\n\n::: {.reading}\nHei.\n\n## Next {#next}\n\nText.\n"
    parsed = split_lesson_source(text)
    assert parsed.frontmatter + "".join(block.source for block in parsed.blocks) == text
    assert [(block.key, block.kind) for block in parsed.blocks] == [
        ("intro", "heading"),
        ("intro#0", "error"),
        ("next", "heading"),
        ("next#0", "paragraph"),
    ]


def test_exercise_spans_given_flow_sequence_expect_only_mapping_replaced() -> None:
    text = '[{handle: first, prompt_md: "Old"}, {handle: second, prompt_md: "Keep"}]\n'
    first, second = split_exercise_source(text)
    result = replace_source_span(text, first, first.source.replace('"Old"', '"New"'))
    assert result == text.replace('"Old"', '"New"')
    assert split_exercise_source(result)[1].source == second.source


def test_reading_fields_given_missing_translation_and_double_quotes_expect_valid_single_quoted_attribute() -> None:
    text = '## Intro {#intro}\n\n::: {.reading speaker_name="Jonas"}\nHei.\n:::\n'
    block = split_lesson_source(text).blocks[1]
    result = replace_source_span(text, block, emit_block_fields(block, {"translation": 'He says "Hi"'}))
    assert "translation='He says \"Hi\"'" in result
    assert split_lesson_source(result).blocks[1].fields["translation"] == 'He says "Hi"'


def _assert_packet_alignment(package: Path) -> None:
    text = (package / "lesson.md").read_bytes().decode("utf-8")
    parsed = split_lesson_source(text)
    assert (parsed.frontmatter + "".join(block.source for block in parsed.blocks)).encode("utf-8") == text.encode(
        "utf-8"
    ), package
    packet = build_package_preview(package, source_label=package.name).packet
    assert packet is not None, package
    for section in packet["sections"]:
        source_blocks = [block for block in parsed.blocks if block.key.startswith(section["id"] + "#")]
        assert len(source_blocks) == len(section["blocks"]), (package, section["id"])
        assert [block.key for block in source_blocks] == [
            f"{section['id']}#{index}" for index in range(len(section["blocks"]))
        ]
