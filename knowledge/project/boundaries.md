---
type: Architecture
title: Authoring, Distribution, and Release Boundaries
description: Why lesson source, approval, distribution, release, and learner runtime stay separate.
tags: [architecture, authoring, distribution, release]
timestamp: 2026-08-28
---

Human-edited lesson source is authoritative; published artifacts are derived. The
learner application owns presentation and interaction, so authoring can focus on
meaning and pedagogical intent without coupling itself to runtime behavior.

The local authoring preview is a read-only inspection surface over current source.
It reuses source parsing, deterministic audit, and public packet projection, binds
only to loopback, and keeps answers and review evidence author-only. It does not
publish, write source or distribution files, call providers, score learners, or
attach stale distribution audio to changed authored text.

The approved catalog owns lesson identity, outcomes, scope, and prerequisites. The
plan orders approved lessons. Scratch generation may propose or repair content, but a
human gate accepts the exact package before promotion; publication cannot silently
redefine the curriculum. Distribution export only projects promoted inventory into
`dist/`; it does not decide curriculum or learner behavior.

The public packet is intentionally one-way and lossy: it contains learner content,
authored order, exercises, and ready audio references, but not request notes, review
evidence, provenance, provider voice data, or runtime behavior. Audio is synthesized
at export; voice policy remains private and provider-neutral; missing audio has no
learner-facing pending state.

Intrinsic lesson data stays in the packet while `catalog.json` carries only distribution
metadata such as position and optional family/status. A separate manifest would
duplicate packet fields and turn packaging metadata into a second lesson contract.
Recurring voices are explicit registry-backed authoring data, never inferred from a
matching name; unresolved identity or profile conflicts fail closed before paid audio.

These boundaries prevent a serving distribution from becoming a second editable
contract. Promotion remains source-only and resumable; deterministic checks own
oracle-verifiable structure, model reviews advise on naturalness and pedagogy, and
humans accept. A distribution is validated as a complete staged tree and replaced
atomically, so failures cannot rewrite unrelated lessons or expose partial data.
External release publication packages and ships that already validated distribution;
it does not alter authoring source. Release publication is all-or-nothing and separate
from authoring, keeping failed or incomplete work from becoming current.
