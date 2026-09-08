---
type: Domain Decision
title: Authoring and Evaluation Decisions
description: Durable curriculum, exercise, and review choices that code alone cannot explain.
tags: [authoring, curriculum, evaluation]
timestamp: 2026-09-06
---

## Curriculum and practice

One catalog owner is one independently teachable learner decision. Approved catalog
inventory defines identity, outcomes, scope, exclusions, and prerequisites; the
curriculum plan orders it. Levels describe learner targets, not catalog units.
Topics and examples are inputs rather than competing units.

Approved curriculum follows one authoring path through independent review and human
acceptance. Parallel paths would create competing authority and inconsistent
approval. Automation assists that path; generated output remains review evidence.
See [source and publication boundaries](../project/boundaries.md).

Editorial sequence preferences apply only among dependency-ready A1 entries; the
catalog retains identity, CEFR, and dependency authority. Sequence provenance makes
a plan stale when preferences change. Lesson IDs remain stable for bookmarks and
progress while positions follow the plan. Exact order and per-lesson rationale
belong in `content/curriculum/sequence.yaml`.

The A1 opening uses supported exchanges before general grammar: supplied repair
requests do not imply independent question construction, and taught quantities do
not imply understanding arbitrary spoken prices. Later grammar generalizes those
fixed expressions. These are editorial judgments, not measured learning outcomes
or human pedagogy approval.

Exercises must stand alone so evidence does not depend on hidden context. Spoken
and written response modes are reusable wherever they serve the objective; tying
them to named lesson kinds would block useful practice. A checkable grammar claim
still requires evidence with determinable correctness. Interaction and assessment
remain learner-application responsibilities.

Pronunciation and spaced-review tracks remain outside the default curriculum until
their outcomes can be represented and assessed honestly. Text exercises cannot
establish pronunciation competence or schedule learner review; forcing those goals
into them would create hollow coverage. Reusable spoken responses remain available.

The final retrieval checkpoint tests transfer to a changed situation: communicative
outcomes use a new exchange, while grammar and phraseology use production or a
meaning-based choice in a new context. Provider choices remain separate from authored
content so the lesson-quality contract survives generation changes.

Practice builds from focused decisions toward the approved transfer outcome.
Retrieval can reinforce a target without repeating a complete transfer task under
a recap heading; length and operation counts are not quality goals. Exercise
authors keep visible tasks, bounds, criteria, and judge instructions consistent,
preserving allowed variants while enforcing explicitly requested forms. Examples
are not exhaustive answer lists. Feedback explains a target choice or likely error
when useful, rather than repeating the key or duplicating another feedback field.
Normalization preserves the reviewed teaching intent rather than repairing pedagogy.

## Trustworthy review and evaluation

Deterministic checks own claims with knowable correctness: parsing, schemas,
identifiers, source audits, routing, state transitions, and handoff invariants.
Model review advises on naturalness and pedagogy; humans retain final authority.
A closed-exercise semantic review receives deterministic opaque option identifiers;
the review operation retains a reversible in-memory mapping before comparing the
reviewer's answer with authored keys. Open write and speak tasks receive a separate
evidence review that inspects their prompts, evidence claims, and authored criteria
or targets without entering deterministic answer comparison. Reviewer findings also
cover factual premises, competing valid answers, distractor parallelism,
supplied-answer retrieval, reconstructed output completeness, and claims of
independent production. Those findings block the strict exercise gate for human
review even when the authored answer key itself can be solved.
A test is useful when it protects observable behavior with an independent oracle
and a realistic failure signal, not because it increases test count or repeats
implementation details. Contributor mechanics and the semantic test-review rubric
live in `AGENTS.md` and `scripts/test_review_prompt.md`.

Pytest owns deterministic behavior and workflow handoff integration. The committed
exercise-quality corpus preserves label provenance and adversarial categories;
its offline evaluator reports semantic cases as `not_evaluated` rather than claiming
model-quality metrics. Opt-in Promptfoo suites evaluate individual production
prompts against representative and adversarial inputs. Prepared review/edit inputs
isolate prompt quality; repair application and full handoff remain workflow tests.
Production and evaluations share prompt builders, including structured-schema
instructions, so they measure the same first-attempt prompt.

Assertions should isolate dimensions whose failures lead to different diagnoses.
Binary rubrics are gates, not calibrated numerical quality scores. Judge calibration
uses positive/negative minimal pairs, provenance, and held-out cases. The committed
calibration labels are a seed, not a validated golden set: a qualified Norwegian
language/pedagogy reviewer must confirm them before they measure judge accuracy.
A model judge is never its own sole oracle; pair it with deterministic checks and
human-audited samples. Review findings distinguish evidence of a defect from
missing context or a stylistic preference. Required prerequisites depend on what
the learner must already do without supplied scaffolding, not ordinary textbook
order. Provider policy prompts defer to each stage's exact output contract so
research evidence and review axes do not acquire competing requirements.

Evaluation evidence must distinguish invalid responses, provider outages, missing
IDs, and unevaluated cases from passes. Retain corpus/configuration identity,
category coverage, model route, completeness, repeatability, latency, token use,
and available cost; missing pricing is not zero cost. Confusion metrics and
thresholds require a validated labeled corpus and repeated baselines. Call/token
budgets belong in configuration. Live evaluation is opt-in and does not become an
ordinary deterministic pull-request gate. Exact commands, suites, cases, and adapter
mechanics are owned by `package.json`, `evals/promptfoo/`, and the evaluator code.
