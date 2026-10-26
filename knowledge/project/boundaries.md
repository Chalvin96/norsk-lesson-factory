---
type: Architecture
title: Authoring, Distribution, and Release Boundaries
description: Ownership of lesson source, workbench decisions, distribution, release, and learner runtime.
tags: [architecture, authoring, distribution, release]
timestamp: 2026-10-22
---

Human-edited lesson source is authoritative; published artifacts are derived.
Authoring defines meaning, evidence, answers, feedback, transcripts, and model
audio. The learner application owns presentation, interaction, playback, and scoring.

## Workbench runtime

The workbench is a React app served by a loopback JSON API. It opens a working
draft when present, otherwise canonical source. The first edit creates a complete
draft copy. Edits affect `plan.md`, `lesson.md`, or `exercises.yaml`; Save commits
draft files without changing canonical source, publishing, or calling providers.
Save, Promote, curriculum save, and new-lesson creation require a working
branch. Detached HEAD errors use the same English precondition message;
new-lesson requests fail before starting a provider job. A busy startup port exits
with status 1 and explains the `--port` and `--port 0` choices.

Chat, curriculum chat, and Fix use a supervised Codex app-server. Chat is
read-only and returns checked proposals for explicit acceptance. Generation,
regeneration, new-lesson drafting, and AI review use OpenCode pipeline jobs and
return proposals or drafts without silently changing canonical source.
Regeneration checkpoints in SQLite. A run waits only after its owner interrupt
is checkpointed; a crash between review and that interrupt remains recoverable.
Accepting or rejecting the proposal resumes the owner gate.

Job failures put an English sentence in `error` and the original exception in
`error_detail`. Provider names come from the configured model or Codex client.
Review and new-lesson errors show the sentence first with a keyboard-operable
Details disclosure. Drafting errors include the first validation reason; repair
prompts include the complete candidate and errors from the last attempt.

Navigation guards protect changed block-editor fields and staged curriculum
edits across links, lesson switching, history, and unload. The source editor
writes on navigation rather than joining that staged-work guard.
The minimap is a keyboard scrollbar: arrows move through the document, Page Up
and Page Down move by a screenful, and Home and End move to either end. Its visual
marks are hidden from assistive technology.

### Provider isolation

Codex uses a private `CODEX_HOME` per app-server generation. Persisted baseline
digests and generation ownership guard credential writeback. Startup recovers an
owner's refreshed credentials only while the parent matches its baseline, stops
a locally superseded owner before seeding the next generation, and reclaims
profiles whose owner exited. Parent changes retire the owner; missing parent
authentication discards orphan credentials.

OpenCode isolates each call's state, temporary files, and selected-provider
credentials. Cache assets and installation locks are shared. Cold calls drain
warm readers; warm calls run in parallel. Locked pending records and installation
inventories support recovery after abrupt exits. Child-held locks protect state
and prompt directories; startup removes unlocked leftovers. Provider refresh is
serialized, and writeback merges only that provider into the latest parent
credentials. The [LLM transport guide](../../src/lesson_builder/clients/llm/README.md)
owns cache coordination, recovery, and configuration details.
Both clients re-read before atomic credential replacement; external writers
that ignore their locks can still race that replacement.

### Settings and plan inspection

Model, effort, review-scope, and audio-cap overrides live in ignored workspace
state. Chat defaults come from Codex settings in `config.yaml`. Generation and
lesson/exercise review use `workbench_author` and `workbench_reviewer` jobs.
Reset removes workspace overrides.

The plan stage shows title, CEFR and tags, outcome, objectives, teaching points,
coverage, success criteria, exclusions, kind, family, terminology, and both
prerequisite lists as author fields. Structured items display their statement,
claim, or text; other objects use key/value rendering. Well-formed coverage shows
claim, scope, and evidence kinds, leaving ids and objective references in source.
Malformed coverage uses key/value rendering. Unknown fields with meaningful
values join author fields; empty ones remain technical. Package version,
identities, CEFR basis, source owners and hashes, and `approved` provenance stay
in a collapsed Technical fields disclosure.

An absent or blank family displays as Unassigned, an approvable choice without
a family gate. New-lesson drafts use that choice with a provisional flag.
AI review operates on lesson blocks; the plan stage explains that scope.

## Curriculum editing

The curriculum editor supports reorder, manual add, remove, merge, split, and
entry update. Each edit is previewed against the staged plan before acceptance.
Manual add creates a slot without a package. Save checks that the preview still
matches current source and requires missing prerequisites to be repaired. It
shows dependency-order findings without requiring per-finding attestations.
Check and release reject prerequisites placed after their dependents. A new
lesson creates a draft package and slot; promote it before standard export or
release can use it.

