# Domain Context

Shared vocabulary for this codebase. Architecture reviews and new code should
use these terms rather than inventing synonyms.

## Modules / packages

- **`pipeline/improvement_steps/`** — the improvement-flow request-handling steps
  (`triage`, `parse_request`, `feasibility`, `add_exercises`, `add_explanation`,
  `revise_lesson`). These are plain functions orchestrated imperatively by
  `improvement_flow.py`; they are NOT LangGraph `StateGraph` nodes. (Renamed from
  the misleading `pipeline/nodes/`.) The actual graph nodes live inline in
  `lesson_qa_graph.py`.

- **`checks/defect_rules.py`** — the pure defect-detection core (constants, span
  walkers, predicates, normalization) shared by the deterministic validators and
  the eval harness (`scripts/eval_lesson_defects.py`). Holds no `CheckResult` and
  no severity policy; severity/advisory decisions live in the individual
  validators.

- **Lesson-QA graph modules** — one-way DAG `graph_deps` ← `graph_nodes` ←
  `lesson_qa_graph`:
  - `graph_deps.py` — contracts + deps: `LoadedLesson`, the Loader/Fixer/Judge/
    Rejudge/Exporter Protocols, `GraphDeps`, `LessonLoadError`, `noop_fixer`,
    `default_loader`.
  - `graph_nodes.py` — the 10 LangGraph node functions and their private helpers.
  - `lesson_qa_graph.py` — topology only (`build_lesson_qa_graph` wiring); re-exports
    the public surface so `from lesson_builder.pipeline.lesson_qa_graph import …`
    still resolves.
