# TERRAIN-X UI/UX Phase 3: Terrain Workspace Shell

Status: implemented and validated. Phase 3 changes presentation and workspace
composition only; terrain, geospatial, calibration, measurement, disaster,
flythrough, export, and report semantics remain authoritative and unchanged.

## Previous architecture

The terrain workspace was a long two-column page. A 280px left column contained
layers followed by most tools, while the 2D/3D viewport occupied only the upper
right. Measurement, disaster, flythrough, and export controls could therefore
scroll far away from the terrain they affected. Terrain, Measurements, and
Disaster project tabs all retained the same `TerrainWorkspace`, but did not
communicate a contextual tool focus to it.

## Implemented architecture

`TerrainWorkspaceShell` now provides one bounded workstation around the existing
terrain engines:

- a compact project/dataset header with 2D/3D and workspace-mode controls;
- a persistent data-quality bar;
- a compact Layers rail on the left;
- the existing Leaflet or Three.js viewport in the center;
- a contextual Inspector rail on the right; and
- a bottom Results drawer for inspection, current results, and history.

`TerrainWorkspace` still owns the existing data, API, and tool state. The shell
owns only geometry, panel visibility, responsive presentation, and contextual
mode selection. Project tabs map Terrain to Explore, Measurements to Measure,
and Disaster to Screen while preserving the same retained workspace instance.
Flythrough is available as an additional workspace mode.

## Responsibilities and lifecycle

The viewport child is structurally stable when either rail, the results drawer,
or a contextual mode changes. These changes do not key, replace, or remount the
Leaflet/Three.js engine. `MapView2D` observes its container and calls Leaflet's
`invalidateSize` after geometry changes; its existing map and layer cleanup
continues to run only with the view lifecycle. The 2D/3D control intentionally
changes the selected engine, as it did before Phase 3.

The shell does not perform API calls, derive terrain values, transform
coordinates, or own measurement/flythrough state. Existing control labels were
retained where browser regressions depend on their user-visible contract.

## Quality bar semantics

Quality items are derived conservatively from the existing visualization
context and active layer:

- backend elevation capability is shown as `Metric`, with the reported
  vertical unit and a validated calibration state;
- relative terrain is shown as `Relative`, `Unitless`, and calibration `Not
  available`;
- CRS/georeference state is displayed only when the context reports it;
- missing georeference information is reported as local pixel or not reported,
  never inferred.

Completion is not presented as scientific accuracy. The bar does not invent a
CRS, unit, calibration result, or availability claim.

## Layers, inspector, and results

Layer rows now keep visibility and active inspection selection as separate
controls. Rows remain compact; expanded metadata and the legend are shown only
for the active layer. Existing opacity, visibility, availability, residual, and
selection behavior is preserved.

The Inspector presents existing controls according to the selected context:

- **Explore:** terrain/view controls and compatibility access to existing tools;
- **Measure:** measurement modes and actions;
- **Screen:** existing disaster screening controls;
- **Flythrough:** path, playback, recording, and related terrain controls.

The Results drawer contains existing inspection results, measurement output,
and saved history. Opening and closing it changes shell geometry without
discarding viewport or tool state.

## Responsive strategy

- **1280px and wider:** persistent 256px Layers rail, fluid central viewport,
  and 320px Inspector rail.
- **768-1279px:** the viewport remains primary and rails overlay it from the
  edges. Workspace rails are stacked above Leaflet panes so controls remain
  usable without changing map behavior.
- **Below 768px:** an intentional vertical sequence presents the viewport,
  contextual tools, and layers without squeezing a three-column workstation.
  The viewport has bounded height and each rail scrolls internally.

The global app bar also collapses navigation labels on narrow phones while
retaining accessible link names. Automated checks cover 1440, 1024, 768, and
390px widths and assert no document-level horizontal overflow.

## Accessibility

The shell exposes named navigation/header, toolbar, complementary rail, main
viewport, and results regions. Toggle controls publish `aria-expanded` and
`aria-controls`; mode and view controls expose pressed state. Closed rail and
drawer content is inert, keyboard focus remains visible, and native controls
preserve keyboard activation. Layer visibility remains a labeled checkbox and
active-layer selection is announced independently.

## Regression and visual validation

- Focused Phase 3 Vitest: **9 passed**.
- Full Vitest: **225 passed**.
- TypeScript: **clean**.
- ESLint: **0 errors, 5 existing Fast Refresh warnings**.
- Vite production build: **passed**.
- Focused Phase 3 Playwright: **1 passed**.
- Full Playwright regression: **22 passed (8.4m)**.

Visual baselines:

- `frontend/e2e/screenshots/phase3-1440-explore.png`
- `frontend/e2e/screenshots/phase3-1024-measure.png`
- `frontend/e2e/screenshots/phase3-768-screen.png`
- `frontend/e2e/screenshots/phase3-390-flythrough.png`

The full browser run exposed one exact terminal-status defect in the existing
flythrough presentation: floating-point recomputation could report
`0.9999999999999999` after playback state was already `finished`. The status
adapter now emits exact progress `1` and elapsed duration for that terminal
state. Path interpolation, playback timing, camera behavior, terrain clearance,
and all terrain/scientific calculations are unchanged.

## Known limitations

- Tablet rails are overlays, not focus-trapping modal drawers; their controls
  remain keyboard reachable and can be closed from their named toggle.
- Phone layout preserves access to the existing advanced tools instead of
  introducing a separate read-only product mode in this phase.
- Rail widths and drawer height are fixed responsive contracts, not yet
  user-resizable.
- The existing production bundle-size warning remains; Phase 3 does not change
  loading or bundling architecture.

