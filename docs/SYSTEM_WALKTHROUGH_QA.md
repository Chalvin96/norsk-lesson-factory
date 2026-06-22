# Current-System Walkthrough — Interview (Q&A)

> Top-to-bottom grilling of the **currently implemented** pipeline (not the deferred
> `curriculum_design/` design — that lives in `PIPELINE_DESIGN_QA.md`). Every step,
> review, branch, and logic decision is approved here against the real code. Dated
> 2026-06-21. Companion to `PIPELINE.md` (the *what*); this is the *approved why*.

---

## Q1 — Top-level dispatch (three-way, repo-state keyed)

**Decision: APPROVED — three-way entry, not two-way.**

Mental model: empty repo → curriculum flow; otherwise → improvement. Refined: the
true branch is **three-way**, because right after curriculum flow runs there are
*zero* lessons and `improve` (J3) needs an existing `data/lessons/<slug>.json`.
Cold-author is the unavoidable middle rung.

```
entry(repo_root, slug):
  no curriculum (data/curriculum empty)      → curriculum_design flow   (build the map)  [UNBUILT]
  curriculum present, lesson <slug> absent   → cold author   J1         (write it)
  lesson <slug> present                      → improve       J3         (refine it)
```

"Improvement mode" = steady state after a slug has been authored once. The
"empty→curriculum, else→improve" intuition is correct at the *bookends*;
cold-author fills the gap.

**Reality today:** there is **no auto-dispatcher**. The CLI exposes discrete
explicit subcommands (`author`, `improve`, `import`, `curriculum`,
`graph run/show/resume/list`, `ledger`, `chat`). The chat LLM-router is the
closest to auto-routing but does not branch on repo-emptiness.

**OPEN — dev experience:** the three-way dispatcher needs a deliberate DX design
(single front-door command vs. the chat router learning the branch vs. an explicit
`init`/`build` verb). Not yet specced. Flagged for a follow-up planning pass.

---

## Q2 — Cold-author staging (4 calls) + the exercises-blindness gap

**Decision: APPROVED with change — adopt (B), tighten stage 3.**

Chain (each a separate LLM call except #4, deterministic):

1. `author_metadata_objectives(slug, requirements)` → title/goal/CEFR/objectives + `bloom_targets` (sees requirements ✓)
2. `author_sections(objectives, requirements)` → teaching sections per objective (sees objectives + requirements ✓)
3. `author_exercises(objectives)` → ≥2 exercises/objective — **sees objectives ONLY** ✗ (the gap)
4. `assemble_lesson(...)` → deterministic assembly + anchor injection + preflight ✓

Failure handling approved as-is: any stage fails → `needs_human` + partial draft
preserved to `tmp/cold_author_drafts/` (R4); overwrite guard refuses an existing
lesson unless `--force` (R3); draft then runs the **same** back-half QA graph.

**The gap + decision:** stage 3 authors from objectives + bloom_targets alone —
blind to the `sections` just written and to the `requirements` anchors. Anchor
coverage currently leans on stage-4 deterministic injection + the
`requirement_anchors` validator. **Chosen (B):** feed stage 3 both `sections` and
`requirements` so the model authors exercises that test what was *taught* and aim
at anchors directly (injection/validator become a backstop, not the primary
mechanism).

**WORK ITEM (build):** widen `author_exercises(...)` signature + prompt to accept
`sections` + `requirements`; keep deterministic injection as backstop.

---

## Q3 — Back-half entry: load → judge, cost/safety branches

**Decision: APPROVED + 2 work items.**

Graph runs `load → judge → run_checks`. Judge runs **once** on entry (not in the
fix loop, not on human-edit re-entry); carried review payloads are re-folded by
every `run_checks` pass.

**Branch B (skip reviewer panel on hard blockers) — APPROVED.** `judge_node` runs
the deterministic gate first; if any load-bearing hard blocker exists it returns
all reviews `None` and skips the 3-call panel. Clarified: this adds **zero** fixer
cost — the fixer runs because of the deterministic blockers regardless; reviewers
are advisory and can't clear a hard blocker. So B is strictly cheaper (3 wasted
reviewer calls avoided on a lesson about to change). Pedagogy is re-derived
post-fix (Task A) once floors are load-bearing.

**Branch C (outage ≠ clean) — APPROVED.** `call_llm` (invocation.py) fails over
across the whole `BackendStep` chain; `BackendDownException` reaching the graph =
**all backends exhausted** (genuine total outage, not a blip). `run_checks` then
emits a `pedagogy_unavailable` blocker (when floors load-bearing ∧ no pedagogy
review ∧ `llm_status` shows a pedagogy failure) so the outage surfaces to the
human gate. Does NOT fire for the offline noop judge (empty `llm_status`).

