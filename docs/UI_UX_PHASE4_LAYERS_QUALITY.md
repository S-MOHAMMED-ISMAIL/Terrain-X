# TERRAIN-X UI/UX Phase 4: Layer System and Data Quality

Status: implemented. Final full-browser total is recorded after the concluding
regression run. This phase changes frontend presentation and existing state
wiring only.

## State ownership

The frontend keeps the existing source-of-truth boundaries:

| State | Authoritative source | Frontend owner |
| --- | --- | --- |
| Reported artifacts, availability, reason, range, CRS | `VisualizationContext.layers` | `TerrainWorkspace` |
| Active layer | local workspace selection | `TerrainWorkspace` |
| Layer visibility and opacity | local presentation state | `TerrainWorkspace` |
| 2D/3D representation | local presentation state | `TerrainWorkspace` |
| Metric/relative terrain | `VisualizationContext.terrain.height_kind` | presentation helper |
| Calibration lifecycle and diagnostics | matching `AnalysisJob` | presentation helper/panels |
| Vertical unit | `AnalysisJob.calibration_metadata.vertical_unit` | presentation helper |
| Residual availability | `VisualizationContext.calibration_residuals` | `TerrainWorkspace` |
| Residual values/statistics | residual artifact endpoint | existing residual component |
| Selected raster metadata/NoData | existing raster metadata endpoint | artifact-ID cache |

The workspace requests the existing project analysis-job list once with each
dataset context load, then matches the job named by the terrain/layer context.
Exact layer artifact job IDs take precedence; standalone disaster jobs own
derivative/screening processing and failure state.
When no completed visualization job exists, the latest dataset job supplies an
honest processing/failure state. Selecting a raster requests its existing
metadata endpoint at most once per artifact ID. Panel and disclosure changes do
not call APIs.

## Layer presentation

The Layers rail owns selection, visibility, availability, and opacity. Active
selection and visibility remain independent: selecting a layer changes the
Inspector source but does not show it, and changing visibility does not change
the active layer. Unavailable controls use native disabled behavior and retain
the backend-provided reason.

Rows show the reported name, concise unit/type semantics, textual data state,
visibility, and active opacity. The Inspector owns the selected layer's range or
categorical legend, dimensions, CRS/local-coordinate state, data type, NoData,
notes, and the reminder that display colors are not measurements. Existing
scientific ramps and categorical colors are unchanged.

Available, unavailable, processing, and failed presentation comes from explicit
job fields where they exist: calibration status for metric/DSM, ground-filter
status for DTM/nDSM, semantic status for segmentation, disaster status for
screening/derivatives, and analysis status for the base result. The API's
`unavailable_reason` remains visible verbatim.

## Quality mapping

The persistent quality bar and its bounded details disclosure use these rules:

| Terrain/job truth | Mode | Vertical unit | Calibration |
| --- | --- | --- | --- |
| `height_kind=elevation`, calibrated | Metric | declared unit, otherwise Unknown | Passed |
| `height_kind=relative_depth`, uncalibrated | Relative | Unitless | Not available |
| `height_kind=relative_depth`, calibration gate failed | Relative | Unitless | Rejected |
| `height_kind=relative_depth`, other calibration failure | Relative | Unitless | Failed |
| calibration in progress, no metric output | unavailable/relative as reported | Unknown/Unitless as reported | Processing |
| no terrain result | Unavailable | Unknown | real job state or Not available |

Calibration success is never inferred from artifact names or job completion.
`quality_gate.passed === false` is the only frontend distinction between
Rejected and another calibration failure. A passed gate is explicitly described
as agreement under configured criteria, not independent accuracy evidence.

CRS comes from the active layer, terrain, or dataset in that order. A missing CRS
is shown as local pixel coordinates for an unreferenced dataset and Unknown for
a georeferenced dataset whose CRS was not reported. Physical vertical units are
read only from the structured calibration metadata. Unknown remains Unknown and
is never assumed to mean metres. Unavailable metric elevation/DSM/DTM/nDSM rows
say `Metric output unavailable`; they never inherit the relative terrain's
`Unitless` label.

