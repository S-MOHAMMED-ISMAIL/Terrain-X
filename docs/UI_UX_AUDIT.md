# TERRAIN-X UI/UX Audit

Status: read-only audit complete. No production frontend, backend, API, schema,
terrain calculation, or closed engineering item was changed.

## Audit method

The audit combined source review, route and component inventory, existing
Playwright acceptance flows, existing rendered screenshots, and a temporary live
browser probe at 1440x1000 and 390x844. The probe used the real local stack,
created and deleted one temporary project, and its temporary account was removed.
No mocked product state was used to support findings.

The application is functionally much stronger than its presentation suggests.
It already has honest pipeline stages, calibration gates, relative/metric
semantics, backend-authoritative coordinates, real 2D and 3D views, measurements,
flythrough, disaster screening, exports, and reports. The redesign should expose
that depth, not replace it.

## Executive findings

1. **The terrain workspace is not viewport-first.** At 1280px, a long 280px
   left rail contains layers and nearly every tool. The map/3D viewport occupies
   only the upper-right region while the page can exceed 4500px in height. Tools
   below the fold lose visual and spatial context with the viewport.
2. **Project tabs over-promise navigation.** Terrain, Measurements, and Disaster
   all render the same `TerrainWorkspace` without passing the selected tab as a
   tool-focus state. The explanatory copy says the focus changes, but the
   interface remains in the same arrangement.
3. **Scientific honesty exists, but is buried in prose.** Relative versus metric,
   calibration rejection, residual scope, and screening limitations are correct,
   often in 10-11px paragraphs inside narrow cards. Trust-critical state should
   be legible at a glance, with details progressively disclosed.
4. **The visual system is only partially unified.** Shared Button, Card, Badge,
   EmptyState, ProgressBar, and Spinner primitives exist, but terrain controls,
   errors, inputs, iconography, tool buttons, and export controls are frequently
   bespoke. White cards on a dark animated background make operational screens
   feel like assembled demo panels rather than one instrument.
5. **Responsive behavior is incomplete.** The live 390px audit measured a 442px
   document width on dashboard and project workspace. The brand wraps, global
   navigation crowds the header, and the full desktop tab set is forced into a
   narrow layout. A phone should be a deliberate review/status experience, not a
   squeezed terrain workstation.
6. **Accessibility has a sound start but material gaps.** Global visible focus
   and reduced-motion handling are good. Login/register labels are not
   programmatically associated with inputs; many icon-like controls use title
   text instead of robust tooltips; small text and low-contrast secondary copy
   are pervasive; status changes lack explicit live-region behavior.
7. **Performance practices are mixed.** Map and Three.js lifecycle code includes
   cleanup, stable refs, visibility handling, and active-only polling. However,
   the dashboard performs `1 + 2N` API calls for N projects, the global decorative
   canvas maintains an animation loop, and the 1210-line TerrainWorkspace owns a
   broad state/effect surface that can rerender many controls together.

## 1. Current UI architecture

The React 18/Vite application uses React Router, Tailwind, Leaflet, Three.js, and
Recharts. There is no external component or icon library.

- `AppShell` owns the dark global header, breadcrumb, decorative canvas, and a
  centered `max-w-6xl` content area.
- `AuthContext` owns token-backed session state and `/auth/me` hydration.
- `ProjectWorkspacePage` owns six in-memory tabs: Datasets, Analysis, Terrain,
  Measurements, Disaster, and Reports.
- Terrain, Measurements, and Disaster are aliases to one retained
  `TerrainWorkspace` instance.
- `TerrainWorkspace` coordinates dataset selection, visualization context,
  layers, map/3D mode, inspection, measurement modes, residuals, terrain
  controls, flythrough, export, history, and disaster controls.
- API access is centralized in `api/client.ts`; server state is otherwise held in
  local component state. There is no query cache or global project workspace
  store.
- Shared UI primitives cover basic cards/actions/status, but not forms, panels,
  tabs, tooltips, dialogs, notices, drawers, or tool controls.

## 2. Current page inventory

