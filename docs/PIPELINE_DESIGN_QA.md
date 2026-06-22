# Pipeline design — interview Q&A (cold start → dist)

> A grilled, decision-by-decision record of how the pipeline works and *why*, from literal zero
> (no curriculum) to a shipped lesson. Captures the design intent that the deleted `scripts/phase1_*`
> standard used to hold. Companion to [PIPELINE.md](../PIPELINE.md) (the *what*, code-cited) — this is
> the *why* and the open decisions. Each entry: **Q**, the **recommended answer**, and the **decision**
> once resolved.

## Verified current flow (from PIPELINE.md + code, 2026-06-21)

Cold-start chain: `curriculum text → concept_requirements/<slug>.json` (partial: drafts requirements,
no research, concept list hand-authored) → `cold_author_flow` (4 staged LLM calls: metadata/objectives
→ sections → exercises → deterministic assemble) → **QA back-half graph** (`load → judge → run_checks →
[fix|regenerate]* → regression → signoff → park → human_gate → export`) → `dist/lessons/<slug>.json` +
acceptance ledger.

Review layers: (1) deterministic gate — 8 checks, schema-validate short-circuits, hard-blocking;
(2) advisory LLM judges — pedagogy / objective-alignment / blinded-answer, log-only; (3) rubric-floor
check — pedagogy **scores** vs golden floors, load-bearing when promoted. Loop: `K_GRAPH_MAX_FIXES=2`,
`K_GRAPH_MAX_REGENERATES=1`, convergence guard (`lesson_hash` + `blocking_fingerprint`) escalates a
stuck loop to the human gate.

The open design under interview: a **curriculum *designer*** at the very front (currently deferred —
PIPELINE.md §7, ADR-0001) using two subagents (write-curriculum + research-topic → consolidate).

---

## Q&A

### Q1 — What does the curriculum designer produce, and is it one-shot or a loop?

**Decision.** The curriculum was **never hand-written** — it is LLM-built by a **loop**:

1. **Researcher** subagent — researches the topic (what a Norwegian A1–B2 learner must cover).
2. **Writer** subagent — writes the curriculum down (concept list + sequence).
3. **Reviewer** — checks the result for topics to **consolidate / split / delete**.
4. **Per-concept requirements** are also produced — they define each lesson's **boundary**, which is the
   signal for whether a concept should be **split** (too broad) or **merged** (overlapping/thin).
5. It is a **whole loop** — research → write → review → boundary-check → revise → converge.

Output is therefore both tiers (course map *and* per-concept requirements), produced together because
the requirements are what tell the reviewer where the lesson boundaries should fall. This is the same
generate → review → converge shape as the lesson-QA loop, applied at the curriculum level.

_(Precedent: the 2026-06-17 curriculum redesign — 106→104, 5 merges + 4 adds — was done exactly this
way, via GLM subagents in 3 reviewed passes.)_

### Q2 — What drives split/merge/delete, and what stops the loop?

**Decision. Requirements-driven signals + stable-pass convergence.**

- **Split** — a concept's per-concept requirements exceed a complexity bound (too many objectives/anchors
  for one lesson, or they span two distinct sub-skills).
- **Merge** — two concepts' requirements heavily overlap (shared anchors/objectives), or one is too thin
  (below a min-objectives floor).
- **Delete** — a concept is fully covered by neighbors, or falls outside the target CEFR band.
- **Converge** — loop until a review pass proposes **zero** consolidate/split/delete changes (a stable
  pass), capped at ~3 passes to bound cost (as the 2026-06-17 redesign ran).
- **Human approves the final map** before any lesson authoring — a curriculum-level HITL gate mirroring
  the per-lesson human gate.

Per-concept requirements are the load-bearing signal here: they make lesson boundaries concrete, so
split/merge is judged on evidence (objective count, anchor overlap) rather than vibes.

### Q3 — What does the researcher ground its findings in?

**Decision. Real web research, cited.** The researcher does live retrieval (CEFR Norwegian competence
goals, official curricula, reference courses) via a search tool and produces **cited** findings — not
unverified model memory. This matches the project's ground-then-verify ethos: the curriculum shape is
the highest-leverage decision in the whole pipeline, so it must rest on real sources, not LLM recall.
Cost: adds a search-tool dependency to the curriculum stage.

### Q4 — Self-contained loop, or coupled to lesson QA?

**Decision. Decoupled, human-bridged.** The curriculum loop runs to a **human-approved map first** —
requirements are derived analytically to judge boundaries, no lessons authored yet. Lesson authoring/QA
is downstream and does **not** auto-revise the curriculum. But when QA finds a concept *unauthorable /
too broad*, that surfaces as a **flagged recommendation** to re-open the curriculum loop for that
concept — **human-triggered**, not automatic. Keeps the two loops from churning against each other while
still capturing the real "this boundary was wrong" feedback.

