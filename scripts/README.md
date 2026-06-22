# Phase 1 Scripts

This directory is intentionally reset.

Build only the new Phase 1 script surface:

- `phase1_make_prompt.py`
- `phase1_generate_codex.py`
- `python -m lesson_builder.pipeline.checks.gate_manager`
- `phase1_answer_review.py`
- `phase1_read_report.py`
- `phase1_export.py`

Implementation contract:

- `docs/SCHEMA.md` (lesson contract + export boundary)
- `PIPELINE.md` (the current LangGraph pipeline; these phase-1 scripts predate it)

Generated run state belongs under ignored `generated/<run-id>/`. `raw/` is an artifact subfolder there
for unparsed Codex output; do not create or depend on root-level `raw/`.

Old scripts are archived under `archive/phase1-reset-2026-06-16/scripts/` and are reference-only until a helper
is explicitly reviewed and re-adopted.
