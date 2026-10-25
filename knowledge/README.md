---
type: Operations
title: Release Operations Runbook
description: Plan, approve, publish, and verify an audio-complete lesson release.
tags: [release, operations, ci]
timestamp: 2026-10-22
---

Standard release synthesis and publication run from an operator's local
workspace. Tag CI verifies the published release without synthesizing audio.
[Workbench audio and promotion](project/boundaries.md#audio-and-distribution)
prepare local artifacts separately from release.

## Plan and publish

1. Create the release tag for the source to publish. Approval requires the tag
   to identify the checked-out source commit locally and on GitHub.
2. Run `uv run lesson-data release`. This provider-free step validates the
   canonical source and prerequisite ordering, records a source-bound plan,
   and reports reused audio, missing recordings, estimated cost, and the exact
   execution command. No lesson approval files or separate review gate are required.
3. Review the audio reuse and cost summary. Continue only with the printed
   `release --approve` command. Approval resumes an unchanged plan, reuses
   unchanged recordings, synthesizes only missing audio, validates complete
   audio coverage, uploads serving audio, and then publishes the packet archive
   without WAV bytes. `--max-cost` can cap the approved synthesis estimate.

The plan discovers valid recordings in the shared cache and scratch
exports. Its source digest covers the curriculum plan, audio configuration,
character registry, and canonical lesson source. The plan also binds the release
tag and digests of selected reusable recordings. Changed source or audio checks
require a new plan.

## Tag verification

The release workflow runs:

```bash
uv run lesson-data release-verify --tag vYYYY.MM.DD
```

Verification downloads the published archive, safely extracts it, checks every
packet against tagged source, and requires ready HTTPS audio references with
SHA-256 digests. It fails if the release or archive is missing, the archive
contains WAV bytes, or packet content differs from source. If tag CI starts
before local approval has published the archive, rerun verification after
publication completes.
