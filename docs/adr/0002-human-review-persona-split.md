# ADR-0002 — Human-review surface: persona split, defer the visual editor

- **Status:** Accepted (visual editor deferred)
- **Date:** 2026-06-21
- **Deciders:** current-system walkthrough interview

## Context

Every lesson — cold-authored (J1) or improved (J3) — parks at one **human gate**
(`human_gate_node` → `interrupt()`), with no auto-accept bypass today. The decision
model is sound and approved as-is:

- **accept** → export; refused (re-parks) if load-bearing `is_blocking` blockers remain and the decision did not set `override`.
- **override** (with accept) → exports past blockers, ledgered `accepted_override`, **excluded from regression baselines** (a forced lesson cannot poison the trusted baseline).
- **edit** → re-runs `run_checks` on the human-edited lesson (the edit is re-validated; no silent broken injection).
- **defer** → END, thread stays parked, resumable.

The gap is the **surface**, not the logic. Today review is CLI-only
(`graph list` → `graph show` / chat `show` → `graph resume --decision`). Two rough
edges: the **edit path replaces the whole lesson JSON via `--lesson-file`** (no
granular per-exercise edit), and the review view does not yet fold issues + signoff
+ regression diff into one rendered surface. A non-technical content reviewer (a
Norwegian teacher) cannot operate a CLI at all.

## Decision

**Split the human role by persona; defer the visual editor.**

1. **Per-lesson gate review = engineer surface.** accept/edit/defer/override runs
   through chat + CLI. This is deliberately an *engineer* task for now.
2. **PM = portfolio surface only.** PMs never touch the per-lesson gate. They
   operate on the quality **report** (derived from the acceptance ledger:
   `signoff_score` vs `rubric_floors.json`) and trigger `improve --below-floor`
   batches, watching the burndown. Steering is portfolio-level.
3. **Non-technical content reviewer = blocked on the visual editor (DEFERRED).**
   Until a rendered review-and-edit UI exists, non-tech users do not do per-lesson
   review. The gate stays engineer-operated.

This resolves the standing "a non-tech user can't operate a CLI" tension: non-tech
users are not *expected* to review per-lesson yet, and PMs are given a portfolio
surface that needs no CLI.

## Consequences

- **Near-term work (engineer surface):** upgrade chat `show` into a real review
  view (rendered lesson + blocking/advisory issues + signoff + regression diff) and
  add a **granular edit** (edit one exercise/section, not whole-JSON-replace).
  Tracked as WORK ITEM 7 in the walkthrough.
- **Deferred (non-tech surface):** the visual editor (rendered lesson + issues +
  accept/edit controls). Designed but not built — see the interaction-surface notes
  in [PIPELINE.md](../../PIPELINE.md) §2.1.
- **No change to the decision model or the graph routing** — this is purely about
  who operates which surface.

## Open questions for the deferred editor spike

1. Granular edit contract — does editing one exercise re-run only that unit's
   checks, or the whole gate? (Today: whole `run_checks`.)
2. Does the editor write through the same `resume_thread(decision={"status":"edit"})`
   seam, or a new structured-patch seam?
3. Auth/identity for a non-tech reviewer (out of scope for the headless engine;
   belongs to the deferred app layer).