| Route/surface | Current purpose | Main states observed |
| --- | --- | --- |
| `/login` | Existing-user authentication | idle, submitting, inline error |
| `/register` | Account creation | idle, submitting, inline error |
| `/dashboard` | Aggregate counts and recent projects | skeleton, empty, populated, error |
| `/projects` | Create/list/delete projects | loading, empty, populated, create error |
| Project / Datasets | Upload and manage source/DEM/GCP data | role selection, drag/drop, upload progress, validation status |
| Project / Analysis | Configure depth, calibration, semantic analysis | empty prerequisites, submit, staged polling, history, failure |
| Project / Terrain | Select dataset and explore layers in 2D/3D | loading, relative, metric, map, mesh, errors |
| Project / Measurements | Alias of terrain workspace | inspect, point, coordinate, distance, profile, slope, save/history |
| Project / Disaster | Alias of terrain workspace | unavailable, configure, running, completed, failed |
| Project / Reports | Generate and download outputs | prerequisites, pending/generating, completed, failed |

Flythrough, path editing, playback, recording, GLB export, residuals, legends,
and measurement results are cards inside the terrain workspace rather than
independent pages.

## 3. Current user flows

### Primary golden path

Register/sign in -> create project -> upload source image -> optionally upload
DEM or GCP reference -> configure analysis -> follow real pipeline stages ->
select dataset in terrain workspace -> inspect relative or metric results ->
switch 2D/3D -> measure or screen -> save measurements -> export/report.

### Relative-only path

Source image -> analysis without accepted calibration -> relative depth and
visual terrain remain available -> metric elevation, DSM, DTM, nDSM, metric
measurements, and disaster screening remain unavailable. Current semantics are
correct, but the user must parse several disabled cards and notices to understand
the consequence.

### Calibrated path

Source plus reference -> quality gate passes -> metric elevation/DSM and
estimated DTM/nDSM become available -> metric tools and screening unlock ->
residual diagnostics explain agreement and limitations.

### Export path

GLB export is embedded in terrain controls. PDF/JSON/CSV/ZIP generation is on a
separate Reports tab. Path JSON and WebM recording live in flythrough controls.
The feature split is technically meaningful but lacks one discoverable export
model.

## 4-8. Findings and recommendations

Each major recommendation records the required current/proposed/why/risk frame.

### A. Rebuild the workspace around a persistent viewport

**CURRENT:** A two-column grid places every layer and tool in a long left rail and
the viewport at the upper right. Scrolling to measurement, disaster, path, or
export controls leaves the viewport behind. Disabled layer cards consume nearly
the same height as available layers.

**PROPOSED:** Use a bounded workspace shell below a compact project context bar:
collapsible Layers rail (left), persistent 2D/3D viewport (center), contextual
Inspector/Tools rail (right), and a resizable bottom drawer for profiles,
measurement history, job details, and tabular results. Keep the viewport visible
while tools change. Disabled/unavailable layers become compact rows grouped under
"Unavailable" with one causal explanation.

**WHY:** Terrain is the product's central object. Keeping it visible makes layer,
measurement, residual, disaster, and flythrough actions spatially comprehensible
and substantially reduces page length.

**RISK:** Restructuring can remount Leaflet/Three.js or lose transient state.
Preserve the current retained workspace instance, stable refs, backend coordinate
resolvers, object-URL cleanup, and D1/D2 browser assertions. Implement layout
around existing engines before changing their internals.

### B. Replace alias tabs with explicit workspace modes

**CURRENT:** Terrain, Measurements, and Disaster tabs render the same component,
but `activeTab` is not passed into it. The tab label changes while tool focus does
not. The user sees six peer tabs even though three are views of one workspace.

**PROPOSED:** Keep project-level navigation to Data, Analysis, Workspace, and
Reports. Inside Workspace, use an explicit mode/tool switcher for Explore,
Measure, Screen, and Flythrough. Selecting a mode opens the relevant right panel
without resetting dataset, layer visibility, camera, or map extent. Make route or
query state deep-linkable after behavior is stable.

**WHY:** Navigation should describe actual state transitions. This reduces
duplicate concepts and makes the integrated-workspace model visible rather than
explaining it in a paragraph.

**RISK:** Existing tests locate old tab labels. Migrate selectors and navigation
tests deliberately; do not alter measurement or disaster APIs.

### C. Elevate data truth into a persistent status model

**CURRENT:** Relative/metric semantics and calibration limitations are accurate
but repeated in dense layer notes and amber paragraphs. Active representation,
units, calibration state, and inspectable layer are not summarized together.

**PROPOSED:** Add a compact, persistent DataQualityBar above the viewport:
`Relative / unitless` or `Calibrated / metric`, active surface name, verified
vertical unit, CRS/georeference status, and calibration outcome. Clicking opens a
diagnostics panel with gate criteria, held-out/in-sample distinctions, residuals,
and limitations. Never use success color for unvalidated scientific accuracy.

