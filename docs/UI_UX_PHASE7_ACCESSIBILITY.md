# UI/UX Phase 7 Accessibility Report

## Scope

The audit covered authentication, dashboard, projects, datasets, analysis,
workspace modes, layers, measurements, disaster screening, flythrough, export,
reports, dialogs, drawers, and downloads. It combines source review, keyboard
flows, existing component tests, and real-browser semantic checks. This is not a
WCAG certification.

## Keyboard audit

- Login and registration begin with logical email/password/action order.
- Dashboard/project links, project forms, dataset controls, analysis controls,
  workspace mode and panel controls, layers, report actions, and downloads are
  native links/buttons/form controls.
- Active tools/layers expose pressed or selected state; visibility remains a
  separate checkbox.
- Native details/summary provides keyboard calibration disclosure.
- Dialog tests verify focus trap, Escape dismissal, safe initial focus, and
  focus return.
- Collapsible rails and results expose `aria-expanded`/`aria-controls` and do not
  remove the terrain viewport.
- Existing focus-visible styles remain globally active. No keyboard trap was
  found in the audited paths.

## Semantic audit

Authentication now uses a `main` landmark. Project Name/Description and Analysis
dataset/reference fields have native label associations. Browser automation on
authentication, dashboard, Analysis, Workspace, and Reports checks visible
buttons, links, inputs, selects, and textareas for accessible names, verifies a
main landmark, duplicate IDs, and image alternative-text presence.

Workspace regions retain named Layers, Terrain viewport, Inspector/tools,
quality, and Results landmarks. Job/report transitions use bounded status/live
regions; continuous progress and animation-frame values are not announced.

## Focus and async behavior

Loading routes expose status text. Busy form/report controls use native disabled
state and/or `aria-busy`. Terminal analysis/report states are polite atomic live
regions. Failures use alert/assertive behavior only where immediate attention is
required. Polling ticks are not separate announcements.

## Contrast and typography

Important state always includes text. Scientific ramps were not recolored.
Low-contrast slate-400 metadata on white cards was moved to slate-500 where
identified. Explicit 10-11 px operational text was normalized to the 12 px
metadata token. General content remains proportional; coordinates, CRS and
diagnostic identifiers use technical/tabular styling selectively.

## Responsive accessibility

At 390 and 412 pixels the map remains available, rails/tools remain reachable
through named controls, quality text wraps, results remain a bounded region, and
download actions stack. Touch layouts preserve control heights instead of
shrinking typography. No document-level horizontal scroll was measured.

## Automated checks

- Focused Phase 7 Playwright: 2 passed, including the seven-width semantic and
  keyboard audit.
- Phase 7 browser semantic/responsive audit: authentication, dashboard,
  projects, Analysis, Workspace and Reports.
- Phase 2 auth browser regression: labels, errors, busy state, protected routes,
  reduced motion and narrow viewports.
- Shared UI Vitest coverage: buttons, fields, icons/tooltips, tabs, dialog focus,
  notices, loading/data states and reduced motion.
- Workspace/component coverage: keyboard layer selection, panel controls,
  measurement tools, flythrough controls and status semantics.

No violations were suppressed and no assertions were weakened.

## Remaining limitations

- Canvas terrain interaction cannot provide a fully equivalent nonvisual spatial
  editing experience; adjacent controls and textual state remain available.
- Browser/OS combinations may expose different MediaRecorder capabilities.
- Automated name/landmark checks supplement, but do not replace, assistive
  technology testing with NVDA, JAWS, VoiceOver or TalkBack.

