---
name: verify
description: Use when a workbench rendering, template, style, or interaction change needs browser evidence at desktop and mobile widths, including lesson routes, URL modes, or history behavior.
---

# Verify a workbench UI change

Use the repository snapshot tool as the browser proof for a workbench UI change.
It copies the workspace, gives the server private HOME and XDG directories, starts
the loopback workbench, and writes ignored screenshots under `reports/`.

## Steps

1. Install the Chromium browser once per machine when it is missing:
   `uv run playwright install chromium`. Completion: Chromium launches from the
   repository's uv environment.
2. Choose a representative route. Use `--lesson <id>` for a lesson page or
   `--route <path>` for the curriculum or settings page. Add `--mode` or `--view`
   when the changed state is selected by URL parameters. Completion: the command
   names the route and any state needed to reproduce the change.
3. Run `uv run python scripts/workbench_snapshot.py` with the chosen options. Add
   `--history` when navigation or guarded back/forward behavior changed.
   Completion: the command prints PASS for both viewport pages and both overflow
   checks, plus the requested history checks.
4. Open every printed screenshot and inspect the actual rendered state at 1280 px
   and 320 px. Check authored content order, author-only disclosures, clipping,
   focus, headings, language scoping, contrast, and affected findings,
   unavailable-audio, malformed-source, empty, error, reload, or recovery
   states. Use temporary invalid-source fixtures so canonical lessons stay
   valid. Completion: each changed surface has visual evidence and no
   unexamined screenshot remains.
5. Report the exact lesson or route, options, command result, and screenshot paths.
   Completion: a reviewer can rerun the command and open the evidence without
   using a real home directory.

## Representative commands

```text
uv run python scripts/workbench_snapshot.py --lesson greet_and_introduce_yourself
uv run python scripts/workbench_snapshot.py --route /settings --history
uv run python scripts/workbench_snapshot.py --lesson greet_and_introduce_yourself --mode draft --view lesson
```

This skill proves the authoring workbench rendering and interaction surface. It
does not define learner-app presentation or interaction behavior.
