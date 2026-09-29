# TERRAIN-X UI/UX Regression Checklist

Purpose: preserve current product behavior while the approved UI/UX overhaul is
implemented. This is a behavioral contract, not a proposed design. Scientific
labels, units, coordinate resolution, API payloads, and output semantics remain
authoritative.

Coverage labels:

- **Vitest:** existing pure-logic unit coverage.
- **Playwright:** existing real-stack browser coverage.
- **Manual:** no meaningful automated assertion currently exists.
- **Partial:** automation covers part of the behavior, not the full UI contract.

## Authentication

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Register | Submit a unique valid email/password on `/register` | Account is created, token established, user reaches Dashboard | Playwright: all real-stack specs register; golden path asserts redirect |
| Login | Sign out, submit valid credentials on `/login` | User session hydrates and Dashboard opens | Manual; backend auth tests do not replace browser coverage |
| Invalid authentication | Submit invalid credentials | Inline non-secret error appears; session remains unauthenticated | Manual |
| Protected route | Open `/dashboard` or `/projects` without a token | Redirect to `/login`; protected content is not rendered | Manual |
| Session hydration | Reload a protected route with a valid token | `/auth/me` restores the user without losing the requested protected page | Manual |
| Logout | Activate Logout | Token is removed and protected routes redirect to Login | Manual |

## Projects

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Project creation | Create a named project | Project appears in the list and is navigable | Playwright: golden path and terrain specs |
| Project loading | Open a project URL and reload it | Correct project name, description, breadcrumb, and datasets load | Partial: all Playwright flows open real projects; no reload assertion |
| Project navigation | Move Dashboard -> Projects -> Project -> All projects | Route and visible context stay consistent | Partial: golden path |
| Project deletion | Delete a disposable project | Project and owned UI context disappear; failures are surfaced | Manual |
| Dashboard counts | Create project/data/jobs and revisit Dashboard | Counts and recent projects reflect backend state | Playwright: phase13 golden path |

## Data

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Dataset list | Open Datasets for a populated project | Real role, filename, status, metadata, and validation state render | Playwright: all workflow specs |
| Source upload | Select/drop JPEG, PNG, TIFF, or GeoTIFF | Real byte progress is shown; resulting dataset reaches backend status | Playwright: terrain/golden path specs |
| DEM upload | Select DEM reference role and upload GeoTIFF | DEM appears separately and is selectable for calibration | Playwright: calibrated workflow specs |
| GCP upload | Select GCP role and upload CSV | GCP dataset is represented honestly and available to analysis | Manual browser coverage |
| Dataset selection | Select a valid source in Analysis/Terrain/Reports | Corresponding context/jobs/reports load; references are excluded where required | Playwright: all major flows |
| Invalid upload | Upload unsupported/invalid data | Validation failure is visible without false success | Manual |
| Dataset deletion | Delete disposable data | Row and dependent selection state update; error is recoverable | Manual |

## Terrain 2D

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Render georeferenced map | Open a projected source analysis | Leaflet base map and raster footprint align; overlay is nonblank | Playwright: p16, golden path, D2 projected |
| Render geographic source | Open a geographic source whose terrain is reprojected | Correct backend-resolved overlay appears without frontend envelope approximation | Playwright: D2 geographic; p16 off-meridian |
| Render non-georeferenced data | Open an unreferenced image analysis | Local pixel-coordinate visualization works without claiming geographic coordinates | Playwright: phase10 relative workflow |
| Map inspection | Enable a scientific layer and click its overlay | Backend-authoritative source pixel/value and correct units are shown | Playwright: p13, p14, p16, phase13b |
| Overlay errors | Force/observe a preview failure | Existing map remains stable and an understandable error is visible | Manual |