### Q5 — Staged authoring vs phase1 single-pass

**Decision. Keep staged (4 calls).** metadata/objectives → sections → exercises → deterministic assemble.
Each call has a narrow job; stage 4 enforces structure (anchors, review pool, gate preflight) by
construction; a mid-chain backend failure becomes `needs_human` with the partial draft preserved.

Clarification from the owner: phase1's value was its **prompt + result quality**, not the single-pass
*method* — so the staged method is fine, and prompts may be improved freely. (See Q6.)

### Q10 — Where does the curriculum-designer loop live?

**Decision. Standalone `curriculum_design/` module, one engine, two modes — subsumes `curriculum/flow.py`.**

- **Bootstrap mode** — literal-zero / new repo: research → write the whole course map → review/converge →
  all requirements. The **front door** for a fresh repo.
- **Increment mode** — generated / existing repo: re-open the loop for one concept (add / split / merge).
  This is what the current partial `curriculum/flow.py` half-does; it becomes this mode.

Same loop engine at different scope (mirrors `cold_author` = one lesson vs a course build = many). One
command surface (`curriculum design` / `curriculum add|revise <concept>`), one human gate, one code path
to maintain — the biggest DX win is **not** having two curriculum implementations drift apart. Built with
the same DI discipline as the QA graph so it's offline-testable; optionally its own small LangGraph
(research/write/review/converge nodes).

### Q9 — Human gate at course scale (cold-starting ~104 lessons)

**Decision. Calibrate first; auto-accept on the calibrated gate; refine later via `improve`.** A
from-zero course build does **not** put a human read in front of all 104 lessons. Instead: a lesson
auto-accepts when the load-bearing layers all pass (zero deterministic blockers + rubric scores clear
floors + regression clean); per-lesson human refinement is deferred to the `improve` / `graph run`
journey (J3) when wanted, not forced upfront.

> **Hard precondition (not yet met).** This is only safe once the rubric floors are **promoted**
> (`data/calibration/rubric_floors.json` → `load_bearing: true` via `rubric_run --promote`). Per the
> `project_calibration_corpus_mismatch` memory, no judge is promoted yet (codex-only; GLM was down).
> Auto-accept-at-scale is blocked on a trustworthy, promoted calibration. "Calibrate first" is literal.

---

## Verified settled stages (code-cited, no open decision)

These were confirmed against the code during the interview; recorded so the *why* isn't lost.

