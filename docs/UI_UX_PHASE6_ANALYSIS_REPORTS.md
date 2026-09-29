# UI/UX Phase 6: Analysis and Reports

## 1. Analysis information architecture

The project Analysis tab now follows the real workflow: available analysis,
configuration, then active and recent jobs. Available-analysis rows explain the
products supported by current inputs. A terrain analysis remains one backend
job; DTM and nDSM are correctly presented as downstream products, not invented
standalone jobs.

## 2. Job-state presentation

`AnalysisJobCard` presents the backend job state, real progress and pipeline
stages, source dataset, timestamps, products, and supported actions. Queued and
running jobs alone trigger polling. Cancel uses the existing endpoint. Failed
jobs retain the backend-provided reason and a recovery instruction. Lifecycle
state changes are polite live regions; percentage ticks are not independently
announced.

## 3. DTM UX

DTM is labeled as an estimated ground surface produced by the raster ground
filter after successful calibration. Its row exposes processing, complete,
failed, or unavailable state, the real unavailable/failure reason, verified
vertical unit, and the existing GeoTIFF download. It is not described as a
measured or independently validated ground model.

## 4. nDSM UX

nDSM is explicitly defined as DSM minus the estimated DTM and as estimated
height above ground. DSM, DTM, and nDSM are separate product rows. nDSM shares
the backend ground-filter state and verified vertical-unit metadata; unknown
units remain `Unknown`.

## 5. Calibration UX

Calibration maps existing state to not available, processing, validated,
failed, or quality-gate rejected. The disclosure exposes scale, offset, sample
and outlier counts. In-sample fit and held-out validation remain visually and
semantically separate, including MAE, RMSE, bias, R2, method, and skill when
provided. No aggregate accuracy score or inferred metric validity is added.

## 6. Residual UX

Analysis explains the authoritative convention: residual equals Predicted minus
Reference; positive means higher than reference and negative means lower. It
identifies held-out residuals as the workspace default. The existing workspace
continues to own residual map rendering, fit/held-out selection, outlier display,
ramp, and GeoJSON download, avoiding duplicate calculations or requests.

## 7. Result artifact UX

Rows cover relative depth, metric elevation, DSM, DTM, nDSM, and requested
surface-region output. Every row pairs a scientific definition with state, unit,
reason, and only an existing download action. Relative depth remains explicitly
unitless and is never labeled elevation.

## 8. Analysis to Workspace

`Open in workspace` sends the completed job's existing source dataset ID to the
existing Terrain Workspace selector. It does not regenerate or duplicate-load
an artifact. Once first opened, the workspace remains mounted while navigating
through Analysis and Reports, preserving its terrain engine and local state.
The last Terrain/Measurements/Disaster mode is also retained.

## 9. Reports UX

Reports is organized into generation and recent-report history. Generation uses
the existing source-dataset selection and endpoint. Metadata-only cards show ID,
created/completed times, exact lifecycle, failure recovery, and completed files.
No full report JSON or raster preview is fetched to render the list.

## 10. Report lifecycle presentation

Backend `pending` is presented as `Queued`; `generating`, `completed`, and
`failed` remain distinct. Only queued/generating reports poll. Active rows use an
indeterminate status because the API exposes no report percentage. Failed rows
show the persisted reason and existing retry operation. Completed controls do
not appear early.

## 11. Download UX

Completed cards identify PDF, JSON, CSV, and ZIP bundle by format and filename.
Controls keep stable accessible names. Formats the report marks unavailable are
disabled with a reason. Existing download endpoints and browser filenames are
unchanged.

## 12. Error, empty, and loading states

No datasets, no selected dataset, no jobs, no reports, metadata loading, action
failure, analysis failure, calibration rejection, and unavailable products all
have explicit text and a next action where one exists. Loading states do not
claim completion and errors do not fabricate causes.

## 13. Accessibility

The surfaces use semantic sections/headings/articles, native form controls and
details/summary, text-plus-color badges, named downloads, keyboard-operable
actions, `aria-busy` for report generation, and restrained live regions for
meaningful lifecycle transitions. Expandable diagnostics remain reachable with
native keyboard behavior.

## 14. Responsive behavior

Operational rows stack at narrow widths; product actions, technical metadata,
report files, and tab navigation remain reachable. Browser checks at 1440,
1024, 768, and 390 pixels found no document-level horizontal overflow.

## 15. Performance observations

- Analysis and report polling remains active-only and stops at terminal state.
- Report history fetches metadata only.
- Terrain Workspace stays mounted after first use across Analysis/Reports tabs.
- Analysis-to-workspace selection reuses the existing context loader.
- The production main JavaScript chunk remains large at 1,430.38 kB minified
  (400.06 kB gzip); code splitting is deferred because it is outside Phase 6.

## 16. Tests

- Focused Phase 6 Vitest: 10 passed.
- Focused persistence/flythrough reconciliation: 30 passed (5 workspace-tab and
  25 flythrough/tool tests).
- Full Vitest: 256 passed across 24 files.
- TypeScript: clean.
- ESLint: 0 errors, 5 existing Fast Refresh warnings.
- Vite production build: passed, 698 modules transformed.
- Focused Phase 6 Playwright: 1 test passed with 6 named scenarios.
- Full Playwright: 26 passed in 9.2 minutes.

## 17. Screenshot baselines

- `frontend/e2e/screenshots/phase6-analysis-1440.png`
- `frontend/e2e/screenshots/phase6-reports-1440.png`
- `frontend/e2e/screenshots/phase6-analysis-1024.png`
- `frontend/e2e/screenshots/phase6-analysis-390.png`
- `frontend/e2e/screenshots/phase6-reports-390.png`

## 18. Known limitations

- DTM/nDSM cannot be run or retried independently because the backend exposes
  them only as products of a calibrated terrain-analysis job.
- Residual visualization remains in Terrain Workspace; Analysis provides the
  convention and direct workspace transition rather than duplicating the map.
- Reports have metadata cards and direct file downloads, but no separate inline
  full-report viewer.
- The Playwright raster-value specs require `.venv-gamus/Scripts` on `PATH` so
  their unchanged `python` subprocess can import Rasterio.
- The known Vite main-chunk size warning remains.