**WHY:** These states determine what every visible value means. They must survive
mode changes and remain visible during measurement and export.

**RISK:** Simplified labels can overclaim. Derive every label from current API
fields and preserve the full backend wording in details/report output.

### D. Make layers compact, comparable, and controllable

**CURRENT:** Each layer is a white card containing title, checkbox, opacity,
legend, dimensions, CRS, notes, and warnings. Active selection is a border. Long
notes create a multi-thousand-pixel rail.

**PROPOSED:** Use `LayerRow` with semantic swatch, visibility icon, active radio
behavior, name, units/status, opacity popover, and overflow menu. Put legend in a
dedicated contextual panel tied to the active layer. Put dimensions, CRS,
provenance, and full notes in Layer Details. Group imagery, elevation surfaces,
derivatives, classifications, and unavailable outputs.

**WHY:** Users primarily scan, compare, show/hide, and activate layers. Metadata
remains available without dominating the repeated row.

**RISK:** Visibility and active-layer selection are distinct today. Preserve both
states and clearly indicate which visible layer is currently inspectable.

### E. Consolidate tools and commands

**CURRENT:** Measurement modes, view angles, flythrough path actions, playback,
recording, residual toggles, and export actions use several ad hoc button styles.
Many text buttons are small and visually equivalent despite different scope.

**PROPOSED:** Introduce icon-backed `ToolButton`, `Toolbar`, segmented controls,
and command menus using one icon library. The active tool gets `aria-pressed`, a
strong persistent highlight, cursor feedback over the viewport, and a short
context instruction. Escape exits transient tools; destructive actions require a
confirmation dialog where data loss is meaningful.

**WHY:** A professional spatial tool needs unmistakable modes and predictable
activation/deactivation.

**RISK:** Keyboard shortcuts and icons can be undiscoverable. Provide tooltips,
visible active-state text, and no shortcut-only action.

### F. Clarify pipeline progress and failures

**CURRENT:** The real macro-stage tracker is a strong foundation. It uses tiny
labels and primarily appears in job history. Errors are often raw inline text;
failed calibration details are spread across cards.

**PROPOSED:** Promote the current real stage model into a vertical/horizontal
`ProgressStepper` with current stage, completed stages, optional stages, elapsed
time, and terminal outcome. Failure panels use: what happened, why it matters,
what remains available, and next action. Calibration rejection explicitly says
"Relative terrain available; metric elevation unavailable" with diagnostics.

**WHY:** Long-running work needs confidence without fake progress. Existing
backend stages already support honest presentation.

**RISK:** Macro grouping can imply unexecuted work. Continue deriving steps from
job parameters and actual stage/status exactly as `analysisPipeline.ts` does.

### G. Create one export model

**CURRENT:** GLB, path JSON, WebM, report generation, and PDF/JSON/CSV/ZIP are
distributed across workspace cards and Reports. Completion feedback differs by
feature.

**PROPOSED:** Add an Export menu in the workspace command bar. Group "Current
terrain" (GLB), "Flight" (path JSON/WebM), and "Project evidence" (report formats).
Complex report generation opens a compact task panel/history with queued,
generating, completed, failed, and download states. Keep Reports as the durable
history destination.

**WHY:** Exports stay discoverable without occupying persistent viewport space,
and users can distinguish immediate downloads from generated reports.

**RISK:** Combining commands may blur different prerequisites. Disable with a
specific reason and route users to the missing analysis/reference step.

### H. Repair responsive architecture

**CURRENT:** At 390px both dashboard and project workspace measured 442px wide.
The logo wraps, global navigation crowds the header, project tabs wrap into a
dense grid, and a full geospatial workspace would be impractical.

**PROPOSED:** Desktop >=1280 gets three workspace regions. Tablet 768-1279 keeps
the viewport primary and moves rails into mutually exclusive drawers. Phone
<768 provides project status, dataset/analysis monitoring, report downloads,
layer metadata, and a read-only map preview; advanced 3D editing, precise
measurement, path editing, and recording present an explicit larger-screen
requirement. Use a compact mobile app bar and overflow navigation.

**WHY:** Purposeful capability boundaries are safer than a cramped precision
tool. The live overflow defect must be eliminated on all routes.

**RISK:** Hiding controls can strand mobile users. Always expose status, results,
downloads, and a clear explanation of desktop-only precision workflows.

### I. Complete accessibility semantics