## Calibration and residual diagnostics

Long diagnostics live in a collapsed, keyboard-operable disclosure in the
Results drawer so they do not permanently reduce the map. The presentation
separates:

- **In-sample fit:** valid samples, MAE, RMSE, bias, and R2, labelled as not
  validation.
- **Held-out validation:** stored cross-validation method, sample count, MAE,
  RMSE, bias, and skill.

Values are formatted for display only; no statistic or accuracy score is
recomputed. Backend errors and limitations remain visible. Calibration failure
keeps a clear relative-output notice when relative depth exists.

Residual controls remain in the Layers rail because they control visibility of
a point overlay. Held-out remains the default, fit remains explicitly
in-sample, and the existing residual definition, sign convention, display ramp,
outlier meaning, download, marker values, and map behavior are unchanged.

## Empty, processing, errors, and NoData

- No valid source dataset uses the shared empty-state presentation.
- Context and layer-detail loading use shared loading states.
- Context failures and selected-metadata failures use shared error notices.
- A loaded dataset without completed analysis retains reported unavailable rows
  and reasons; RGB/relative results are not hidden merely because metric output
  is absent.
- Processing and failed artifact states are text-labelled and do not rely on
  color.
- An authoritative NoData sentinel is shown as excluded and explicitly not
  zero. When the metadata endpoint reports no sentinel, the UI says it is not
  reported and still states that NoData is not zero.
- Preview failures remain on the existing map error surface; this phase did not
  change Leaflet request or overlay behavior.

## Accessibility and responsive behavior

Layer selectors are named buttons with `aria-pressed`; visibility uses a
separate labelled checkbox. Disabled layers retain descriptions and reasons.
Native disclosures support keyboard operation, selected details use headings
and description lists, numeric diagnostics use tabular formatting, and async
loading/errors use status/alert semantics. State text accompanies every color.

Live browser validation found no document-level horizontal overflow at 390,
768, 1024, or 1440px. Long identifiers and explanations break within their
region; diagnostic and quality disclosures scroll internally. Expanded quality
details overlay the workstation rather than resizing the viewport. This was
necessary to preserve the fitted Leaflet geometry for dense residual markers.

## Measured performance observations

- Dataset selection adds one existing analysis-list request to associate
  calibration truth with the displayed context.
- Selecting an artifact adds one existing raster-metadata request, cached by
  artifact ID for the workspace session.
- Automated request counting observed zero additional preview/map-preview
  requests from opening/closing tools, results, or quality presentation after
  requested overlays had settled.
- The viewport instance remains mounted through panel and disclosure changes.
- The production JavaScript is 1,417.91 kB minified / 396.49 kB gzip, about
  13.55 kB / 3.59 kB above Phase 3. General bundle optimization was not
  attempted.

## Validation

| Validation | Result |
| --- | --- |
| Focused Phase 4 Vitest | **2 files; 20 passed** in 7.26s |
| New Phase 4 Vitest cases | **11 passed** |
| Full Vitest | **21 files; 236 passed** in 12.07s |
| TypeScript | **clean** |
| ESLint | **0 errors; 5 existing Fast Refresh warnings** |
| Vite production build | **passed**, 694 modules, 18.21s |
| Focused Phase 4 Playwright | **2 passed** in 35.9s |
| Phase 3 + Phase 4 browser slice | **3 passed** in 58.6s |
| Exact calibrated residual regression | **1 passed** in 22.9s |
| Full Playwright | **24 passed** in 13.0m |

## Remaining limitations

- `VisualizationContext` does not carry calibration status/metadata, so the
  frontend must associate the existing `AnalysisJob` response with it.
- `LayerContext.nodata` is currently often null; selected artifact metadata is
  required for the authoritative sentinel.
- Calibration diagnostics are display-only and do not provide an independent
  scientific accuracy assessment.
- The Results drawer remains fixed-height rather than user-resizable.
- The existing large-chunk Vite warning and five Fast Refresh warnings remain.

