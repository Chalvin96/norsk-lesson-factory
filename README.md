# norsk-lesson-factory

Open Norwegian Bokmål lesson data plus the agentic QA pipeline used to build and maintain it.

This repository has two audiences:

- **Learners, teachers, and app builders** who want structured Bokmål lesson artifacts they can
  inspect, transform, or use in a learning product.
- **Contributors and pipeline builders** who want to improve the lessons or study how the data is
  generated, reviewed, repaired, and exported.

The project currently contains **104 validated lessons** across CEFR A1-B2. For stable data, use a
tagged version or GitHub release. The working branch may include in-progress lesson and pipeline
changes.

Latest tags include:

```bash
git clone --depth 1 --branch v2026.06.18 https://github.com/Chalvin96/norsk-lesson-factory.git
```

Inside a tagged checkout, the exported lesson artifacts live in [`dist/lessons`](dist/lessons), with
a machine-readable index in [`dist/manifest.json`](dist/manifest.json).

## What Is In The Data?

Each exported lesson is a JSON document with:

- lesson metadata: slug, title, CEFR level, concept slug
- teaching sections: orientation, model, contrast, recap
- exercises: recall, matching, choose, categorize, build, judge, find/fix
- review pools for downstream assessment or app rendering

Useful files in a tagged checkout:

```text
dist/manifest.json          # list of all exported lessons
dist/schema/lesson.schema.json
dist/lessons/*.json         # learner-facing exported lesson artifacts
data/lessons/*.json         # internal source-of-truth lesson aggregates
```

Example:

```bash
jq '.lessons[] | select(.cefr_level == "A1") | {slug, title, path}' dist/manifest.json
jq '{title, cefr_level, goal, elements: (.elements | length)}' dist/lessons/modal_verbs_basic.json
```

If you only want the data, start with a tagged checkout and read `dist/manifest.json` +
`dist/lessons/*.json`. You do not need to run the pipeline.

`data/lessons` is the source of truth used to regenerate exports.

## Why This Is Interesting

The interesting part is not "call an LLM to write a lesson." It is treating the LLM as an
**unreliable component** and engineering around its failure modes: a reviewer that over-reports
defects, a repair loop that might not converge, and a model backend that can silently go down.

```text
                 ┌── deterministic gate (schema, anchors, coverage) ──┐
   lesson ──►    │                                                    │ ──► human gate ──► export
                 └── LLM judge panel (pedagogy · alignment · answer) ──┘        ▲
                            │ blocking issue                                    │ park / resume
                            ▼                                                    │
                      fix / regenerate ──► re-judge ──► converged? ─────────────┘
                       bounded budget,    rubric        else escalate to human
                       no-progress guard  floors
```

Notable engineering choices:

- **Calibrated LLM judge.** The reviewer issue lists are advisory, while rubric scores are calibrated
  against golden lessons and promoted deliberately. See [docs/CALIBRATION.md](docs/CALIBRATION.md).
- **Generate-then-verify.** An answer reviewer solves exercises with the answer key hidden, then
  compares its answer to the authored key.
- **Bounded repair.** Fix/regenerate loops use budgets plus `lesson_hash` and
  `blocking_fingerprint` guards to escalate no-progress loops to a human.
- **Failure-aware state.** LLM backend status is first-class, so outages do not read as clean reviews.
- **Strict dependency injection.** Graph nodes are pure `(state, deps)` functions; IO and LLM calls
  live behind injected collaborators, which keeps the suite fast and offline-testable.

See [PIPELINE.md](PIPELINE.md) for the architecture wiki and
[docs/TERMINOLOGY_MAINTENANCE.md](docs/TERMINOLOGY_MAINTENANCE.md) for the terminology/data
maintenance workflow.

## Quickstart

Requirements:

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/)

```bash
uv sync
uv run pytest -q
uv run ruff check .
```

Work with the data:

```bash
# list lessons
jq -r '.lessons[] | [.cefr_level, .slug, .title] | @tsv' dist/manifest.json

# validate source/export stability
uv run lesson-data regenerate-dist --repo-root .

# run the terminology audit
uv run lesson-data terminology audit data/lessons/*.json dist/lessons/*.json --summary
```

Run the pipeline if you want to inspect or improve the generation/QA system:

```bash
uv run lesson-data graph run adjective_agreement  # QA graph; scratch by default
uv run lesson-data chat                           # conversational operator REPL
uv run langgraph dev                              # visual graph inspection in Studio
```

Live LLM journeys need backend credentials (Codex CLI / OpenRouter depending on the selected
surface). The test suite, schema validation, export regeneration, and deterministic checks run
offline.

## Repository Layout

