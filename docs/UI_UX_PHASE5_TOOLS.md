# TERRAIN-X UI/UX Phase 5: Operational Tools

Status: complete. This phase changes frontend presentation and state wiring only;
the measurement, disaster, flythrough, recording, mesh, and report engines remain
authoritative and unchanged.

## Tool activation model

The Phase 3 workspace modes remain the primary context selector:

| Mode | Primary tools |
| --- | --- |
| Explore | layer inspection, terrain representation, camera controls |
| Measure | inspect, elevation, coordinate, distance, profile, slope, history |
| Screen | calibrated source selection, screening inputs, job/result status |
| Flythrough | first-person controls, waypoints, playback, recording |

The existing project-level Terrain, Measurements, and Disaster tabs still route
to the same persistent workspace. Explore retains the established measurement
and path controls as a compatibility path for the existing accepted workflows;
the dedicated modes provide the explicit focused entry points. Mode changes do
not remount Leaflet or Three.js. The Inspector scroll position resets when its
mode changes so a new tool never opens at a stale offset.

`WorkspaceToolSection` is the common operational pattern: a short title,
always-visible textual state, optional description, and local controls/results.
Color supplements the state text and is never its only signal.

## Measurement UX

`MeasurementTools` presents one named tool group with native buttons and
`aria-pressed` state. Inspect, point elevation, distance, profile, coordinate,
and slope retain their existing identifiers and calculations. The active tool,
remaining click count, registered-click/loading state, and target surface are
stated beside the viewport. Measurement and waypoint modes use a crosshair
cursor, while existing map/terrain markers remain the source of point feedback.

Clear measurement explicitly removes pending points, the current result, and
the current error. It does not delete saved history. Results and saved history
remain in the existing Results drawer. Backend-derived value kind, units,
calibration state, CRS, disclaimers, and profile data are unchanged; relative
depth is never relabelled as elevation or assigned a metric unit.

## Disaster UX

Screen mode is organized into three operational groups:

- status and the persistent `Screening, not prediction` limitation;
- inputs, including the existing calibrated product and real parameters;
- screening result with real job stage/progress and Summary, Details, Actions.

Idle, processing, completed, failed, and unavailable states come from the
existing product/job state. The progress bar uses the backend job percentage and
does not extrapolate time. Completed output shows only persisted terrain, flood,
and landslide summaries. The result timestamp is the job's existing `updated_at`.
Failed jobs show the returned failure plus a valid retry path; one assertive live
announcement is used even when both job and disaster metadata contain details.

## Flythrough, waypoints, and playback

The Flythrough section distinguishes first-person/path status from waypoint
editing and playback/recording. It shows the existing navigation keys, selected
path speed, terrain-follow behavior, and authoritative path clearance. The
first-person HUD is compact, 12px minimum, moved to the upper-right to avoid the
relative-terrain notice, and exposes position, ground, clearance, heading,
speed, and follow state already computed by the flight engine.

Waypoint mode has an explicit pressed state, count, clear/undo actions, and the
existing full validation reason. No bounds, interpolation, sampling, collision,
or clearance behavior changed. Path readiness is computed when the existing 3D
path engine is mounted. Playback provides play/pause/resume/restart/stop, speed,
real progress, and a terminal label. Finished playback continues to report the
engine's exact `progress = 1` and displays 100%.

## Recording UX

The UI maps only states the current MediaRecorder integration can prove:

- Idle before recording;
- Recording while a recorder exists;
- Complete after the existing `Recording saved.` callback;
- Failed after the existing recording failure message.

The active action becomes `Stop recording`. Unsupported recording retains the
browser capability reason. The UI does not claim Preparing or Stopping because
the engine does not expose those states, and it does not claim completion before
the `onstop` callback produces the WebM download. VP9/VP8 selection is unchanged.

## GLB export UX

