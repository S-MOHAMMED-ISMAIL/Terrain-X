# UI/UX Phase 1 Baseline

Baseline date: 2026-09-28

Status: **PASS**. This document records the validated pre-redesign production
frontend. No production behavior or test assertion was changed in Phase 1.

## Route map

```text
BrowserRouter
  PageHeaderProvider
    /login                 LoginPage (public)
    /register              RegisterPage (public)
    ProtectedRoute
      AppShell
        /dashboard         DashboardPage
        /projects          ProjectsPage
        /projects/:id      ProjectWorkspacePage
    /                      redirect -> /dashboard
    *                      redirect -> /dashboard
```

Authentication is token-backed through `AuthProvider`; protected routing waits
for auth hydration and redirects unauthenticated users to `/login`.

The project route contains six in-memory tabs rather than nested routes:
Datasets, Analysis, Terrain, Measurements, Disaster, and Reports. Terrain,
Measurements, and Disaster all retain the same `TerrainWorkspace` instance.

## Layout hierarchy

```text
main.tsx
  BrowserRouter
    AuthProvider
      App
        PageHeaderProvider
          public auth page OR
          ProtectedRoute
            AppShell
              DynamicScientificBackground
              global header / nav / breadcrumb
              centered max-w-6xl main content
                routed page
```

`ProjectWorkspacePage` owns project/dataset loading and tab choice. It renders:

- Datasets: `DatasetUpload` + `DatasetTable`
- Analysis: `AnalysisPanel` + `PipelineTracker`
- Terrain/Measurements/Disaster: one `TerrainWorkspace`
- Reports: `ReportsPanel`

## Component architecture

### Shared UI

- `Button`: primary/secondary/danger/ghost variants.
- `Card`: shared white bordered container, optional hover treatment.
- `Badge`: neutral/info/success/warning/danger/brand status tones.
- `EmptyState`: bordered empty-state message/action.
- `ProgressBar`: real 0-100 value with progress semantics.
- `Spinner`: labeled async indicator.
- `AnimatedNumber`: dashboard count transition with reduced-motion handling.

Missing shared production primitives: FormField, ErrorState, LoadingState,
SuccessState/toast, Tooltip, ConfirmDialog, Tabs, SegmentedControl, ToolButton,
Panel/Drawer, LayerRow, and ExportMenu.

### Workspace and terrain

- `TerrainWorkspace` (1210 lines): dataset/context loading, layer state, map/3D
  mode, inspection, measurement orchestration, residual loading, terrain
  controls, flythrough/path state, exports, history, and disaster panel.
- `MapView2D` (276 lines): Leaflet lifecycle, base map, authenticated previews,
  overlays, residual points, click sampling, and waypoint display.
- `TerrainView3D` (1271 lines): Three.js scene/mesh/texture/cameras, raycast
  sampling, first person, terrain follow, path playback, recording canvas, HUD.
- `LayerPanel` + `Legend`: layer availability, visibility, opacity, active layer,
  scientific notes, and continuous/categorical legends.
- `CalibrationResidualsCard`: held-out/in-sample selection, legend, download,
  and limitations.
- `MeasurementResultPanel`, `ProfileChart`, `MeasurementHistory`: result and
  persistence UI.
- `DisasterPanel`: metric prerequisite, configuration, active-only polling, and
  flood/landslide summaries.
- `FlythroughPathCard`: waypoint editing, playback, speed, recording, path JSON.
- `MeshExportCard`: 256/512 GLB generation, texture choice, and parsed summary.

Pure helper modules isolate important behavior from React: analysis pipeline,
inspection, measurement modes/formatting, map clicks, terrain availability,
terrain coordinates/mesh/camera/exaggeration, flythrough/path/recording,
calibration residuals, mesh export, report status, dashboard counts, and tab
routing.

## Existing automated coverage

### Vitest

18 test files / 204 tests. Coverage is concentrated on pure logic:

- analysis macro-stage mapping and optional calibration/semantic stages
- dashboard counts and project workspace tab routing
- report active/terminal formatting
- terrain geometry, D1 cell-center mapping, UV convention, nodata holes,
  relative gamma/exaggeration, and top-down camera framing
- D2 terrain/texture availability and relative/metric labeling
- map click resolution and inspection formatting
- all measurement modes and result formatting
- calibration residual values, colors, scale, tooltip text, and disclaimers
- first-person movement/follow/HUD helpers
- path construction, clearance, playback, speed, and relative semantics
- recording support helpers
- GLB export parsing/limits/naming

There are no React DOM/component tests. Login/Register, ProtectedRoute, AppShell,
forms, upload/table rendering, analysis/report panels, error/empty/loading states,
responsive layout, focus behavior, and most ARIA contracts lack direct Vitest
coverage.

### Playwright

11 spec files / 19 real-stack tests:

- D2 projected/geographic texture compatibility (2)
- P1-3 DTM/nDSM (1)
- P1-4 slope at point (1)
- P1-5 calibrated/uncalibrated residuals (2)
- P1-6 off-meridian 2D georegistration (1)
- P1-7 calibrated/relative first-person flythrough (2)
- P1-8 calibrated/relative path, playback, recording (2)
- P1-9 calibrated/relative GLB export (2)
- Phase 10 calibrated/relative terrain and texture (2)
- Phase 13 golden path through dashboard, upload, analysis, map, measurement,
  disaster, report, and PDF download (1)
