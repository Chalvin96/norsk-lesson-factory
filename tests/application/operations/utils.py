"""Not a test itself — source parsing helpers for application behavior tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lesson_builder.application.operations.convert_lesson_blocks import convert_blocks
from lesson_builder.application.operations.load_lesson import load_lesson_source
from lesson_builder.domain.lesson.models.lesson import Lesson
from lesson_builder.formats.markdown.frontmatter import parse_frontmatter
from lesson_builder.formats.markdown.pandoc import compiler_fingerprint
from lesson_builder.formats.markdown.pandoc import parse_markdown

K_TEST_FM = """\
---
type: Lesson
slug: test_lesson
title: Test Lesson
cefr_level: A1
goal: Test goal.
default_lang: nb
grounding_mode: grounded
bloom_targets: [understand]
objectives:
  - id: o1
    statement: Test objective.
    bloom_targets: [understand]
requirements_ref: curriculum/concepts/test.md
---

"""

K_TEST_LESSON_SOURCE = """\
---
type: Lesson
slug: demo
title: Demo Lesson
cefr_level: A1
goal: Learn basic Norwegian greetings.
default_lang: nb
grounding_mode: grounded
bloom_targets: [understand]
objectives:
  - id: obj_greet
    statement: Greet someone in Norwegian.
    bloom_targets: [understand]
requirements_ref: curriculum/concepts/demo.md
---

## orient: Introduction {#sec-intro}

Hei! Velkommen til norsk.

{{exercise: ex-greet}}

## model: Greetings {#sec-model objectives=obj_greet}

[God morgen]{lang=nb} means good morning.

{{exercise: ex-choose}}

## recap: Summary {#sec-recap}

You learned basic Norwegian greetings.
"""

K_TEST_EXERCISES_SOURCE = """\
- handle: ex-greet
  op: judge
  objective: obj_greet
  bloom: understand
  prompt_md: Is this a valid greeting?
  sentence_md: God morgen!
  is_correct: true

- handle: ex-choose
  op: choose
  objective: obj_greet
  bloom: understand
  prompt_md: Choose the correct greeting.
  options:
    - id: opt_hei
      text: Hei
      correct: true
    - id: opt_farvel
      text: Farvel
"""


def parse_prose(md_text: str) -> SimpleNamespace:
    """Compose the real source parsers for focused behavior tests."""
    frontmatter, body = parse_frontmatter(md_text)
    doc = parse_markdown(body)
    warnings: list[str] = []
    return SimpleNamespace(
        frontmatter=frontmatter,
        blocks=convert_blocks(doc.content, warnings, frontmatter.default_lang),
        fingerprint=compiler_fingerprint(),
        warnings=warnings,
    )


def lesson_source(body: str) -> str:
    """Wrap Markdown content with the standard test front matter."""
    return K_TEST_FM + body


def require_block(result: SimpleNamespace, index: int = 0):
    """Return one parsed block from a test result."""
    return result.blocks[index]


def write_lesson_source(
    root: Path,
    *,
    lesson_text: str = K_TEST_LESSON_SOURCE,
    exercises_text: str = K_TEST_EXERCISES_SOURCE,
) -> Path:
    """Write authored lesson files for a loader behavior test."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "lesson.md").write_text(lesson_text, encoding="utf-8")
    (root / "exercises.yaml").write_text(exercises_text, encoding="utf-8")
    return root


def load_lesson_files(source_dir: Path) -> Lesson:
    """Load authored fixture files through the text-only entry point."""
    return load_lesson_source(
        (source_dir / "lesson.md").read_text(encoding="utf-8"),
        (source_dir / "exercises.yaml").read_text(encoding="utf-8"),
    )