**CURRENT:** Focus-visible styling and reduced-motion overrides are global.
Several controls have useful `aria-*` state. Login/register labels are not linked
to inputs; tab buttons use `aria-current` instead of tab semantics; title-based
hints are mouse-centric; loading/status changes are not consistently announced;
10-11px copy is common.

**PROPOSED:** Associate all labels/fields with IDs, add FormField and described-by
error/help contracts, implement correct tab/toolbar/dialog semantics, add a real
Tooltip primitive, announce async completion/failure through restrained live
regions, maintain 44px touch targets, and set 12px as the minimum exceptional
metadata size with 13-14px default control copy. Test keyboard-only order and
contrast in every phase.

**WHY:** Precision tooling must be operable and understandable without relying on
hover, color, or pointer input.

**RISK:** Over-announcing polling updates creates noise. Announce stage changes
and terminal outcomes, not every poll response.

### J. Rationalize loading, empty, success, and deletion states

**CURRENT:** Skeletons and EmptyState exist, but empty terrain is plain text,
errors are repeated red boxes, successful uploads rely on list state, and project
deletion occurs immediately without confirmation. Skeletons can remain visible
briefly without an accessible loading label.

**PROPOSED:** Add shared `LoadingState`, `ErrorState`, `SuccessToast`,
`PrerequisiteState`, and `ConfirmDialog`. Empty states should contain the next
valid action. Destructive project/dataset/measurement actions require scoped
confirmation. Keep inline errors near the responsible control and a page-level
error only for page failure.

**WHY:** State feedback becomes predictable and prevents accidental loss.

**RISK:** Too many toasts/dialogs interrupt work. Reserve dialogs for destructive
or irreversible actions and prefer inline success for persistent results.

## 9. Performance observations

Positive existing behavior:

- Analysis, report, and disaster polling only run for active jobs and stop at a
  terminal state.
- The global canvas pauses with document visibility and responds to reduced
  motion.
- Leaflet/Three integrations use refs and explicit cleanup rather than React
  state for every animation-frame value.
- Binary imagery and terrain grids use authenticated blob/object-URL paths.

Observed risks:

- Dashboard loading is `1 + 2N` requests: projects, then datasets and jobs for
  every project. This scales poorly and duplicates server aggregation work.
- `TerrainWorkspace.tsx` is 1210 lines and owns many independent states/effects.
  Unrelated panel updates can traverse the entire workspace render tree.
- The ambient full-screen canvas runs on every authenticated route. It should be
  measured on low-power devices before being retained in dense workspaces.
- Layer previews may issue several image decodes/requests together when context
  changes. Preserve caching/object URL reuse and avoid remounting view engines.
- Full-page entrance animation is keyed by pathname; internal project tabs add
  further animations. Motion should not delay controls or invalidate map
  measurements.

Recommendations: add a dashboard summary endpoint only in a later approved
engineering change, split workspace state by concern, memoize panel boundaries
where profiling shows benefit, keep WebGL/Leaflet mounted across mode/tool panel
changes, and profile before changing render logic. T2 limits and backend behavior
remain untouched.

## 10. Proposed information architecture

```text
Global shell
  Dashboard
  Projects
    Project context
      Data
        Datasets
        Upload / validation
      Analysis
        Configure
        Running/history
      Workspace
        Explore (2D / 3D)
        Measure
        Screen
        Flythrough
      Reports
        Generation history
        Downloads
```

Workspace composition:

```text
Project / Dataset / analysis outcome / units / CRS                 Export
Layers rail       Persistent 2D or 3D viewport       Contextual tool panel
                  cursor + active-layer status
Resizable results drawer: profile / measurement / diagnostics / job details
```

The project and selected dataset remain stable across Workspace modes. The 2D/3D
switch is a viewport representation control, not top-level navigation.

## 11-13. Design, components, and motion

The detailed proposed design system is in `scratchpad/uiux/DESIGN_SYSTEM.md`.
Core direction:

- Quiet charcoal workspace chrome, neutral light data surfaces only where they
  improve reading, restrained teal for selection/primary action, and semantic
  green/amber/red/blue for status.
- Compact engineering typography with tabular numerals and monospace only for
  coordinates, CRS, IDs, and diagnostic values.
- Fewer cards; use rails, dividers, rows, drawers, and popovers for spatially
  related controls.