- Phase 13b 2D/3D mode switching and repeated PointerLock interaction (3)

Strong browser coverage protects terrain correctness and major golden paths.
Weak coverage remains around login/logout/protected-route semantics, responsive
layout, accessibility, failed/loading states, destructive actions, GCP UI,
WebGL fallback, and report formats other than the golden-path PDF assertion.

## Validation results

| Validation | Exact result |
| --- | --- |
| Vitest | **18 files passed; 204 tests passed** in 16.35s |
| TypeScript | **clean** (`tsc -b --pretty false`) |
| ESLint | **0 errors, 5 warnings** |
| Vite production build | **passed**, 682 modules, 20.61s |
| Playwright | **11 spec files; 19 tests passed** in 11.5m |

Production bundle output:

- HTML: 0.39 kB (0.27 kB gzip)
- CSS: 41.65 kB (11.97 kB gzip)
- JavaScript: 1,391.63 kB (389.19 kB gzip)

## Known frontend warnings

ESLint reports five existing `react-refresh/only-export-components` warnings:

- `AuthContext.tsx`: 1
- `PageHeaderContext.tsx`: 4

Vite reports one chunk-size warning because the minified JavaScript chunk is
larger than 500 kB. These are baseline warnings, not Phase 1 regressions.

Playwright/Node may print that `NO_COLOR` is ignored because `FORCE_COLOR` is
set. This is runner output, not a browser/product error.

## State baseline

### Loading

- Auth hydration uses a full-screen loading message through ProtectedRoute.
- Collections and project heading use fixed skeleton blocks.
- Upload uses real XHR byte progress.
- Analysis/disaster/report polling displays backend state and stops at terminal
  status.
- 3D displays a terrain-grid loading overlay until real mesh readiness.

### Empty

`EmptyState` is used for projects, datasets, analysis jobs, and reports. Empty
terrain context is plain explanatory text. Prerequisite states are generally
disabled cards or paragraphs rather than a shared pattern.

### Error

Most pages/panels render a local red bordered message populated from `ApiError`.
Badge `title` sometimes carries terminal error detail. There is no shared
structured ErrorState, retry contract, or error boundary.

### Success

Success is normally represented by the resulting persisted row/artifact and a
green status badge. There is no shared toast/live-region success pattern.

## Responsive baseline

Tailwind responsive usage in production components is limited to:

- `sm:inline` in AppShell
- `sm:grid-cols-3` in Dashboard
- `sm:flex-row sm:items-end` in Projects
- `sm:grid-cols-4` in Reports
- `lg:grid-cols-[280px_1fr]` in TerrainWorkspace

The prior live audit at 390x844 measured a 442px document width on both Dashboard
and project workspace. The brand wrapped, navigation crowded the header, project
tabs wrapped densely, and the full workspace had no phone-specific capability
boundary. There are no automated responsive assertions.

Desktop populated terrain screenshots show a 280px left rail extending beyond
4500px while the map/3D viewport remains near the top. This is the central layout
risk for the overhaul.

## Accessibility baseline

Existing strengths:

- global `:focus-visible` ring
- global `prefers-reduced-motion` override
- reduced-motion handling in AnimatedNumber and decorative canvas
- ProgressBar and Spinner roles/values/names
- selected residual kind radio semantics
- waypoint `aria-pressed`, labeled mesh controls, and several grouped controls
- decorative icons/canvas marked hidden

Known gaps:

- Login/Register labels are not programmatically associated with inputs.
- Project workspace tab buttons use `aria-current`, not complete tab semantics.
- Many hints rely on native `title`, which is mouse-centric and inconsistent.
- Async stage/completion changes do not consistently use live regions.
- Terrain metadata and limitations frequently use 10-11px text.
- Keyboard navigation, canvas alternatives, focus restoration, and contrast have
  no automated checks.
- Upload dropzone has `role=button`, but full keyboard/name behavior needs a
  dedicated baseline assertion during its redesign phase.

## Performance baseline

Existing strengths:

- active-only analysis, disaster, and report polling with terminal cleanup
- Leaflet/Three.js lifecycle cleanup and refs for animation-sensitive state
- object URL revocation and authenticated binary fetching
- decorative canvas pauses on hidden documents and handles reduced motion
- terrain engine behavior is covered by T2 and browser guardrails

Observed risks:

- Dashboard issues `1 + 2N` requests for N projects.
- One 1.39 MB minified JS chunk loads Leaflet, Three.js, Recharts, and all routes
  eagerly; Vite warns above 500 kB.
- TerrainWorkspace (1210 lines) and TerrainView3D (1271 lines) are broad ownership
  boundaries with rerender/lifecycle risk during restructuring.
- The full-screen decorative canvas maintains a requestAnimationFrame loop on
  authenticated routes while visible.
- Context changes can trigger multiple authenticated preview/terrain requests
  and image decodes; engine remounts must not be introduced by the overhaul.

No performance optimization is authorized in Phase 1. Future changes require
profiling and must preserve T2 operational behavior.

## Phase 1 change boundary

Phase 1 adds only this baseline and the regression checklist. No tests were added
because the complete existing suite passed and missing coverage does not require
changing production behavior before the relevant UI phase.
Backend, database, migrations, AI, geospatial code, APIs, closed engineering
logic, and production frontend files remain untouched.
