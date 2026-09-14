---
type: Operations
title: Release Operations Runbook
description: How a release is planned, synthesized, published locally, and verified by tag CI without remote synthesis.
tags: [release, operations, ci]
timestamp: 2026-09-14
---

Releases are synthesized and published from one operator's local workspace,
where the ignored `store/cache/v4-audio` cache and historical scratch exports
live. CI never synthesizes: the tag workflow holds no audio-provider or storage
credentials, so it cannot spend or upload; it only verifies what local approval
already published.

## Local preparation and publication

1. Push the release tag first (`git tag vYYYY.MM.DD && git push origin vYYYY.MM.DD`).
   Approval reconciles the GitHub release with `--verify-tag`, so the tag must exist.
2. `uv run lesson-data release` writes a source-bound plan. The plan freezes the
   release tag and discovered GitHub repository along with the digest of every
   historical recording selected for reuse. Default output is a
   bounded summary: lesson and recording counts, reused versus missing audio,
   estimated synthesis cost, the plan path, and the exact approve command. The
   full machine-readable plan (including every candidate record) stays in
   `store/scratch/release/release-plan.json`; pass `--format json` to print it.
3. `uv run lesson-data release --approve --tag vYYYY.MM.DD --github-repository owner/repo`
   requires that tag to identify the checked-out source commit both locally and
   in the selected GitHub repository, resumes only an unchanged plan, reuses
   every unchanged historical recording through an atomic cache write,
   synthesizes only missing fingerprints, and optionally refuses the run with
   `--max-cost` when the estimate exceeds an explicit cap. It uploads serving
   audio to S3 before publishing the WAV-free packet archive to the GitHub
   release. Approved output stays bounded; candidate detail remains in the plan
   file.

## Tag CI verification

The `release` workflow (tag push, or `workflow_dispatch` with a tag input)
checks out the tagged source and runs
`uv run lesson-data release-verify --tag <tag> --github-repository owner/repo`.
Verification downloads the published `lessons.tar.gz` (packet JSON only, no WAV
bytes), safely extracts it, proves every packet matches the tagged source
modulo audio, and requires ready HTTPS URLs plus SHA-256 digests on every audio
reference. It fails closed when the release or asset is missing, the archive
embeds audio bytes, or any packet drifted from source.

Because local approval can finish after the tag push races ahead, a verification
run may fail with "does not expose the asset"; re-run the workflow once local
approval completes.
