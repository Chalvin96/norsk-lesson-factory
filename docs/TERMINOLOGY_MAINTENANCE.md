# Terminology Maintenance

This project uses a small terminology system for English-language Norwegian Bokmål lessons.
The goal is stable learner-facing language, not a maximal grammar ontology.

## Source Of Truth

[terminology-style-guide.md](terminology-style-guide.md) is the terminology source of truth.
It is prescriptive: one pinned term per concept, with limited allowed scaffolds for lower CEFR
levels.

The style guide has three roles:

- Human reference for lesson authors and reviewers.
- Prompt context for LLM authoring/review.
- Source for the deterministic hard-ban list.

The hard-ban list should stay small. Most terminology choices belong in the guide and LLM
review, not in regex rules.

## Lesson Editing Workflow

For MVP lesson stability, review the learner-facing export while keeping `data/lessons` as the
editable source:

1. Pick the lesson in `data/lessons/*.json`.
2. Compare against the matching `dist/lessons/*.json` file as the exported preview.
3. Make accepted terminology and naturalness edits in `data/lessons/*.json`.
4. Run `lesson-data regenerate-dist --repo-root .`.
5. Accept the change only when regeneration reports `104 unchanged`.

`data/lessons` remains the authoritative source for regeneration. `dist/lessons` is the
serving/exported shape, but for human lesson review it is easier to inspect. A dist-only fix is
fragile; the next regeneration can erase it.

## Current Decisions

Current terminology decisions live in [terminology-style-guide.md](terminology-style-guide.md).
The style guide is the prescriptive glossary and decision log; this page only explains how to
maintain and verify it.

## Review Signals

Use these checks before committing terminology changes:

```bash
uv run lesson-data terminology audit data/lessons/*.json dist/lessons/*.json --summary
uv run lesson-data regenerate-dist --repo-root .
uv run ruff check .
uv run mypy src/
uv run pytest
```

For broad terminology passes, also run a raw scan for likely drift terms:

```bash
rg -n "noun group|gender group|tense-carrying verb|subjunction|subordinating connector|-s passive" data/lessons dist/lessons
```

The raw scan is advisory. Some words, such as `connector`, can be correct in discourse lessons
and wrong in grammar lessons; use the style guide to decide.

## External Reference Bias

When deciding between plausible terms, prefer common learner-facing Norwegian resources over
invented house terminology. Useful references include:

- NTNU NoW / LearnNoW grammar pages
- NTNU Short Grammar PDF
- Grammatikk.com terminology material

External sources are evidence, not automatic authority. The house guide may choose a stricter
term when it prevents corpus drift or avoids a Norwegian-specific confusion.