## Terrain 3D

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Calibrated DSM render | Switch calibrated workspace to 3D | Nonblank elevation-backed mesh renders without relative warning | Playwright: phase10 calibrated, p17, p18, p19 |
| Relative terrain render | Switch uncalibrated workspace to 3D | Nonblank mesh renders with unitless/relative honesty notice | Playwright: phase10 relative, p17, p18, p19 |
| Terrain loading | Enter 3D while grid loads | Genuine loading state clears only after the real grid/mesh is ready | Playwright: phase10 |
| View presets | Select Perspective, Top-down, Reset/Fit | Camera framing is correct and terrain remains hittable | Vitest: terrainTopDownCamera; Playwright: p17/phase10 partial |
| WebGL unavailable | Run without WebGL support | 3D is disabled/explained while 2D remains usable | Manual |
| Grid/texture controls | Toggle grid and compatible RGB texture | Visible scene updates without changing terrain values | Playwright: D2 and phase10 texture assertions; grid manual |

## Layers

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Active layer | Select each available scientific layer | Active styling, legend, inspection source, and result semantics agree | Partial: p13/p14/p16 |
| Visibility | Toggle imagery and result-layer checkboxes | Only requested overlays render; active/visible states remain distinct | Playwright: p13 DTM/nDSM and golden path |
| Opacity | Change a visible layer's slider | Corresponding overlay opacity changes without changing data | Manual |
| Unavailable layers | Open relative/rejected-calibration context | Metric-only layers show an accurate unavailable reason | Playwright: phase10 relative, p15 uncalibrated |
| Legend | Compare continuous/categorical layers | Ramp/classes, endpoints, units, and nodata semantics match the selected layer | Partial: residual and disaster tests; otherwise manual |
| D2 texture compatibility | Compare projected and reprojected terrain | Texture is offered only when backend provenance says it is compatible | Vitest: terrainAvailability; Playwright: D2 (2 tests) |

## Measurements

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Plain inspection | Click 2D and 3D with Inspect off | One current inspection result appears from the authoritative layer | Playwright: golden path and phase13b |
| Point elevation | Activate, click once, save | Correct value/unit/source is shown and history persists it | Vitest: mode/format/inspection; Playwright: golden path, phase13b |
| Coordinate | Activate and click once | Geographic or local-pixel result matches backend resolution | Vitest: mode/format/mapClick; Playwright: phase13b partial |
| Distance | Activate and click two points | Real distance endpoint returns correct value/units and no duplicate handler fires | Vitest: mode/format; Playwright: phase13b |
| Profile | Activate and click two points | Profile endpoint and chart render; result can be saved | Vitest: mode/format; Playwright: phase13b |
| Slope at point | Activate and click calibrated slope location | Authoritative slope raster value appears and saves to history | Vitest: mode/format; Playwright: p14 |
| Mode switching | Cycle tools and return to Inspect | Point buffers reset correctly; stale callbacks do not fire | Vitest: measurementMode; Playwright: p14 and phase13b |
| Delete saved result | Delete a disposable measurement | History updates without affecting terrain state | Partial: Playwright p14 save/history; delete manual |

## Calibration

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Metric state | Open a gate-passed analysis | Metric elevation/DSM available with correct units and no relative-only warning | Vitest: terrainAvailability; Playwright: phase10/p13/p15 |
| Relative state | Open no-reference or rejected calibration | Relative/unitless terrain remains available; metric layers unavailable | Vitest: terrainAvailability; Playwright: phase10/p15 |
| Calibration status | Inspect Analysis job and terrain context | Passed/rejected/not-requested state and limitations match API metadata | Playwright: golden path, p15; status presentation partly manual |
| Residual visualization | Enable held-out/in-sample residuals in 2D | Points, legend, counts, tooltip values, and disclaimer match API/stored CV | Vitest: calibrationResiduals; Playwright: p15 |
| Residual unavailable | Open uncalibrated job | Accurate unavailable reason appears and no residual points are drawn | Playwright: p15 uncalibrated |

