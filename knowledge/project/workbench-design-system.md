---
type: Architecture
title: Workbench Design System
description: Frontend stack, visual tokens, components, and learner inspection.
tags: [architecture, workbench, frontend, design-system]
timestamp: 2026-10-22
---

The workbench uses Vite, React, TypeScript, Tailwind CSS, Radix primitives,
shadcn-style components, class-variance-authority, and Lucide icons. Primitives
live in `workbench/src/components/ui/`; composites live in
`workbench/src/components/workbench/`.

## Theme and controls

`workbench/src/styles/tokens.css` defines the theme;
`workbench/src/styles/workbench-overrides.css` owns workbench contrast adjustments.
`workbench/src/styles/index.css` bundles DM Sans and Outfit through Fontsource.

Dialogs use the primary 80 color step with white text and opaque hover fills.
Light-mode destructive actions use the destructive 80 step with white text;
badges have no hover tint. Dialogs have an opaque popover surface, default
border, 16px corners, and dark scrim. Their controls use the popover fill and
strong border; controls elsewhere use their own fill and border. Focus rings
preserve border width; invalid controls use destructive colors. Labels are
semibold; descriptions and helper text use muted foreground.

Outline and secondary buttons have distinct styles. Dialog Cancel uses
`dialogSecondary`: popover fill, strong border, and default text. Disabled dialog
primary actions keep readable labels; the lesson toolbar uses its standard
disabled opacity and primary fill. Shared headers center below `sm` and align
left above it. New lesson keeps left alignment and full-width mobile actions.
Dialog select values truncate. Dark mode has distinct opaque dialog and page
surfaces, a visible ring, and a primary hover fill no darker than its base.
Audio estimate summaries use a dark-mode fill and muted helper text.

New lesson keeps unavailable Create lesson focusable with `aria-disabled` and a
description of the first invalid field. Activation focuses that field without
sending a request. Slug errors share one description with the action and do not
announce on every edit.

## Layout and previews

On narrow screens, curriculum rows place title and status chips below selection,
reorder, and action controls. The stacked lessons pane leaves room for multiple
rows; the page scrolls to entry and chat panes. Desktop uses three panes.
Storybook includes a 320 px curriculum preview with long titles and canonical,
draft, and undrafted entries, plus page and component previews.

Learner view in `workbench/src/lesson/document/render/learner/` is a read-only
snapshot of Flyt presentation without session or API state. Its header records
the source commit and paths. Refresh the copy when Flyt presentation changes and
update that header. Unsupported packet kinds point to Author view. Flyt owns
shipped presentation and interaction; the workbench's
[authoring boundaries](boundaries.md) define its inspection role.
