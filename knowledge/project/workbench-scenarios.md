---
type: Operations
title: Workbench Scenarios
description: Implemented workbench states and routes for browser verification.
tags: [workbench, design, verification]
timestamp: 2026-10-22
---

The Penpot design file's `Workbench · *` pages hold the designs for these states.
Use the [workbench verification skill](../../.agents/skills/verify/SKILL.md)
for browser evidence at 1280 px and 320 px. Check affected states for horizontal
overflow, console errors, and unexpected requests. Use temporary fixtures for
invalid source. Inspection does not call providers; actions marked _provider_ do.

## Curriculum (`/`)

| State | How to reach |
| --- | --- |
| Default list with the selected entry and its plan findings | open `/` |
| Loading and unavailable | slow or failing `/api/curriculum` |
| Unsaved order with an entry open for editing | drag a row, then edit the entry |
| Save failed with check findings | save an order that breaks a check |
| New lesson: form, drafting, done, failed (_provider_) | "New lesson" |
| Merge, split, and remove entries | select entries, then "Merge…", "Split…", or "Remove…" |

## Lesson (`/lessons/<id>`)

| State | How to reach |
| --- | --- |
| Read and review modes; author, learner, and source views | `?mode=read`, default review, `?view=learner`, `?view=source` |
| Plan stage | `?stage=plan` |
| Lesson switcher open | the lesson picker in the header |
| Editing a block | open a block editor in review mode |
| Source write failed | in the browser, make the source save request return an error (for example, a Playwright route), then save |
| Cannot compile | a source file with a schema error |
| AI review failed (_provider_) | run AI review without a reachable provider |
| Proposal pending; agent working with proposal outcomes (_provider_) | ask chat for an edit |
| Stale exercises regenerated (_provider_) | regenerate after editing the lesson |
| Lesson audio with reused and estimated-cost clips | a lesson with missing audio clips |
| Loading, not found, unavailable | slow API, unknown id, failing API |

## Lesson dialogs

| Dialog | Trigger |
| --- | --- |
| Approve, promote | approve a reviewed lesson, then promote it |
| Canonical changed | the canonical source changes under an open draft |
| Discard draft | "Discard draft" in the toolbar while a draft exists |
| Leave with unsaved edits | leave the page or close a block editor with changed fields |
| Regenerate stale exercises, regenerate scope (_provider_) | regenerate from the toolbar |
| Resolve similar findings | resolve one of several matching findings |
| Keep exercise as is, with owner reason chips | "Keep as is" on a stale exercise or a proposal |

## Settings and system

| State | How to reach |
| --- | --- |
| Settings | `/settings` |
| Page not found | any other page URL (served as the app shell with HTTP 404) |