```text
src/lesson_builder/
  pipeline/
    lesson_qa_graph.py       # Lesson-QA StateGraph topology and routing
    judges.py  fixers.py     # injected LLM collaborators
    graph_runner.py state.py # run/show/resume threads; state, routing, budgets
    checks/                  # deterministic gate + advisory check folding
    cold_author/             # cold lesson authoring
    calibration/             # rubric-floor calibration
  chat/                      # terminal operator REPL
  schema/                    # Pydantic lesson + export contracts

data/lessons/                # 104 internal lesson aggregates; source of truth
data/concept_requirements/   # lesson requirements cards
dist/lessons/                # exported lesson artifacts for consumers
dist/schema/                 # exported JSON Schema
goldens/                     # calibration anchors
docs/                        # calibration, terminology, and system notes
PIPELINE.md                  # architecture wiki
```

## How To Contribute

Contributions are welcome in three lanes.

### 1. Improve Lesson Data

Best for teachers, Norwegian learners, linguists, and people testing the data in an app.

1. Pick a lesson in [`data/lessons`](data/lessons); this is the source of truth.
2. Use the matching file in [`dist/lessons`](dist/lessons) as the learner-facing preview.
3. Keep terminology aligned with [docs/terminology-style-guide.md](docs/terminology-style-guide.md).
4. Run regeneration and confirm the exported lesson matches the accepted source text.
5. Run:

```bash
uv run lesson-data regenerate-dist --repo-root .
uv run lesson-data terminology audit data/lessons/*.json dist/lessons/*.json --summary
uv run pytest -q
```

`regenerate-dist` should report `104 unchanged` when the committed source and exported data are
already in sync. A dist-only lesson fix is not enough; it can be overwritten by the next
regeneration.

### 2. Add Or Improve Pipeline Behavior

Best for engineering contributions.

1. Read [PIPELINE.md](PIPELINE.md) for the graph/node model.
2. Keep graph nodes pure and put IO/LLM behavior behind injected dependencies.
3. Add behavior tests with fakes instead of hitting live CLIs or APIs.
4. Follow the local test naming rules in `AGENTS.md`.
5. Run:

```bash
uv run pytest
uv run ruff check .
```

### 3. Improve Terminology

Best for grammar reviewers and curriculum contributors.

1. Update [docs/terminology-style-guide.md](docs/terminology-style-guide.md) first.
2. Keep hard bans small; most choices should be reviewer guidance, not regex rules.
3. Use `dist/lessons` as the exported preview, but land accepted lesson text in `data/lessons`.
4. See [docs/TERMINOLOGY_MAINTENANCE.md](docs/TERMINOLOGY_MAINTENANCE.md) for the current workflow.

Good first contributions:

- Fix unclear learner-facing explanations.
- Improve A1/A2 naturalness without removing standard textbook terms.
- Add missing examples to an existing lesson.
- Report a terminology drift with the lesson slug and exact phrase.
- Add tests for a deterministic check or CLI behavior.

## Data Use Notes

In a tagged checkout:

- Start from `dist/manifest.json`.
- Load individual lessons from `dist/lessons/<slug>.json`.
- Validate against `dist/schema/lesson.schema.json`.
- Treat the schema as the public contract; internal `data/lessons` includes fields used by the QA
  pipeline and may be less convenient for app rendering.

If you build on the data, preserve attribution to `norsk-lesson-factory` and note that the lessons
are generated and QA-reviewed, not a substitute for professional language instruction.

## License

This repository is licensed under the Creative Commons Attribution 4.0 International License
(`CC BY 4.0`). You may use, share, and adapt the lesson data and repository contents, including in
commercial projects, as long as you give appropriate credit to `norsk-lesson-factory`.

Suggested attribution:

```text
Norwegian Bokmål lesson data adapted from norsk-lesson-factory, licensed under CC BY 4.0.
```

## Tests

```bash
uv run pytest                  # full offline suite
uv run ruff check .            # lint
uv run mypy src/               # types
```

Current baseline: **487 passed, 7 skipped**.

## Documentation

- [PIPELINE.md](PIPELINE.md) — node-by-node pipeline wiki
- [docs/CALIBRATION.md](docs/CALIBRATION.md) — judge calibration story
- [docs/TERMINOLOGY_MAINTENANCE.md](docs/TERMINOLOGY_MAINTENANCE.md) — terminology maintenance workflow
- [docs/terminology-style-guide.md](docs/terminology-style-guide.md) — prescriptive terminology guide
- [docs/SCHEMA.md](docs/SCHEMA.md) — exported lesson contract
- [dist/schema/lesson.schema.json](dist/schema/lesson.schema.json) — exported lesson schema

## Status

This is an active data and pipeline project. The data and pipeline are usable, but lessons are still
generated and QA-reviewed; review them for your audience before using them in production instruction.