**WORK ITEM 1 — kill the noop default.** `noop_judge`/`noop_fixer` are test-only
but are currently the **silent default** in `GraphDeps`. Make `judge`/`fixer`
**required** (no `default_factory`); keep `noop_*` importable for explicit test
injection. Prevents a miswired caller shipping a zero-review lesson with no error.

**WORK ITEM 2 — invalidate stale LLM reviews on fix/regen.** Today a fix carries
the stale `objective_alignment_review` + `answer_review` forward and `run_checks`
keeps folding their pre-fix findings. Set both `→ None` on fix/regen. Pedagogy is
already re-derived (load-bearing); the **deterministic** `answer_valid_check`
re-runs every pass (cheap) so answer correctness stays covered with no extra LLM
spend. Net: no stale advisory folds.

---

## Q4 — The deterministic gate

**Decision: APPROVED a/b/c, keep d warning + 1 work item (coverage→blocker).**

Gate severities: **hard blockers** = `schema_validate`, `structural` (exercise),
`objective_structural`, `nynorsk`, `requirement_anchors`. **Warning-only** =
`bloom_alignment`, `regression`, `stale`.

**(a) Schema short-circuit — APPROVED.** Schema failure returns only schema results,
skips the other 7 checks. Rationale endorsed: can't run structural/anchor checks on
malformed shape; and if schema keeps failing, the author model is too weak — that's
the signal to surface, not bury under downstream noise.

**(b) `requirement_anchors` HARD blocker — APPROVED, resolves the Q2 tension.** Anchor
miss blocks export + drives the fix loop, so coverage is already *guaranteed* by the
gate. Q2's choice (B, author toward anchors) is therefore an **efficiency win** (fewer
fix/regenerate cycles), not a correctness fix.

**(c) `nynorsk` HARD blocker — APPROVED.** Wrong written standard = wrong content.

**(d) `bloom_alignment` warning — KEPT, with coverage promoted.** The check emits 3
finding types: (1) exercise level ∉ objective targets, (2) objective target never
reached, (3) objective has **zero** linked exercises. Level fidelity (1+2) stays
**warning** — Bloom-level inference is heuristic, over-block risk; the load-bearing
pedagogy signal is the calibrated rubric-floor *score*, not this structural heuristic.
But **coverage (3) must be guaranteed**: every objective needs ≥1 exercise.

**WORK ITEM 3 — split `bloom_alignment` severity.** Type 3 (zero-exercise objective)
→ `blocker` + `revision_target`; types 1+2 stay `warning`. Closes the gap where
cold-author asserts ≥2 exercises/objective at *authoring* time but the gate only
*warns* on zero — so a fix that drops an exercise can't silently ship an uncovered
objective.

---

## Q5 — Routing, budgets, convergence, floors, the quality flywheel

**Decision: APPROVED + work items 4–6.**

**Escalation ladder** (`route_after_aggregate`): actionable issues → `2 fixes →
1 regenerate → human`. Max 3 author attempts. APPROVED. `actionable = is_blocking
OR revision_target` (advisory issue-lists never route).

**Convergence guard** (`route_after_fix`): escalate to human if the latest fix
no-op'd (`hash_before == after`) or its `blocking_fingerprint` repeated a prior
fix. Scans backwards for the latest *fix* (robust to human-edit tail entries).
APPROVED. Regen has no symmetric guard — accepted (regen budget=1 bounds it).