- **Human-gate semantics** (`human_gate_node`, `route_after_human`) — the interrupt payload carries
  blocking issues + advisory issues + regression verdict + signoff score + attempt ledger. Decisions:
  `accept` → export (re-parks if load-bearing blockers remain and no `--override`); `edit` → re-enter
  `run_checks` to re-validate the human's edit (same gate, single funnel); `defer` → END (stays parked).
  `--override` accepts past remaining blockers and marks the lesson `accepted_override` (excluded from
  regression baselines, so a forced accept can't become a trusted bar).
- **Regression** (`regression_node`) — re-checks the final lesson vs the latest trusted baseline and emits
  a **structured verdict** (passed/warnings/severity/diff/reason). **Non-blocking by design** — only
  warns, so a real diff reaches the human instead of reading as "no change". Never blocks the loop.
- **Signoff** (`signoff_node`) — `signoff_score = pedagogy_percent(review)` (0–100 over the 7 axes) when a
  pedagogy review exists, else None. Advisory; the human gate is still the decision point.
- **Export** (`export_node`, `default_exporter`) — reached only on an admitted accept. Writes the internal
  `Lesson` source in `data/lessons/<slug>.json` + the projected `dist/lessons/<slug>.json`
  (via `to_export_dict`, lossy — see [SCHEMA.md](SCHEMA.md)) + an acceptance-ledger entry
  atomically. `data/lessons/` is the source of truth; `dist/lessons/` is a derived projection
  regenerated from data, not a place to hand-author lesson fixes.
- **Budgets / convergence** (`state.py`) — `fixes=2`, `regenerates=1`, `signoff_retries=1`; the
  convergence guard (`lesson_hash` + `blocking_fingerprint`) escalates a no-progress loop to the human.

## Cold-start sequence (resolved end-to-end)

```
literal zero
  │
  ▼  curriculum_design (BOOTSTRAP) ───────────────── loop ─────────────────┐
  │    researcher (web, cited) → writer (course map) → reviewer            │
  │    (consolidate/split/delete, driven by per-concept requirements)      │
  │    └──────────── until a stable pass (≤3), then ────────────────────► HUMAN approves map
  │
  ▼  cold_author per slug (4 staged calls, prompts seeded from phase1 archive)
  │    metadata/objectives → sections → exercises → deterministic assemble
  │
  ▼  Lesson-QA graph  (load → judge → run_checks → [fix×2 | regenerate×1] → regression → signoff)
  │    review trust: deterministic=hard · LLM issues=advisory · rubric scores=load-bearing
  │
  ▼  gate at scale:  auto-accept when (0 blockers ∧ scores clear floors ∧ regression clean)
  │                  else park for human;  refine later via `improve` (J3)
  │                  ⚠ precondition: rubric floors PROMOTED (not yet done)
  ▼
  dist/lessons/*.json + acceptance ledger
```

### Q11 — Governing the curriculum reviewer

**Decision. Deterministic signals + LLM advisory (mechanism), with thresholds calibrated from the
existing course (data).** Exactly the rubric-floor move, one level up:

- **Mechanism:** the LLM reviewer *proposes and explains* split/merge/delete; **deterministic thresholds
  decide** — objective count per concept (split), anchor-overlap % between concepts (merge),
  min-objectives floor (thinness/delete) — plus **coverage completeness** (every competence goal the
  researcher cited maps to ≥1 concept) as the curriculum-level "clears the bar" check.
- **Threshold values:** seed/calibrate them from the **existing 104-concept course** (`structure.json` +
  `concept_requirements/*`) — the empirical distribution of objective/anchor counts and overlaps is the
  starter dataset, just as golden lessons seed the rubric floors. For a brand-new topic with no existing
  course, fall back to defaults + the research-derived coverage bar (which generalizes).

So: load-bearing = deterministic + research-coverage; LLM = advisory; numbers come from the existing
course where one exists.

### Q12 — Model routing for the three curriculum subagents

**Decision. Reuse the `Agent`/`BackendStep` profile + fallback-chain pattern, DI-injected, with the
writer sharing the researcher's strong model:**

- **Researcher** — strong model (gpt-5.5-via-codex, best quality-per-$) **+ a web-search tool**
  (Exa/WebSearch), in a **bounded** research loop (cap N searches). The one genuinely agentic subagent.
- **Writer** — **also the strong model** (not the cheap tier): writing the course map *is* the
  high-leverage structural decision and benefits from the better model's knowledge.
- **Reviewer** — **reuse the existing `reviewer()` chain** (the calibrated pedagogy surface).

One model-routing discipline across the repo; spend concentrated on research+writing (the leverage
points); offline-testable by injecting fakes, like the QA graph's fixer/judge.

---

## Prerequisite work items (design resolved; these are build steps)

- **Promote the rubric floors** (`rubric_run --promote`) — the literal blocker for auto-accept-at-scale
  (Q9). Needs a trustworthy reviewer run.
- **Port the phase1 prompt craft** into the staged cold-author stages (Q6).
- **Derive curriculum thresholds** (objective-count / anchor-overlap / thinness) from the existing
  104-concept course (Q11).
- **Build `curriculum_design/`** — the standalone two-mode loop (Q1–Q4, Q10).

### Q8 — Keep the auto-repair loop?

**Decision. Keep it.** Blocking issues → `fix` (budget `K_GRAPH_MAX_FIXES=2`) → re-check → `regenerate`
(`K_GRAPH_MAX_REGENERATES=1`) → re-check; the convergence guard (`lesson_hash` + `blocking_fingerprint`)
escalates a stuck/no-progress loop to the human rather than burning budget. The author LLM fixes what it
can; the human handles only what it can't. Extends phase1's generate→gate→human-read; the human still
sees everything at the gate.

### Q7 — Keep the three-tier review trust split?

**Decision. Keep three-tier exactly.** (1) Deterministic gate = hard blockers; (2) LLM issue-lists =
advisory/log-only (calibration: precision ~0.25, they over-flag); (3) rubric-floor **scores** =
load-bearing when promoted; human arbitrates nuance at the gate. This is a superset of phase1
(deterministic gate + independent answer reviewer + human read): phase1's answer reviewer survives as
the blinded `answer_review` (advisory), and the rubric-floor score gate is the new layer phase1 lacked.
The full rationale is [CALIBRATION.md](CALIBRATION.md).

### Q6 — Anchoring "phase1 quality" when phase1's prompts are archived out of the repo

**Decision. Results-anchored + port prompts.** The durable, machine-checkable contract is: authored
output **clears the golden-derived rubric floors** — the goldens were the phase1 bake-off bar, so the
floors *are* "phase1-quality result" encoded. Method and prompts are then free to improve. As a starting
point, **port phase1's prompt craft** into each stage so the proven authoring bar isn't lost, then
iterate. (Guards against the "semantically thinner port" failure codex flagged.)
