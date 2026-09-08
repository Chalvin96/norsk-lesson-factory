# Doctor-appointment pilot

This fixture is a small unit package with two independently editable lesson
candidates:

```text
doctor_appointment/
├── brief.md                 # unit situation and lesson relationship
├── grammar/
│   ├── brief.md             # reusable grammar lesson contract
│   ├── lesson.md            # learner-facing explanation
│   └── exercises.yaml       # grammar practice
└── communicative/
    ├── brief.md             # interaction contract and prerequisite
    ├── lesson.md            # model exchange and response choices
    └── exercises.yaml       # communicative practice
```

Edit the Markdown/YAML source. Do not edit generated lesson JSON. The focused
test copies both packages into temporary storage, compiles them separately, and
runs the deterministic checks:

```bash
uv run pytest -q tests/application/operations/test_load_lesson_source.py
```

Section roles are source metadata, not learner-facing text. Write a heading as
`## The finite verb carries the question {#sec-model role=model}`. For a short
reading unit with app-revealed translation, write:

```markdown
::: {.reading translation="Hello."}
Hei.
:::
```

The `reading` block is generic: it can hold a dialogue turn, story sentence, or
another short text unit.

The grammar package reuses the existing `question_word_order` ingredient as a
standalone, context-independent explanation. The communicative package applies
the pattern to a doctor's appointment and presents the call as a branching
interaction rather than a fixed sequence.