**Floors route, don't hard-block — APPROVED (no hard block).** A below-floor
pedagogy score is `revision_target` (drives the loop) but NOT `is_blocking` (so
`blocking_issues` at export doesn't keep it). Reconciliation: the current graph
**always parks at the human gate** — there is no auto-accept bypass yet — so a
below-floor lesson is **never silently shipped**; the human sees `signoff_score`
+ the floor finding and decides. Hard-blocking would trap marginal lessons a
human might legitimately accept. The deferred auto-accept-at-scale rule:
`0 blockers ∧ scores ≥ floor ∧ regression clean` → else route to human (never
auto-export below floor).

**Regenerate earns its place (phase-1 grounding).** Recovered phase-1 from git:
`phase1_repair`=fix (schema/answerability), `phase1_regenerate`=regenerate
(*"defects are STRUCTURAL — re-author the WHOLE lesson, don't patch"*),
`phase1_revise`=rubric-floor revision. Regenerate is for structural defects a
patch can't fix (wrong-paradigm class, e.g. `past_tense` on modals). KEEP it.
**WORK ITEM 4:** phase-1 kept the **best gate-clean attempt by rubric overall —
"a rework must never regress."** Current `regenerate_node` blindly replaces; adopt
keep-best so a regenerate never ships worse than the lesson it discarded.

**The ledger needs a consumer, not a flag.** Today the ledger is read only by the
graph's baseline lookup + `ledger mark-unverified`. A `below_floor` bool would be
write-only dead data, and would go **stale** when `rubric_floors.json` changes.
**WORK ITEM 5:** build a `ledger report` consumer that **derives** quality from
stored `signoff_score` vs current floors (no stored flag). EX surface = CLI table
(`--below-floor`, sort by score); PM/CV surface = `--format md` rollup
(`data/reports/quality.md`: counts, mean, weakest axes) — a portfolio screenshot.

**The quality flywheel (proceed-path).** The report is the **prioritized backlog**
for `improve` (J3): `author → gate → export → ledger → report → improve → re-export
↺`. **WORK ITEM 6:** `improve --below-floor [--limit N]` pulls flagged slugs from
the *same* query that built the report (selector and report can't drift). Escalation
terminus (no infinite churn): still-below-floor after an improve → human rework at
the gate, OR — if the concept itself is unauthorable/too broad — emit the
**curriculum re-open signal** (the human-bridged hook into the deferred
`curriculum_design` loop). The report is what surfaces "this concept keeps failing"
= the trigger to reconsider the *curriculum*, not just the lesson.

---

## Q6 — The human review process

**Decision: APPROVED — decision model as-is; persona split; visual editor DEFERRED.
Captured as [ADR-0002](adr/0002-human-review-persona-split.md).**

**Decision model (`route_after_human`) — APPROVED unchanged.** accept → export
(refused/re-parked if `is_blocking` blockers remain ∧ no `override`); override →
export past blockers, ledgered `accepted_override`, excluded from regression
baselines; edit → re-runs `run_checks` on the edited lesson (re-validated); defer
→ END, parked, resumable.

**Surface = the gap, split by persona:**
- **Engineer** = per-lesson gate review (chat + CLI). Rough edges: whole-JSON-replace
  edit; review view doesn't yet fold issues+signoff+diff. → **WORK ITEM 7:** chat
  `show` → real review view + granular per-exercise/section edit.
- **PM** = portfolio surface only (Q5 quality report + `improve --below-floor`
  batches). Never touches the per-lesson gate.
- **Non-tech reviewer** = blocked on the **visual editor — DEFERRED**. Until it
  ships, non-tech users don't do per-lesson review; the gate is engineer-operated.

Resolves the standing "non-tech can't CLI" tension: non-tech isn't expected to
review per-lesson yet; PM steers at portfolio level.

---

## Q7 — Export + acceptance ledger (terminal boundary)

**Decision: APPROVED — machinery as-is; reviewer hardcode deferred; WI 8 (per-axis).**

Export ordering is load-bearing and correct: (0) idempotency by `source_lesson_hash`
vs latest ledger entry → no-op on re-run; (1) **validate before write** (failed
validation writes nothing — can't corrupt the internal store); (2) atomic writes
(temp + `os.replace` + fsync file & dir — never partial); (3) **ledger appended
LAST** (crash → idempotent re-run, never an export without provenance); (4) manifest
upsert; scratch-vs-repo `output_root` keeps demo runs off the tracked tree. APPROVED.

Ledger records `signoff_score`, `status` (accepted/accepted_override),
`blocking_issues` (forced-past on override), `corrections_applied` (fragility),
hashes, paths.

**(1) `reviewer="human"` hardcoded — FINE for now** (human gate always hit). Revisit
at the auto-accept-at-scale milestone (should reflect human vs calibrated-gate).

**(2) WORK ITEM 8 — persist per-axis rubric scores at export.** Ledger stores only
overall `signoff_score`; the Q5 PM report's "weakest axes" rollup + "which axis
dropped this lesson below floor" need per-axis. Persist them (ledger field or
sidecar keyed by `export_hash`) so the report reads the ledger instead of
re-judging. Note `signoff_score` can be `None` (pedagogy skipped/failed, Q3 B/C) →
report renders unverified as a distinct state, not score 0.

---

## Q8 — Error handling + the recovery contract

**Decision: APPROVED — 4 in-node handlers stand; +work items 9–13; recovery =
rerun, made clean.**

**Taxonomy (clean):** `LlmException` → `LlmQuotaException`/`LlmParseException`/
`BackendDownException`; `LessonLoadError`; `StageFailure`/`AssemblyError`.

**Four in-node handlers — APPROVED:** (1) LLM chain fails over across `BackendStep`s,
re-raises last on exhaustion; (2) fixer catches the 3 LLM exceptions → records
`failure_kind` → convergence guard escalates to human; (3) cold-author stage fail →
`needs_human` + partial draft preserved; (4) export validate-first/atomic/idempotent.

**(A) WI 9 — `parse_request` silent fallback.** `parse_request_with_agent` swallows
the agent exception and degrades to the deterministic parser with no log — a dead
agent silently degrades forever. Keep the fallback, add a `warning` log with the
swallowed exception.

**(B) WI 11 — document + test the client-wrap invariant.** Verified all clients
already wrap remote/transient failures into `LlmException` (`exec_subprocess` wraps
`FileNotFoundError`/`TimeoutExpired`; httpx helpers wrap `HTTPError`/auth/quota). The
graph's narrow catch is safe *because of* this. Make it an explicit contract + a
guard test that no client leaks a raw exception. Keep the narrow catch.

**(C) WI 10 — uniform CLI error envelope, no bug-masking.** Don't add a graph-level
park-as-failed that swallows unexpected exceptions — a real bug should crash loudly,
not read as a soft "failed lesson." Instead wrap each `_cmd_*` so any exception prints
`error: <msg>` + nonzero exit (matching the chat REPL), not a raw traceback.

**Recovery contract — RERUN, made clean.** `resume_thread` refuses non-parked threads
(`ThreadNotParkedError`), so resume is only for human-gate-parked threads; everything
else recovers by **rerun**. Decided: rerun is fine (no resume-non-parked complexity).
The irony noted — cleanly-handled failures checkpoint *past* the retry point (need a
fresh rerun) while uncaught crashes are technically resumable — is accepted; we don't
chase resume. Terminal rerun is already clean (export idempotent by
`source_lesson_hash`; same run_id replays the checkpoint).

**WI 12 (revised) — make rerun leave no mess:** (a) cold-author drafts overwrite a
deterministic path / clean on success (stop littering `tmp/cold_author_drafts/`);
(b) failure surface prints the exact rerun command (same run_id → checkpoint replay);
(c) optional `graph prune` for orphaned non-parked checkpointer threads (or document
they're inert).

**WI 13 — document the Branch-C recovery.** Load-bearing judge outage stays
parked-and-human-visible ("not verified" must reach a human); document its recovery:
rerun when the backend is healthy to obtain the review, or accept-unverified at the
gate. The human shouldn't have to guess.

---

## Q9 — The improvement flow (J3): steady-state mode

**Decision: APPROVED + WI 14 (diagnosis-guided improve, all paths).**

J3 is a **deterministic Python chain** (not a LangGraph despite `nodes/` naming):
hydrate `LessonIndex` → parse spec → load lesson → `feasibility_check` → branch →
author → shared back-half graph.

**(a) No operation gap — APPROVED.** `ImprovementOperation` is exactly 3:
`add_exercises` / `add_explanation` / `improve`. The `else: needs_human` branch is
**dead defensive code** (the type already constrains it). The flywheel op (`improve`
→ `revise_lesson`) is covered. Dead branch may stay defensive or be dropped.

**(b) Two front-filters — APPROVED.** `feasibility_check` = deterministic pre-flight
gating the expensive author (`feasible`→author / `off_scope`→triage /
`infeasible`→human). `triage_request` = retrieval-grounded (queries `LessonIndex`
top-k, grounds the rationale). Both protect the back-half from junk requests.
Confidence `low` (no slug detected) → triage; off_scope → triage; infeasible →
human; lesson missing → human.

**Two parse paths (intended):** improve CLI uses the **deterministic** keyword parser
(blunt/predictable); the chat surface uses the **LLM** parser (rich/nuanced). Keep
both.

**(c) WI 14 — diagnosis-guided improve (the real seam).** Today `improve` →
`revise_lesson(spec, lesson)` receives only the text-parsed spec — NOT the structured
below-floor diagnosis. So a flywheel `improve --below-floor` on a lesson failing the
`objective_alignment` axis does a **generic whole-lesson revise**, blind to *which*
axis failed — may not lift it and may regress others (the "rework must never regress"
risk, cf. WI 4). **Decision (approved both paths):**
- **Flywheel improve** bypasses text round-tripping — builds an `ImprovementSpec`
  directly + carries the below-floor findings (axis, floor gap, rubric issues) into
  `revise_lesson`. The report→improve handoff carries the diagnosis, not just the slug.
- **Manual free-text improve** ALSO fetches the target lesson's current rubric
  findings and passes them in — so even a hand-typed "improve X" is diagnosis-guided,
  not a blind rewrite.

Makes the Q5 flywheel actually convergent: report says "axis X below floor" → improve
targets axis X → report re-measures X.

---

## Q10 — Import flow (J2) + the dist-reconstruction question

**SUPERSEDED (2026-06-21, ADR-0003):** the import-from-dist recovery journey was retired and
backlog WI 17 was deleted. `data/lessons/` (+ `data/concept_requirements/`) is the git-tracked source
of truth; `dist/` is a derived serving projection regenerated from data, never re-imported.

**Decision: APPROVED + WI 17 (hash-gated re-enrich). Architect + codex both
independently endorsed re-enrich and both flagged the same drift failure mode.**

**J2 solid — APPROVED:** `import_lesson` defaults `imported_unverified` (no silent
regression baseline); validate-first; idempotent (export_hash+status → no-op);
goldens excluded as an import source (calibration integrity); CLI/script-only (no
chat) matches the persona split.

**The dist-reconstruction finding (the crux):** the export is lossy in a
load-bearing way. `to_export_dict` drops, unrecoverable from dist:
`objectives[].statement` + `.bloom_targets`, `Section.objective_ids`, `Section.id`,
per-exercise `bloom_level` — exactly the fields the gate
(`bloom_alignment_check` incl. the WI 3 coverage blocker, `objective_structural`)
consumes. So:
- **dist serves the product** (learner-facing content intact) — sufficient to SERVE.
- **dist alone CANNOT be re-QA'd/improved** — degraded placeholders.
- **Faithful recovery = dist + `concept_requirements`** (the Q1 scope card holds the
  dropped objectives/bloom_targets/anchors).

**Reviewer consensus (architect + codex, independent):** re-enrich; "serving-only" is
the wrong center of gravity because the improvement flywheel (J3) is central and the
gates need the dropped fields — dist-only stamping `accepted` silently converts a
full-fidelity authoring system into a degraded serving archive. BUT both flagged
**semantic drift**: the scope card is the *pre-authoring* contract; the flywheel may
have revised the lesson away from it, so blind re-enrich produces a confidently-wrong
lesson that QAs against fictional structure (fails quietly — worse than placeholders).

**WORK ITEM 17 — hash-gated re-enrich (finalized):**
1. Recovery contract: internal = **dist + concept_requirements**, never dist alone.
2. Import re-enriches export-dropped fields from the scope card **only when its
   `requirements_hash` matches** the shipped lesson's recorded hash.
3. Match → faithful reconstruction + honest status; mismatch/missing →
   `imported_unverified` + `needs_scope_reconciliation`, placeholder objectives,
   **never `accepted`**.
4. **Fix the blanket accepted-stamp on the retired dist recovery path** (both reviewers: the actual
   bug — it laundered unverifiable lessons into trusted baselines).
5. Reuse existing plumbing: the ledger already carries `recorded_requirements_hash`
   and `stale_check` already compares it — drift detection is mostly built.
6. **`concept_requirements` = load-bearing for recovery, conditionally** (trusted only
   when hash-matched); must be tracked/backed-up alongside dist.

---

## Q11 — Curriculum flow (current stub vs deferred loop)

**Decision: APPROVED as known interim stub. Full `curriculum_design` planned
SEPARATELY after this interview (not now).**

**Today:** single-pass deterministic drafter — `parse_request → triage → route
(patch_lesson/split/new_topic) → template requirements → park to tmp/ → explicit
commit`. **Good discipline (keep):** drafts to `tmp/`, never touches lesson JSON,
explicit `commit_curriculum_draft`, computes `stale_impacts` via `requirements_hash`,
seeds structure.

**The stub:** `_generate_requirements` emits only `required_anchor_forms` (crude
tokenization) + hardcoded `min_clean_examples: 5` + a templated `notes` string. NO
objectives / bloom_targets / CEFR — the core of a scope card. So cold-start (Q1: no
curriculum → curriculum flow → cold-author) is **weak at the curriculum layer**: the
stub scope card has no objectives, so cold-author invents them ungrounded.

**Nuance — the shipped 104 are fine.** The existing `concept_requirements` were
authored by a past GLM redesign loop, not this flow. Solid for *improving the 104*
and *stub-then-hand-fix topic adds*; NOT for genuine literal-zero bootstrap (that
hits the stub).

**Resolution:** (1) current flow = known interim stub, not a bug; keep its discipline.
(2) Deferred `curriculum_design` (research→write→review, [PIPELINE_DESIGN_QA.md])
**subsumes** it — keep discipline, replace `_generate_requirements` with real
objectives+bloom_targets+anchors. (3) **WI 18 honesty:** docs/README state cold-start
is scaffold-only today; literal-zero bootstrap is gated on `curriculum_design`.
**Full curriculum design planned AFTER this interview.**

---

## Q12 — Reviewer panel + calibration (load-bearing quality signal)

**Decision: APPROVED + WI 19 (median-of-3 on the load-bearing pedagogy score).**

**Floors ARE promoted** (corrects stale memory): `rubric_floors.json`
`load_bearing: True`, `n_goldens: 5`, `overall_min: 91.43`, axis floors (1–5)
`on_concept/complete/bokmal/answerable/depth=4, sequencing=3, presentable=2`. The
scores-route story is LIVE.

**Panel (judges.py) — APPROVED.** 3 producers run in parallel: `pedagogy_review`
(7-axis scores, load-bearing), `objective_alignment_review` (advisory),
`answer_review` (advisory). **Blinded answer reviewer = standout pattern:**
`answer_review` is the reviewer's OWN answers to the blinded exercises;
`answer_valid_check` compares them to the lesson's key — mismatch = wrong/ambiguous
key. Independent re-derivation, not self-grading.

**Floors — APPROVED.** Floor = MIN(kept-golden axis score) − tolerance; goldens below
`exclude_below` dropped. A **quality-collapse detector**, not an optimizer — strict on
content axes (=4), lenient on noisy `presentable` (=2). Correct for calibrate-first:
route away from broken, don't chase excellent.

**The issue — single-reviewer variance.** `reviewer()` is ONE logical reviewer (zai→
opencode fallback), not a consensus panel. A borderline lesson's route/accept rests on
one stochastic scoring call → can flip above/below a floor on re-run. `floor_tolerance`
absorbs calibration slack, NOT sampling noise. Floors are one-sample-per-golden too.

**WORK ITEM 19 — median-of-3 on the load-bearing pedagogy review** at both calibration
(per golden) and gate-time (per lesson). Advisory reviews stay single-sample. 3× cost
on the one review that routes the loop — justified; bias cancels (same reviewer scores
goldens+lessons), multi-sample kills the variance.

---

## Q13 — Derived store / retrieval (`LessonIndex`)

**Decision: APPROVED + WI 20 (replace hash-embedding retrieval; LLM now,
sqlite_vec as scale-path).**

**Architecture — APPROVED.** Derived, rebuildable sub-lesson index (SQLite; rows
`slug/unit_type/unit_id/text/embedding/hash`; content-hashed idempotent hydrate from
`data/lessons`). Fits the Q10 model — `data/lessons` authoritative, index is
throwaway/rehydratable, no recovery concern. Zero-cost deterministic (no model call).

**The finding:** `retrieve()` uses `_hash_embedding` (dim-64 hashed-token
pseudo-embedding) + pure-Python cosine — **lexical-overlap, not semantic**. So
"retrieval-grounded triage" (Q9) + curriculum impact (Q11) are lexical-grade. A
semantically-phrased request can misroute (e.g. "make the verb-conjugation lesson
harder" misses slug `past_tense`). Severity low — triage is a human-gated front
filter that only fires on low-confidence/off-scope requests.

**WORK ITEM 20 — replace hash-embedding retrieval.** Two paths: (A) sqlite_vec + real
embeddings (best at scale, needs infra); (B) **LLM triage** (feed the slug catalog +
request to the LLM, classify route + matched lesson — infra-free, semantic, consistent
with the LLM-first stack). **Decision: B now** (at 104 lessons the catalog fits one
prompt; cost bounded — triage only fires on ambiguous requests), **A documented as the
scale-path** (catalog outgrows a prompt). `LessonIndex` stays for `slugs()` +
hydrate; only `retrieve()` is replaced. Benefits both J3 triage and curriculum routing.

---

## Q14 — Gate validator internals (hard-blockers)

**Decision: APPROVED as-is. `requirement_anchors` soft-match accepted for now.**

**`structural` (policy Pydantic can't express) — APPROVED.** Blockers: `judge`
is_correct=False + empty feedback; `recall_fill` with no blanks; audio/listen media
(enforces deterministic-grading — no audio); empty example `no`/`en`, empty table
headers/body. Warnings: `<3` exercises (thin_lesson), `<3` distinct ops
(repetitive_operation_mix), build tokens pre-sorted (trivial). Warning severities right.

**`objective_structural` — APPROVED.** Bidirectional connectivity: every
exercise/section/pool `objective_id` resolves to a declared objective, AND every
declared objective is used by ≥1 exercise/section. All blockers.

**`nynorsk` — APPROVED (edge noted).** Word-bounded (`WORD_RE.findall` → curated
marker set), not raw substring. Edge: flags a marker in a legit Bokmål-vs-Nynorsk
contrast example → false-positive; low risk (Bokmål-only curriculum). Optional opt-out
marker later.

**`requirement_anchors` — soft match ACCEPTED for now (no WI).** Check is raw
substring over `json.dumps(elements)`: (1) not word-bounded (`"han"` satisfied by
`"handle"`), (2) matches the whole JSON incl. ids/slugs/ops/keys, not just
learner-visible text. So the hard guarantee is SOFT — it proves the substring exists
somewhere in serialized JSON, NOT that the anchor is taught. This means the Q4 claim
("anchors hard-blocked ⇒ coverage guaranteed ⇒ Q2-B is mere efficiency") is overstated;
Q2-B (author toward anchors) has real value. **Owner decision: `json.dumps` is fine for
now** — accept the soft match as interim; revisit only if anchor false-positives surface.

## Q15 — Remaining validators + answer-correctness (load-bearing decision)

**Decision: APPROVED + WI 22 (answer_valid load-bearing on reviewer consensus).**

**`schema_validate` — APPROVED.** Firewall: validates internal `Lesson` → export
projection → `ExportedLesson`; both shapes must pass. Blocker + short-circuits.

**`regression` — APPROVED.** Canonical current-export vs trusted baseline; differs →
`warning` only (a legit improvement is a diff). No-op without baseline.

**`stale` — APPROVED.** `warning` on `recorded_requirements_hash` ≠ current hash; no-op
without recorded hash (imports need backfill, ties WI 17).

**`answer_valid` — PROMOTED to load-bearing (owner call).** Blinded reviewer answers
each exercise; `compare_answers` vs the lesson's own key; mismatch = key probably wrong.
Today it emits `severity=blocker, advisory=True` → `is_blocking=False` → advisory only,
so **a wrong answer key currently ships** (human-visible, unblocked). Wrong keys are the
worst defect (actively teach the wrong answer) → must block.

**WORK ITEM 22 — answer_valid load-bearing on reviewer CONSENSUS.** Promote
answer_valid to `revision_target`/blocker (routes the fix loop, blocks export) — but on
**majority disagreement** (≥2/3), NOT single-sample, so one fallible reviewer can't
over-block a correct lesson. This means `answer_review` JOINS the load-bearing set →
**extends WI 19 multi-sampling to `answer_review`** (not just pedagogy). Prereq: WI 19.
Interim (pre-multi-sample): stays advisory to avoid over-block.

---

# Consolidated work-item backlog (WI 1–22)

Build backlog distilled from Q1–Q13.

**STATUS — 2026-06-21 simplification refactor** (`plans/2026-06-21-pipeline-simplification.md`):
- ✅ **DONE / DELETED by the refactor:** **WI 1** (noop defaults removed), **WI 12a**
  (draft writers unified + deterministic overwrite), **WI 20** (hash-embedding → LLM
  triage), **Q9a** (dead branch removed) — batches 1/1B/2, committed.
  **WI 17** (hash-gated re-enrich) — **deleted**: the import-from-dist journey was
  retired (ADR-0003), so there is no lossy inverse to guard. Batch 3, committed.
- **NOT deleted — kept:** **WI 8** (persist per-axis rubric scores for the report) is
  about reportability keyed by export, independent of the recovery model.
- **WI 19 / WI 22 refined:** single-sample default for the pedagogy floor
  (routes-not-blocks, variance tolerable); multi-sample ONLY `answer_valid` (load-bearing
  AND blocking → over-block is the real risk). Forward backlog, not built.
- **Still open (forward backlog):** Q2, WI 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 18,
  19, 22 + the deferred `curriculum_design` loop. (WI 15/16 were superseded by WI 17,
  now all moot — the import-from-dist journey they guarded is retired.)

**Authoring**
- **WI 1 — kill the noop default** in `GraphDeps` (make `judge`/`fixer` required; keep `noop_*` for explicit test injection). [Q3]
- **WI 2 — invalidate stale LLM reviews on fix/regen** (`objective_alignment_review`+`answer_review` → None; pedagogy already re-derived; deterministic `answer_valid_check` covers correctness). [Q3]
- **Q2 — tighten cold-author exercises stage** (feed `sections`+`requirements` into `author_exercises`; deterministic injection stays backstop). [Q2]

**Gate / routing**
- **WI 3 — split `bloom_alignment` severity**: zero-exercise-objective → `blocker`+`revision_target`; level-fidelity findings stay `warning`. [Q4]
- **WI 4 — regenerate keep-best**: compare rubric overall vs pre-regen lesson, keep the higher (never regress; phase-1 parity). [Q5]

**Quality flywheel**
- **WI 5 — `ledger report` consumer**: derive quality from stored `signoff_score` vs current floors (no stored flag). EX = CLI table; PM/CV = `--format md` rollup. [Q5]
- **WI 6 — `improve --below-floor [--limit N]`**: batch from the report query; escalation marker when a slug stays below floor after improve (→ human rework / curriculum re-open). [Q5]
- **WI 8 — persist per-axis rubric scores at export** (ledger field/sidecar by `export_hash`) so the report shows weakest-axis + which-axis-failed; render `None` as unverified. [Q7]
- **WI 14 — diagnosis-guided improve** (both paths): flywheel builds `ImprovementSpec` + below-floor findings directly into `revise_lesson`; manual improve fetches current rubric findings too. [Q9]

**Human review**
- **WI 7 — chat review view + granular edit** (rendered lesson + issues + signoff + regression diff; edit one exercise/section, not whole-JSON). [Q6]
- Visual editor DEFERRED — [ADR-0002]. [Q6]

**Error / recovery**
- **WI 9 — log `parse_request` agent-fallback** (no more silent degradation). [Q8]
- **WI 10 — uniform CLI error envelope** (any exception → `error:`+nonzero, no raw traceback; no bug-masking park-as-failed). [Q8]
- **WI 11 — document+test the client-wrap invariant** (clients wrap all remote failures into `LlmException`). [Q8]
- **WI 12 — clean rerun**: cold-author drafts overwrite/clean (stop littering `tmp/`); failure surface prints the exact rerun command; optional `graph prune` for orphan threads. [Q8]
- **WI 13 — document Branch-C recovery** (load-bearing judge outage → rerun when backend healthy, or accept-unverified). [Q8]

**Import / recovery integrity**
- **WI 17 — hash-gated re-enrich** (import re-enriches export-dropped fields from `concept_requirements` only when `requirements_hash` matches; else `imported_unverified`+`needs_scope_reconciliation`, never `accepted`; fix the retired recovery path's blanket acceptance). `concept_requirements` = load-bearing for recovery. [Q10, reviewer-consensus]

**Calibration / answer correctness**
- **WI 19 — median-of-3 on the load-bearing reviews** (calibration per golden + gate per lesson). [Q12]
- **WI 22 — answer_valid load-bearing on reviewer consensus** (≥2/3 disagree with the key → routes/blocks; extends WI 19 multi-sampling to `answer_review`; interim stays advisory). [Q15]

**Gate detail (Q14/Q15)**
- `requirement_anchors` soft substring-over-JSON match — **accepted as-is** ("json.dumps is fine for now"); revisit only if anchor false-positives surface. (WI 21 considered, DROPPED.)
- `nynorsk` word-bounded marker set — accepted; optional opt-out marker for intentional Bokmål-vs-Nynorsk contrast later.

**Retrieval**
- **WI 20 — replace hash-embedding retrieval** (LLM triage now; sqlite_vec scale-path). [Q13]

**Honesty / docs**
- **WI 18 — docs/README: cold-start is scaffold-only today** (stub scope cards; literal-zero bootstrap gated on `curriculum_design`). [Q11]
- Document the recovery contract (resume-for-parked / rerun-otherwise) + the recovery matrix. [Q8]
- Document triage retrieval as lexical-grade until WI 20 lands. [Q13]

**Deferred (separate planning, not WIs here)**
- `curriculum_design` loop (research→write→review) — subsumes the current stub; planned after this interview. [Q11, `PIPELINE_DESIGN_QA.md`]
- Reviewer field un-hardcode at the auto-accept-at-scale milestone. [Q7]
