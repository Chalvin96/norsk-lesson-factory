# norsk-lesson-factory

Open Norwegian Bokmål lesson data and the tools used to author, review, and
export it.

The repository contains structured grammar, phraseology, communication,
pronunciation, and writing lessons. Each lesson combines explanations, examples,
and exercises around a learning objective. Markdown and YAML are the editable
source; ready-to-consume JSON packets live in `dist/`.

## Use the lesson data

Applications can consume the checked-in distribution without installing the
authoring toolchain:

- [`dist/catalog.json`](dist/catalog.json) lists lessons in curriculum order and
  includes family metadata.
- [`dist/lessons/`](dist/lessons/) contains one self-contained packet per lesson.
- [`dist/schema/lesson.schema.json`](dist/schema/lesson.schema.json) defines the
  public lesson-packet contract.

Pin a release tag or commit so data updates are deliberate. For example, with
`jq` installed:

```bash
jq -r '.lessons[] | [.position, .lesson_id, .family_id] | @tsv' dist/catalog.json
jq '{id, title, cefr_level, goal}' dist/lessons/modal_verbs_basic.json
```

A packet contains lesson metadata, objectives, teaching sections, exercises, and
an ordered `content` list. Exercises cover choices, matching, categorization,
sentence building, error identification, gap filling, and written or spoken
responses.

This repository defines authored content and assessment criteria. A consuming
application owns presentation, playback, answer reveal, learner progress, and
scoring behavior. See [Authoring and Release
Boundaries](knowledge/project/boundaries.md) for the complete contract boundary.

## Edit a lesson

Local development requires Python 3.12+ and
[`uv`](https://docs.astral.sh/uv/). Set up a checkout and run the offline checks:

```bash
git clone https://github.com/Chalvin96/norsk-lesson-factory.git
cd norsk-lesson-factory
uv sync --locked
uv run lesson-data doctor
uv run lesson-data exercise lint content/lessons/adjective_agreement
uv run lesson-data regenerate-dist --repo-root .
uv run lesson-data check --workspace-root . --format json
```

Edit a lesson's `lesson.md`, `exercises.yaml`, and related source under
[`content/lessons/`](content/lessons/), then commit the regenerated `dist/` files
with the source change. Keep prompts, answers, feedback, grading criteria, and
surrounding lesson prose consistent, and include the context needed to answer each
exercise.

Use the local authoring preview to inspect one package in a browser:

```bash
uv run lesson-data preview content/lessons/greet_and_introduce_yourself
```

The preview binds to loopback, performs no provider calls, and does not modify
source or distribution files. Use `--no-browser` in a headless environment or
`--port 0` to choose an available port.

Adding a lesson also changes the approved catalog and curriculum plan. Read
[Authoring Decisions](knowledge/pipeline/authoring.md) before changing that
inventory. Engineering contributions must follow [AGENTS.md](AGENTS.md).

## Validate a change

Run the complete deterministic check suite with:

```bash
npm run check
```

This checks conventions, formatting, linting, types, behavior tests, mechanical
exercise quality, regenerated distribution drift, repository consistency, and
knowledge links. These checks validate structure and consistency; they do not by
themselves establish linguistic or pedagogical quality.

Model-dependent evaluations require Node.js 22.22.0+ and authenticated access to
the provider configured in [`config.yaml`](config.yaml):

```bash
npm run eval:promptfoo -- -c evals/promptfoo/reviews.yaml
```

Provider-backed generation, audio, and evaluation are optional for reading,
editing, validating, and regenerating the lesson data. Run `uv run lesson-data
doctor --capability all` to inspect their local prerequisites without making a
provider call.

## Repository layout

```text
content/                         editable catalog, curriculum, and lesson source
dist/catalog.json                exported curriculum order and family metadata
dist/lessons/<lesson-id>.json    exported lesson packets
dist/schema/lesson.schema.json   public packet schema
src/lesson_builder/              authoring, review, export, and release tooling
tests/                           deterministic behavior tests
evals/promptfoo/                 model-dependent quality evaluations
knowledge/                       current domain and architecture decisions
store/                           ignored drafts, checkpoints, and caches
```

Human-edited source under `content/` is authoritative, and `dist/` is its derived
distribution. Generated drafts remain in ignored scratch storage until a human
accepts them. Exporting data does not approve a draft or redefine the curriculum.
The [knowledge index](knowledge/index.md) is the starting point for the repository's
current design decisions.

## License

Lesson data under `content/`, `dist/lessons/`, and `dist/catalog.json` is licensed
under [CC BY 4.0](LICENSE-DATA.md). Source code and other repository materials,
including the exported schema, are licensed under [MIT](LICENSE). See those files
for exact scope and attribution terms.