## DTM/nDSM

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| DTM availability | Complete gate-passed calibrated analysis | Estimated bare-earth DTM appears with explicit estimated semantics | Playwright: p13 |
| nDSM availability | Complete gate-passed calibrated analysis | Estimated height-above-ground layer appears with correct semantics | Playwright: p13 |
| Toggle and inspect | Show DTM/nDSM and click known locations | Correct layer/value/unit is inspected; DSM remains the 3D terrain source | Playwright: p13 |
| Rejected calibration | Open rejected/relative analysis | DTM/nDSM do not appear as available metric products | Partial: p15/phase10 |

## Disaster

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Prerequisite | Open screening without metric elevation | Screening is unavailable with an accurate calibration prerequisite | Manual/indirect golden path |
| Configure | Select elevation source, flood level, and screening options | Submitted parameters match visible choices and units | Playwright: golden path |
| Active lifecycle | Start screening | Real job status/progress is polled; controls stay appropriately disabled | Playwright: golden path |
| Completed results | Await terminal success | Terrain statistics and requested flood/landslide summaries render | Playwright: golden path |
| Scientific limitation | Read screening UI/results | "Screening, not prediction" and hydrology/geotechnical limitations remain visible | Playwright: golden path |
| Failure | Exercise a failed screening job | Failure and recoverable next action are visible; no false result layer appears | Manual |

## Flythrough

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Enter/exit first person | Activate Flythrough and use PointerLock/Escape | Camera enters/exits safely; normal click tools recover afterward | Vitest: flythrough; Playwright: p17, phase10, phase13b |
| Navigation | Use WASD, mouse look, vertical movement, and speed | Camera/HUD update and terrain-follow constraints remain valid | Vitest: flythrough; Playwright: p17 |
| Terrain follow | Fly over calibrated and relative terrain | Minimum clearance is maintained against rendered display grid | Vitest: flythrough; Playwright: p17 |
| Waypoints | Add points from 2D and 3D, undo, clear | Coordinate/local positions agree and list changes deterministically | Vitest: flythroughPath; Playwright: p18 |
| Playback | Play, pause, resume, restart, stop, change speed | Deterministic path and HUD state respond without losing clearance | Vitest: flythroughPath; Playwright: p18 |
| Relative semantics | Fly relative terrain | HUD/path remain unitless and never claim elevation/metres | Playwright: p17/p18 relative cases |

## Recording

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Supported recording | Record a valid calibrated path | Real WebM capture starts/stops and download is produced | Vitest: recorder support helpers; Playwright: p18 |
| Unsupported recording | Use unsupported context/browser fixture | Control is disabled/explained without false completion | Vitest: flythroughRecorder; Playwright: p18 relative case |
| Recording state | Record during playback | Playback/record controls cannot enter contradictory states | Playwright: p18 |

## GLB Export

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Resolution | Export 256 and 512 | Valid GLB has expected vertex/triangle bounds and requested resolution | Vitest: meshExport; Playwright: p19 |
| Metric export | Export calibrated off-meridian DSM | Cell centers, heights, CRS, units, texture, and no display exaggeration are exact | Playwright: p19 calibrated |
| Relative export | Export uncalibrated terrain | Raw unitless values, pixel units, holes, and relative labeling are preserved | Playwright: p19 relative |
| Texture eligibility | Request texture for compatible/incompatible sources | Compatible texture embeds; incompatible output explains omission | Playwright: p19 and D2 |
| Export failure | Force an API/download failure | Error appears and no success summary is shown | Manual |

## Reports

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Prerequisite | Select source without completed analysis | UI explains that analysis is required | Manual |
| Generate | Start report for completed analysis | Real pending/generating state appears and active-only polling starts | Vitest: reportFormat; Playwright: golden path |
| Terminal success | Await completion | Polling stops; Completed and available formats appear | Vitest: reportFormat; Playwright: golden path |
| Download | Download PDF/JSON/CSV/ZIP as available | Real file download starts; unavailable CSV has a reason | Playwright: PDF in golden path; other formats manual UI coverage |
| Terminal failure | Observe failed report | Polling stops and backend error is visible without downloads | Vitest: reportFormat state; browser presentation manual |
| Reload/reconciliation | Reload while report is active or after worker failure | Server truth is restored; UI does not remain generating forever | Backend R1 coverage; browser manual |