The export section states that the output is a physical, non-exaggerated terrain
grid mesh. Resolution uses the existing 256/512 options. Texture is explicitly a
request, not a promise: the existing GLB endpoint remains the authoritative
compatibility decision and the completed summary reports embedded or omitted
with its reason. This matters because GLB UV embedding and direct on-screen grid
texturing have different compatibility contracts. The request, parser, file
naming, generation, and download logic are unchanged.

Ready, Generating, Completed, and Failed are explicit. Generation uses an
indeterminate spinner because the endpoint does not provide percentage progress.
Completion reports the parsed vertex/triangle/height/texture summary. Failure
keeps the actual safe message and offers the same action as a retry.

## Reports

Reports remain in the existing project Reports tab; Phase 5 did not create a
second report surface in the workspace. The existing R1 lifecycle remains the
sole source of truth: pending, generating, completed, and failed are rendered
from returned report status, and polling runs only for active states. Existing
report formatting and golden-path browser regressions remain Phase 5 guardrails.

## Loading, errors, and accessibility

Tool loading is local: measurement says `Reading terrain value...`, screening
shows its real stage/progress, and GLB export says `Generating GLB...`. Tool
actions do not freeze the workspace. Known unavailability includes the actual
prerequisite or compatibility reason. Errors state the failed operation and the
available retry/recovery action without exposing stack traces.

Controls have programmatic names, keyboard operation, native disabled state,
textual pressed/status state, and existing focus-visible treatment. Measurement
feedback, recording completion/failure, export completion/failure, and screening
failure use bounded live regions. Continuous frame updates are not announced.

## Responsive strategy

At 1280px and wider, Layers, viewport, and Inspector use three columns. At
768-1279px, rails overlay the persistent viewport. Below 768px, the viewport is
22rem high and Layers/Tools become bounded stacked drawers; Results remains a
separate bounded drawer. Controls wrap or use responsive grids, remain reachable,
and produce no document-level horizontal overflow at 390, 768, 1024, or 1440px.

## Performance observations

- Mode, drawer, and tool changes preserve the existing terrain host.
- Measurement and export presentation adds no metadata or preview request.
- Disaster polling remains scoped to one active job and stops at terminal state.
- No React state was added to animation-frame paths.
- No Leaflet, Three.js, coordinate, physics, recording, export-generation, or
  report lifecycle implementation changed.

## Visual baselines

- `frontend/e2e/screenshots/phase5-1440-measure.png`
- `frontend/e2e/screenshots/phase5-1440-screen.png`
- `frontend/e2e/screenshots/phase5-1440-flythrough.png`
- `frontend/e2e/screenshots/phase5-390-measure.png`
- `frontend/e2e/screenshots/phase5-390-flythrough.png`

## Validation

| Validation | Result |
| --- | --- |
| Focused Phase 5 Vitest | **8 files; 83 passed** |
| Focused Phase 5 Playwright | **1 test; 4 named real-stack scenarios passed** in 1.1m |
| Responsive widths | **390, 768, 1024, and 1440 passed with no horizontal overflow** |
| Full Vitest | **22 files; 246 passed** in 20.09s |
| TypeScript | **clean** |
| ESLint | **0 errors; 5 existing Fast Refresh warnings** |
| Vite production build | **passed**, 696 modules in 22.11s |
| Production bundle | JS **1,424.21 kB / 398.68 kB gzip**; CSS **56.87 kB / 14.64 kB gzip** |
| Corrected GLB + report golden-path rerun | **3 passed** in 2.2m |
| Full Playwright | **25 passed** in 18.1m |

## Remaining limitations

- MediaRecorder does not expose separate Preparing or Stopping state to React;
  Phase 5 does not invent them.
- Path validity is produced by the existing Three.js path engine, so the 2D
  editor shows waypoint count until the 3D path surface is available.
- Reports remain a dedicated project tab and are not duplicated in the terrain
  Inspector.
- The existing Results drawer is fixed-height rather than user-resizable.
- The production build retains its existing large-chunk warning; bundle splitting
  was outside Phase 5.