- Shared components: AppBar, ProjectContextBar, WorkspaceShell, Panel,
  StatusBadge, DataQualityBadge, FormField, SegmentedControl, Tabs, ToolButton,
  LayerRow, LegendPanel, ProgressStepper, LoadingState, EmptyState, ErrorState,
  Notice, Tooltip, ConfirmDialog, ExportMenu, MeasurementResult, and TaskHistory.
- Motion is 120-200ms for hover/selection, 180-240ms for panels, and disabled by
  reduced-motion preference. Never animate map geometry, measurement anchors, or
  camera state for decoration.

## 14. Terrain workspace redesign

The first viewport shows the selected dataset, active surface, units, calibration
state, and 2D/3D mode before secondary controls. Layers remain visible at left;
the selected tool appears at right. Inspection output anchors near the cursor and
in the results drawer. Profiles get the drawer's width instead of a narrow card.
Flythrough playback controls dock below the 3D viewport; path editing remains
available in both views. Recording state is visible at viewport level.

The viewport must maintain stable dimensions with a minimum usable height.
Panels scroll independently. Opening a tool must not resize the map unpredictably;
when a rail width changes, explicitly invalidate Leaflet size and resize Three.js.

## 15. 2D/3D interaction redesign

- One segmented 2D/3D control sits in the viewport header.
- Active layer/surface and units remain identical across representations.
- A shared cursor/selection model displays map coordinates and source pixel from
  backend-authoritative resolution; no frontend geospatial approximation is
  introduced.
- "Top" and "Perspective" are 3D camera controls, not peer navigation tabs.
- Texture availability explains D2 compatibility and remains disabled when the
  backend says geometry/texture do not correspond.
- Switching representation preserves the selected dataset, active layer, tool,
  and measurement result. Camera/extent linking is introduced only with explicit
  tested transforms.

## 16. Calibration and error UX

Success summary:

```text
Calibrated
Metric elevation available
Reference: DEM/GCP | unit | held-out skill | samples
[View diagnostics]
```

Rejected summary:

```text
Calibration rejected
Relative terrain available; metric elevation unavailable
The reference agreement did not pass the quality gate.
[View diagnostics] [Review reference inputs]
```

Diagnostics distinguish quality-gate criteria, held-out validation, in-sample
fit, outliers, and residual map scope. Error components always state what
happened, why it matters, what remains available, and what can happen next. Raw
stack traces are never rendered.

## 17. Export and report UX

The command bar exposes one Export menu with eligibility and generation state.
Immediate GLB/path downloads show local progress and completion. Worker-generated
reports create a task row that persists through navigation, continues real
terminal-state polling, and offers PDF/JSON/CSV/ZIP only when available. CSV's
absence is explained as "No tabular data available", not generic failure.

## 18. Implementation phases

### Phase 0: Baseline and guardrails

Capture desktop/tablet/mobile visual baselines; add accessibility smoke checks;
identify stable test IDs; retain D1/D2/D3, calibration, measurement, flythrough,
report, and T2 performance constraints. No behavior changes.

### Phase 1: Foundations

Implement tokens and shared primitives, adopt one icon library, fix form labels,
tooltips, focus semantics, notices, confirmation, and status patterns. Restyle
auth/dashboard/projects with no workflow change.

### Phase 2: Workspace shell

Build the persistent viewport layout around the existing TerrainWorkspace
engines. Introduce independent rails/drawer and responsive panel behavior. Keep
current state ownership until layout/browser regressions pass.

### Phase 3: Layers and data trust

Convert layer cards to grouped rows, add DataQualityBar and diagnostics, separate
legend/details, and make active versus visible explicit. Verify all relative and
metric labels and D1/D2 behavior.

### Phase 4: Tools

Move inspection, measurements, slope, residuals, disaster, flythrough, and export
into contextual tool panels and the result drawer. Preserve API calls and exact
coordinate/measurement semantics.

### Phase 5: Analysis and reports

Upgrade pipeline stepper, prerequisite/error states, report task history, and
unified export entry points. Do not invent progress.

### Phase 6: Responsive, accessibility, and measured performance

Enforce the phone capability boundary, remove horizontal overflow, complete
keyboard/screen-reader/contrast testing, profile rerenders and API calls, and
optimize only measured hotspots.

## First screen to redesign

**Redesign the Terrain Workspace first**, after the small Phase 1 primitive
foundation is available. It is the product's defining experience, contains the
largest usability and responsive problems, and provides the component patterns
that Analysis, Disaster, Measurements, and Reports should then reuse. Start with
layout and information architecture around the existing map/Three.js engines;
do not rewrite those engines as part of the visual redesign.