Concurrent new-lesson jobs preserve both drafts when intervening edits only
insert entries and preserve existing entries and order. Saving reapplies the
insertion to current source with a live anchor and free slug. Rollback restores
that source on failure. Other edits, removed anchors, occupied slugs, and
unparseable concurrent YAML cause conflicts without partial drafts or overwrite.
The [drafting contract](../pipeline/authoring.md#new-lesson-drafting) describes
exemplars, server-owned metadata, teaching points, and size guidance.

New-lesson dialogs suggest ASCII slugs with Norwegian transliteration until the
author edits the slug. Closing a dialog retains its job and submitted inputs.
Separate dialogs retain separate jobs. Running and failed jobs have compact
toolbar indicators with truncated titles and stages. Failures offer the English
error, Retry, and Edit inputs. Completion refreshes the curriculum, selects the
new row, and announces the lesson title once in a persistent polite live region,
including across navigation and while the success dialog remains open.

Session storage retains outstanding job IDs and inputs for polling after reload.
Restored failed or missing jobs become recoverable errors once and clear their
stored IDs. Missing jobs explain that a server restart may have lost the job;
Retry starts a new job. Errors remain in memory until dismissed, retried, or
edited. Completed jobs lose their indicators; cancelled sessions leave memory.
Edit inputs preserves values across navigation. Unavailable session storage does
not interrupt in-app tracking. Source badges appear once per row; the lesson
switcher shows curriculum positions while lesson source loads.

A pristine page follows server order. A staged order keeps surviving lesson IDs,
drops removed IDs, and appends new IDs in server order. Outside an accepted
preview, metadata comes from current curriculum data. Refresh failures keep the
workspace mounted and staged edits available, with an error. Rows show open AI
findings from the curriculum payload. Deterministic counts load separately from
`GET /api/curriculum/checks` after rendering; they use the lesson page's cached
preview count of source findings plus compile errors.

Removing an entry keeps its package unless the author selects an eligible
never-promoted draft for deletion. Merge keeps the earliest selected ID and
relinks dependents to it. Split keeps the original ID for its first part, chains
later parts, and relinks dependents to the last part.

## Save and promotion

Git commits and PR review record acceptance. The workbench has no separate
approval step, stage receipts, committed source snapshots, or approval evidence.
AI review is optional and its findings are advisory. Current block hashes still
identify which source a review covered; editing source invalidates that coverage
without blocking promotion or release.

Promotion requires a draft, passing deterministic checks, settled proposals,
complete current audio, no open audio findings, and canonical source unchanged
since drafting. It copies the package to canonical source, refreshes
provider-free `dist/`, and commits source and changed distribution together.
Standard export and release validate the complete planned canonical inventory.
Direct canonical edits use the same validation and Git review without receipts.

After accepting a proposal, the author can review uncovered blocks. Review
history retains local finding decisions. Owner resolutions and stale-exercise
keep decisions require a typed reason and allow a comment of up to 500
characters. Review prompts include prior decisions as advisory context; changed
evidence may reopen a finding. Decisions stay outside learner content and scoring.

## Exercise alignment

Exercise `derived_from` references compare a draft's exercises with canonical
lesson sections. Editing, adding, or removing a referenced section marks the
exercise stale. Editing exercise YAML alone does not clear that comparison.
Regenerate or keep with an owner reason to capture the current section versions
in ignored local records. Local decisions apply only to drafts started from the
same canonical package; a new canonical version cannot reuse an old decision.
Earlier decisions remain visible in history. Records whose canonical digest
does not match the draft, including legacy records without that digest, cannot
clear staleness until the author confirms a current decision.
For a never-promoted lesson, the draft's creation timestamp identifies eligible
decisions, so recreating the same lesson ID cannot reuse a previous draft's pins.
Section text in local captures supports exact before/after comparisons during
regeneration. No comparison snapshots are committed.

A fresh clone compares against canonical source without needing local records.
Exercises without references are untracked; a new exercise without a canonical
version or local capture has unknown alignment. Alignment is authoring guidance,
not a release gate.

## Audio and distribution

Workbench audio generation is explicit. Its cost plan captures clip text, reuse,
estimated spend, monthly spend, and cap. Confirmation requires unchanged source
and clips. Synthesis validates audio into the shared local cache and records
author checks without publishing.

Packets carry the authored `kind`: `grammar`, `phraseology`, `communicative`,
`pronunciation`, or `writing`. Export and author preview require explicit plan
kind. Internal projection defaults to grammar only when kind is omitted;
empty or unsupported kinds fail. The packet schema requires kind.

Packets contain learner content, authored order, exercises, and ready audio
references. They omit authoring requests, approval evidence, provenance, provider
voice data, and learner-runtime behavior. `dist/catalog.json` indexes packets
with distribution metadata such as position. Dialogue remains structured
stimulus data; consumers own its visual treatment.

The [release runbook](../README.md) owns planning, synthesis, upload, publication,
and verification. Standard releases require valid canonical source and complete validated audio, separate from promotion and workbench synthesis.

## Request and path safety

Each lesson has at most one open proposal. Proposals are checked against captured
source; conflicts can only be rejected. Applying source changes requires an explicit proposal decision.

The server validates Host and any Origin against its bound authority. Writes
require JSON and the workbench request marker. Lesson IDs and source paths stay
inside workspace roots. Authored text cannot inject markup into the workbench.

Safe unknown page URLs return the app shell with HTTP 404. API and audio requests
keep their resource responses. Malformed lesson paths, assets, static paths, and
unsafe components keep plain errors. A missing app build returns the rebuild
page with HTTP 503, including for safe unknown page URLs.

The [design system](workbench-design-system.md) owns frontend tokens and components.
