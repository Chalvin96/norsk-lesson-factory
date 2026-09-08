# norsk-lesson-factory

Norwegian Bokmål lesson data, editable lesson sources, and a command-line
toolchain for authoring, reviewing, and exporting them. Lessons combine learning
objectives, explanations, examples, and exercises covering grammar, phraseology,
and everyday communication.

Use the JSON distribution in a learning application, adapt the Markdown and YAML
sources, or run the authoring pipeline to draft and review content. This
repository owns lesson content and assessment criteria; consuming applications
own presentation, playback, learner progress, and scoring behavior.

## Use the lesson data

Download `lessons.tar.gz` from [GitHub
Releases](https://github.com/Chalvin96/norsk-lesson-factory/releases), or use the
checked-in [`dist/`](dist/) directory. Neither requires the authoring toolchain.
Pin a release tag or commit when integrating the data.

| File | Purpose |
| --- | --- |
| [`dist/catalog.json`](dist/catalog.json) | Lesson IDs, curriculum positions, and family metadata |
| [`dist/lessons/`](dist/lessons/) | One JSON packet per lesson |
| [`dist/schema/lesson.schema.json`](dist/schema/lesson.schema.json) | Public lesson-packet schema |

Each packet includes metadata, objectives, teaching sections, exercises, and a
`content` list specifying their authored order. Exercises include choices,
matching, categorization, sentence building, gap filling, error identification,
and written or spoken responses.

With `jq` installed, inspect the catalog and a lesson from the repository root:

```bash
jq -r '.lessons[] | [.position, .lesson_id, .family_id] | @tsv' dist/catalog.json
jq '{id, title, cefr_level, goal}' dist/lessons/modal_verbs_basic.json
```

## Set up a development checkout

Install Git, Python 3.12+, and `uv`. You also need Node.js with npm to run the
repository's check scripts. Model-dependent evaluations require Node.js
22.22.0+. Python dependencies and development tools are managed by `uv`.

```bash
git clone https://github.com/Chalvin96/norsk-lesson-factory.git
cd norsk-lesson-factory
uv sync --locked
uv run lesson-data doctor
```

The default doctor checks local offline prerequisites without calling a
provider. Editing source, previewing lessons, validating data, and regenerating
JSON do not require provider credentials. Run `uv run lesson-data --help` to see
the available commands; each command also supports `--help`.

## Edit and validate a lesson

Edit `lesson.md`, `exercises.yaml`, and the related source files in a lesson's
directory under [`content/lessons/`](content/lessons/). These files are
authoritative. Regenerate the derived `dist/` files and include them with source
changes.

```bash
uv run lesson-data exercise lint content/lessons/adjective_agreement
uv run lesson-data preview content/lessons/adjective_agreement
```

The preview serves a read-only authoring view on loopback. It makes no provider
calls and writes no source or distribution files. Use `--no-browser` for a
headless session or `--port 0` to select an available port. Stop the preview with
Ctrl+C before continuing.

```bash
uv run lesson-data regenerate-dist --repo-root .
uv run lesson-data check --workspace-root . --format json
```

Review and stage or commit intentional source and distribution changes, then run
the complete deterministic suite:

```bash
npm run check
```

This checks conventions, formatting, lint, types, tests, mechanical exercise
quality, distribution drift, workspace consistency, and knowledge links. It
regenerates the distribution and uses `git diff --exit-code` to detect unstaged
distribution changes. These checks establish structural consistency; linguistic
and pedagogical quality still require review.

## Generate lesson drafts

Generation requires the OpenCode CLI on your `PATH`, authenticated provider
access, and POSIX file locking (Linux, macOS, or WSL). Configure the model routes
and authoring jobs in [`config.yaml`](config.yaml) for models available to your
provider account. See the [LLM client documentation](src/lesson_builder/clients/llm/README.md)
for configuration and authentication details.

Check local prerequisites, then generate a scratch draft for one approved
catalog entry:

```bash
uv run lesson-data doctor --capability generation
uv run lesson-data generate-lessons \
  --repo-root . \
  --plan content/curriculum/plan.yaml \
  --batch-id first-draft \
  --catalog-id greet_and_introduce_yourself \
  --workers 1
```

The generation command makes provider calls. The doctor checks local setup but
does not verify live provider access. Use a new batch ID for a new run; omit
`--catalog-id` only when you intend to generate the whole plan.

Drafts and batch artifacts live under ignored
`store/scratch/catalog_generation/`; resumable workflow state is stored in
`store/catalog_generation_checkpoints.db`. Review the draft and its findings
before accepting it. `promote-lessons` handles explicit approval and promotion
into canonical source; inspect its `--help` for batch selection and replacement
options. Generation and distribution export do not themselves approve content
or change the curriculum.

For optional model-dependent prompt evaluations:

```bash
uv run lesson-data doctor --capability eval
npm run eval:promptfoo -- -c evals/promptfoo/reviews.yaml
```

## Package a distribution

To validate and package the current `dist/` tree locally:

```bash
uv run lesson-data validate-distribution --repo-root . --distribution-root .
uv run lesson-data package \
  --repo-root . --distribution-root . --output /tmp/lessons.tar.gz
```

Packaging creates a deterministic archive of a complete validated distribution.
`--distribution-root` names the directory containing `dist/`. Packaging does not
generate lesson drafts or publish a release.

Audio synthesis is a separate, provider-backed export. It requires Google Cloud
Text-to-Speech credentials and audio configuration; see [`.env.example`](.env.example)
and [`config.yaml`](config.yaml). Inspect its prerequisites and options with:

```bash
uv run lesson-data doctor --capability audio
uv run lesson-data export --help
```

`export` writes to `store/scratch/audio-distribution/` by default. Use that
directory as `--distribution-root` when validating or packaging an audio export.
The [release workflow](.github/workflows/release.yml) validates, packages, and
publishes a distribution when a maintainer pushes a `v*` tag.

## Repository layout

| Path | Contents |
| --- | --- |
| [`content/catalog/`](content/catalog/) | Approved lesson inventory and family registry |
| [`content/curriculum/`](content/curriculum/) | Curriculum plan and sequence |
| [`content/lessons/`](content/lessons/) | Editable lesson packages |
| [`content/authoring/`](content/authoring/) | Shared character authoring data |
| [`dist/`](dist/) | Generated catalog, lesson packets, and public schema |
| [`src/lesson_builder/`](src/lesson_builder/) | CLI, authoring, review, export, and release code |
| [`tests/`](tests/) and [`scripts/`](scripts/) | Behavior tests and repository checks |
| [`evals/promptfoo/`](evals/promptfoo/) | Optional model-dependent evaluations |
| [`knowledge/`](knowledge/) | Current domain and architecture decisions |
| `store/` | Ignored local drafts, checkpoints, and caches |

## Contribute

For content corrections, include enough context to explain the problem and keep
lesson prose, prompts, answers, and feedback consistent. Report issues through
the [public issue tracker](https://github.com/Chalvin96/norsk-lesson-factory/issues).

Read [AGENTS.md](AGENTS.md) for contributor conventions and the [knowledge
index](knowledge/index.md) for current design decisions. Before adding lessons or
changing their scope, read [authoring decisions](knowledge/pipeline/authoring.md)
and [source and distribution boundaries](knowledge/project/boundaries.md).
Include validation commands and results in your pull request.

Report vulnerabilities privately using the guidance in [SECURITY.md](SECURITY.md).

## Licenses and attribution

- **Lesson data:** `content/`, `dist/catalog.json`, and `dist/lessons/` are
  licensed under [CC BY 4.0](LICENSE-DATA.md). Credit the project and identify
  changes when adapting or redistributing the data; the license file supplies
  attribution wording and provenance details.
- **Code and other materials:** everything else, including documentation and
  `dist/schema/`, is licensed under [MIT](LICENSE).

The shipped lessons were produced with LLM-assisted authoring and reviewed
through the repository's human and automated workflow. See
[LICENSE-DATA.md](LICENSE-DATA.md) for the full scope and third-party attribution.