## Error States

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| API/page error | Fail project/dataset/context request | Error is visible; stale success is not implied | Manual |
| Upload/analysis/report error | Trigger each terminal failure | Responsible surface shows failure and permits a valid recovery action | Partial state-helper coverage; browser manual |
| Map/3D artifact error | Fail preview/grid/texture request | View remains stable where possible and error does not expose a stack trace | Manual |
| Transient polling error | Interrupt one poll, then restore | Active state is not falsely converted to terminal failure | Source behavior; no direct frontend automated test |

## Loading States

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Auth hydration | Reload with stored token | Bounded loading state precedes protected content/redirect | Manual |
| Page collections | Throttle projects/datasets/jobs | Skeletons occupy stable layout and resolve to data/empty/error | Manual |
| Upload | Upload a nontrivial file | Progress uses real bytes and reaches terminal dataset state | Playwright workflows; exact progress presentation manual |
| Analysis/disaster/report | Start each worker task | Only real stage/status/progress is presented | Playwright: golden path; Vitest pipeline/report helpers |
| Terrain assets | Enter map/3D on cold load | Preview/grid/mesh loading clears when actual content is ready | Playwright: phase10 |

## Responsive Behavior

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Desktop 1440/1280 | Exercise all pages and populated workspace | No overlap; viewport/tools remain usable; text fits controls | Manual; screenshots exist from E2E but no layout assertions |
| Tablet 1024/768 | Exercise shell, project tabs, map, reports | No horizontal page overflow; controls remain reachable | Manual |
| Phone 390x844 | Exercise auth/dashboard/project/status flows | No horizontal overflow; readable header/navigation; supported capabilities explicit | Manual; audit baseline found current 442px overflow |
| Long content | Use long email/project/dataset/error text | Text wraps/truncates without obscuring commands | Manual |
| Canvas resize | Resize viewport/panels around map/3D | Leaflet and Three.js resize correctly and stay nonblank | Partial Playwright canvas size checks; responsive resize manual |

## Accessibility

| Behavior | How to verify | Expected result | Existing automated coverage |
| --- | --- | --- | --- |
| Keyboard navigation | Tab through each route and workspace control | Logical order, no trap, all actions operable, focus always visible | Manual |
| Form labels | Inspect accessibility tree/getByLabel | Every input/select has programmatic label and errors/help are associated | Manual; current login/register fail association baseline |
| Control semantics | Inspect tabs, tool toggles, dialogs, progress | Correct roles/states/names; selected and pressed states announced | Partial: some `aria-*`; no semantic test suite |
| Status announcements | Run upload/jobs/reports | Meaningful stage/terminal changes are announced without poll noise | Manual; currently inconsistent |
| Contrast and color | Audit light/dark surfaces, ramps, disabled/error states | WCAG AA where applicable; status/legend meaning is not color-only | Manual |
| Reduced motion | Emulate `prefers-reduced-motion: reduce` | Optional animation collapses; controls and data remain available | Source implementation; no browser assertion |
| Pointer alternatives | Use measurement/flythrough controls without mouse where supported | Commands work by keyboard; canvas-only precision limits are explicit | Manual |

## Baseline coverage gaps to address during the overhaul

Do not change current tests merely to accommodate a redesign. Add behavior-first
assertions when the corresponding phase is implemented. Highest-priority gaps:

1. Login, invalid login, protected-route redirect, session reload, and logout.
2. Programmatic form labels, keyboard order, focus return, and live status.
3. Responsive overflow/layout checks at 1024px and 390px.
4. Project/dataset deletion confirmation and failure presentation.
5. Loading/empty/error/success rendering for shell, pages, and task panels.
6. Report failed/reload lifecycle and non-PDF download controls in the browser.
7. WebGL-unavailable fallback and terrain artifact/texture failure UI.

