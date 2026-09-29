# TERRAIN-X Technical Roadmap

This roadmap sequences work so that every phase ends with something real and runnable — no phase depends on faked data from a later phase. Phases are scoped by dependency order, not calendar dates.

> Numbering note: this roadmap was originally drafted with auth/projects and dataset ingestion as separate phases. In practice, Phase 0 delivered both (environment, auth, and project management together), so the phases below are renumbered to match what was actually built, starting from Phase 1 = dataset ingestion. If you're looking at an old reference to "Phase 2 — Dataset ingestion" or "Phase 3 — Analysis job framework," those are this document's Phase 1 and Phase 2 respectively.

## Phase 0 — Environment, auth & project management — ✅ complete
- Docker Compose: PostgreSQL/PostGIS, Redis, FastAPI backend, RQ worker, React frontend.
- `users` table, Argon2id password hashing, JWT access tokens, protected route dependency.
- `projects` CRUD, ownership checks (404-not-403 IDOR prevention).
- Frontend: login/register, protected routes, project list/create/view.
- **Exit criterion met**: a real user can register, log in, and create/view/delete a project — all state in PostgreSQL.

## Phase 1 — Dataset ingestion — ✅ complete
- Upload endpoint (JPEG/PNG/TIFF/GeoTIFF) streaming to `storage/`, server-side magic-byte format detection (never trusting client extension/MIME).
- Real metadata extraction via GDAL/rasterio: dimensions, bands, and — only when GDAL actually reports one — CRS/bounding box.
- `datasets` table + status tracking (`uploaded → validating → valid/invalid/failed`).
- Frontend: dataset upload UI with real upload progress and status display.
- **Exit criterion met**: uploading a real GeoTIFF produces a `datasets` row with genuinely extracted metadata (dimensions, CRS, bounds) visible in the UI; a plain `.tif` with no embedded CRS is correctly shown as non-georeferenced.

## Phase 2 — Analysis job framework (no AI yet) — ✅ complete
- `analysis_jobs` (status + stage enums, versioned JSONB parameters, execution summary) and `analysis_artifacts` (reserved, empty) tables.
- RQ + Redis worker executing real jobs: reopens the stored file via GDAL, cross-checks structural metadata against what's recorded, computes real per-band pixel statistics — genuine verification work, not a depth/DSM pipeline.
- Job create → enqueue → real status/stage transitions → completion/failure, with a database-enforced one-active-job-per-dataset invariant and safe queued-only cancellation.
- Frontend: dataset picker + "Start Analysis," job history table, polling (configurable interval) that stops at any terminal status.
- **Exit criterion met**: a job can be created, tracked through real status/stage transitions via the separate worker container, and reaches `completed` or a real, informative `failed` — end-to-end through Redis/RQ, verified to survive a full `docker compose down`/`up`.

## Phase 3 — Monocular depth estimation (AI MVP) — ✅ complete
- `ai/DepthEstimator` interface + `DepthAnythingV2Estimator` (Depth Anything V2 Small, Apache-2.0, via `transformers`), CPU inference (no local GPU available; CUDA used only if the runtime genuinely detects it).
- Model name/revision/device/inference-time recorded on both the job's `execution_summary` and the artifact's `artifact_metadata`.
- Four new `AnalysisStage` values (`loading_model`, `preprocessing`, `inference`, `writing_depth`) extend the enum between `validating_input` and `finalizing`; Phase 2's generic `executing` is kept (never removed — Postgres enums are append-only) but no longer produced for new jobs.
- Real single-band float32 GeoTIFF output (`relative_depth` artifact), first real rows in `analysis_artifacts`; georeferencing carried over from the source only when the source genuinely had it.
- Input-eligibility policy: 3-band (or 4-band PNG with alpha dropped) 8-bit RGB only — other configurations fail the job with a clear message rather than being silently reinterpreted.
- Frontend: Analysis tab shows model/device/inference time/output dimensions per job, with a "Download depth" action once completed, clearly labeled as uncalibrated relative depth.
- **Exit criterion met**: uploading an image and running a job produces an actual relative depth map (verified non-constant on a structured test scene, not synthetic noise), stored as a real artifact and retrievable via the authenticated download API — verified end-to-end through the real separate worker container, including surviving a full `docker compose down`/`up`.

## Phase 4 — Geospatial calibration & DSM — ✅ complete
- `geospatial/calibration.py`: CRS-aware DEM/GCP sampling (`rasterio.warp.transform`, never index-matched across grids), robust affine fit (`Z = a·D + b` via OLS + iterative sigma-clipping), real validation metrics (MAE/RMSE/bias/residuals — never an "accuracy percentage").
- `Dataset.role` enum (`source_image`/`dem_reference`/`gcp_reference`) reuses the existing table — no schema duplication; DEM references reuse the raster ingestion path, GCP references use a new CSV path (`app/services/gcp_ingestion.py`) with a caller-declared CRS (a CSV has none of its own).
- `AnalysisParametersV1` gained optional `dem_reference_dataset_id`/`gcp_reference_dataset_id` (at most one); calibration is opt-in per job, reusing job creation rather than a separate stateful workflow.
- Four new `AnalysisStage` values (`calibrating`, `writing_metric_elevation`, `writing_dsm`, `validating_results`), only entered when a reference was requested; a new, independent `calibration_status`/`calibration_metadata` pair on `AnalysisJob` tracks the calibration outcome apart from job status.
- Real `metric_elevation` and `dsm` artifacts (float32 GeoTIFFs, source CRS/transform preserved) — only written on a successful fit; a non-georeferenced source, missing/invalid reference, or too few valid samples correctly yields `calibration_status=failed` (with a real, honest reason) without failing the job itself or fabricating output.
- Frontend: Analysis workspace lets a user attach a DEM/GCP reference and start calibration, observes the real stage progression, and sees real scale/offset/sample counts/MAE/RMSE/bias plus metric elevation/DSM downloads — clearly labeled "Relative Depth — Uncalibrated" vs. "Metric Elevation — Calibrated" vs. "DSM — Calibrated," no fabricated accuracy score anywhere.
- `terrain_results` (a queryable summary/aggregate table) remains **not yet implemented** — Phase 4's calibrated output lives directly in `analysis_artifacts` (real rows, real files) rather than a separate summary table; deferred until a phase that needs cross-artifact aggregate queries.
- Terrain derivatives (slope/aspect) remain **not yet implemented** — deferred to a later phase alongside real DSM-specific processing (§ known limitations in `docs/ARCHITECTURE.md`).
- **Exit criterion met**: attaching a real DEM or GCP CSV reference and running a job produces a real calibrated metric elevation raster and DSM, with honest scale/offset/residual statistics traceable to the actual reference data used, verified via independent `rasterio` inspection of the downloaded artifacts and end-to-end through the real separate worker container, including surviving a full `docker compose down`/`up`. Slope/aspect layers are explicitly deferred (see above), so the DSM-alone portion of the original exit criterion is met while the slope-layer portion moves to a later phase.

## Phase 5 — Terrain workspace (2D/3D visualization) — ✅ complete
- Real visualization API (`app/api/v1/endpoints/visualization.py`, `app/services/visualization.py`): a per-dataset context endpoint reporting real layer/terrain availability (never a fake disabled layer), real raster metadata/preview/window/pixel-value endpoints per artifact, and a real terrain height-grid (metadata + binary Float32) endpoint — all read-only over existing Phase 1-4 rows, **no new database table, no migration**.
- `geospatial/raster_preview.py` (real, decimated-read statistics; PNG previews — true-color passthrough for RGB, a fixed color ramp normalized by real finite min/max for scientific single-band rasters; NoData/NaN/Inf rendered fully transparent, never a fabricated color) and `geospatial/terrain_grid.py` (real, deterministically downsampled DSM height grid, bounded by a configurable `MAX_TERRAIN_DIMENSION`, with a real local coordinate frame — pixel-space if not georeferenced, the actual projected transform if already projected, or a real UTM reprojection via `rasterio.warp.reproject` if geographic).
- Leaflet 2D layer stack (RGB, relative depth, metric elevation, DSM) — a georeferenced dataset is placed on a real OpenStreetMap-compatible basemap using bounds actually reprojected to EPSG:4326 by the backend; a non-georeferenced dataset uses local pixel coordinates and a clear "not georeferenced" banner, never a fabricated location.
- Three.js 3D terrain mesh generated exclusively from the real DSM height grid (never mathematically generated/random/placeholder terrain) — vertex colors or a spatially-verified RGB texture, vertical exaggeration (a rendering-only parameter, clearly labeled, never altering stored data), grid toggle, orbit and first-person/flythrough (WASD + mouse-look) controls, reset/fit-camera derived from the mesh's own real bounds, and full Three.js resource disposal on unmount/dataset switch.
- Layer panel with real per-layer visibility/opacity/legend (actual min/max, never a hardcoded range) and honest "unavailable" explanations (e.g. "Metric elevation unavailable — calibration failed: ...") reflecting the dataset's actual calibration state — reused directly from Phase 4's `calibration_status`/`calibration_metadata`.
- Real cursor-inspection (2D and 3D): the actual stored pixel value, or "No data" for NoData/NaN/Inf — never a fabricated number.
- WebGL feature-detection gates the 3D mode without crashing the workspace on unsupported browsers/devices; the 2D view remains fully usable either way.
- `terrain_results` (a queryable aggregate table) remains **not yet implemented** — Phase 5's visualization reads directly from `analysis_artifacts` on demand rather than a separate summary table, exactly as Phase 4 left it.
- **Exit criterion met**: a completed job (with or without successful calibration) is explorable in both 2D and 3D, every layer backed by a real stored raster or a real "unavailable" reason, and the 3D terrain's height values independently verified (via `rasterio`, outside the application) to match the actual stored DSM cell-for-cell.

## Phase 6 — Semantic segmentation & quality metrics — ✅ complete
> Renamed from this document's original "Semantic features & uncertainty" heading, and the exit criterion below replaces the original one ("confidence layer values vary meaningfully... correlate with genuinely harder regions"). That original framing assumed a genuine per-pixel confidence/uncertainty signal would be available; in practice, no such signal exists for either the segmentation model or Depth Anything V2 (§3.6 of `docs/ARCHITECTURE.md`), and fabricating one from unrelated statistics (depth magnitude, gradient, color) would have violated this project's core "no fabricated scientific output" rule. Phase 6 instead ships real class-agnostic region segmentation plus honestly-scoped, separately-labeled quality indicators — a truthful phase, not a scaled-back one.
- `ai/SemanticEstimator` interface + `MobileSAMEstimator` (MobileSAM, Apache-2.0, pinned GitHub commit, checkpoint verified via real git blob SHA-1) — real class-agnostic automatic mask generation, CPU inference (no local GPU available; CUDA used only if the runtime genuinely detects it). A second, independent model interface from `DepthEstimator` — never forced into a shared "generic prediction" shape.
- Deterministic overlap-resolution policy (largest-first sort, smaller-region-painted-on-top, real post-overlap pixel-area recount) fully documented since it was an explicit open design question, not something the library decides.
- Four new `AnalysisStage` values (`loading_semantic_model`, `semantic_preprocessing`, `semantic_inference`, `writing_semantic`) extend the enum between `validating_results` and `finalizing`, entered only when a job opts in (`enable_semantic_segmentation=true`); a new, independent `semantic_status`/`semantic_metadata` pair on `AnalysisJob` tracks the outcome apart from job status, mirroring Phase 4's calibration pattern exactly (a failed segmentation attempt never fails the job).
- Real single-band `uint32` categorical GeoTIFF output (`semantic_segmentation` artifact, `display_label="Distinct Surface Regions"`) — region IDs only, never a class label; full provenance back to the source dataset and this job's depth artifact via existing conventions, no new schema concept.
- `geospatial/image_quality.py`: real, model-independent sharpness/exposure/luminance/valid-pixel metrics computed for every job (not gated behind segmentation) — explicitly labeled as image properties only, never model confidence or segmentation/elevation accuracy, and never combined with the segmentation model's own mask-stability score or Phase 4's calibration residuals into one number.
- Categorical (region-ID) visualization extending the existing Phase 5 API/UI rather than a parallel system: deterministic golden-angle-hue per-region colors, `Resampling.nearest` exclusively (never blending region IDs), a bounded/summarized legend, and inspection reporting a real region ID + area + mask-stability score — never a class name — while explicitly preserving Phase 5's active-vs-visible-layer inspection fix.
- 3D terrain view left untouched — DSM/metric elevation remains the only height source; semantic regions were not added as a 3D overlay in this phase (documented as future work).
- Uncertainty/confidence layer as originally envisioned: **not implemented, by design** — Depth Anything V2 has no native per-pixel uncertainty, and none is fabricated from other signals. What Phase 6 does provide (image quality, mask stability, calibration residuals) is kept explicitly separate and separately labeled rather than merged into a "confidence" figure.
- **Exit criterion met**: uploading a real image and opting into segmentation produces genuine MobileSAM-detected regions (verified independently via `rasterio` outside the application: real region IDs, real per-region pixel counts, real dimensions matching the source), stored as a real categorical artifact and explorable in the Terrain workspace with a real legend and real per-region inspection values — verified end-to-end through the real separate worker container, including surviving a full `docker compose down`/`up`.

## Phase 7 — Measurements & terrain analysis — ✅ complete
- Real point elevation lookup, planimetric distance, full-resolution terrain profile, and backend-authoritative coordinate inspection (`geospatial/measurements.py`, `app/services/measurement_service.py`) — synchronous REST endpoints extending the Phase 5 visualization API pattern, not a new `AnalysisJob`/RQ pipeline.
- Scientific-honesty gating carried through from Phases 3/4: `relative_depth` is never labeled elevation; `metric_elevation`/`dsm` report a genuinely unspecified real-world unit (never assumed metres, matching the existing `LayerPanel.tsx` convention); an uncalibrated job simply has no elevation artifact to measure from.
- Real CRS handling for distance/profile: a projected CRS's real linear unit (e.g. "metre") read directly from the raster; a geographic CRS reprojected into a real local UTM CRS first (`geospatial.terrain_grid.select_utm_crs`, made public and shared for this reuse) — degrees are never treated as Cartesian metres (independently verified against a real haversine calculation in tests); a non-georeferenced raster reports pixel distance only.
- Real persisted `measurements` table (`app/models/measurement.py`, migration `45fde3a116eb`) — `point_elevation`/`distance`/`profile`/`coordinate` types, full CRUD (`POST`/`GET`/`DELETE`), the exact same ownership chain as every other resource, and a save action that always recomputes server-side rather than trusting a client-supplied result.
- Building/object height estimation — **not implemented**, as anticipated: Phase 6's segmentation layer is class-agnostic (region IDs only, §3.6 of `docs/ARCHITECTURE.md`), so it cannot by itself identify "this region is a building"; a real height-estimation feature would need either a user-selected region + manual confirmation, or a future validated classifier layered on top of the existing regions.
- Fixed a real, previously-unflagged bug found during this phase's own readiness audit: the 3D terrain viewer's raycast click was being interpreted directly as a full-resolution pixel index, when it was actually expressed in the downsampled (≤256px) display grid's own coordinate space — silently sampling the wrong pixel for any DSM larger than the display cap. Fixed via a new backend-authoritative `coordinate_to_pixel` endpoint (georeferenced case) and an exact proportional rescale using real, already-reported dimension ratios (non-georeferenced case); regression-tested against a 2048×2048 source, a non-square raster, all corners, the center point, and out-of-bounds coordinates.
- Frontend: the previously-disabled "Measurements" tab now renders the same `TerrainWorkspace` component as "Terrain" (no duplicate map/3D implementation), with a real measurement-mode selector (always visibly labeled so a plain click is never mistaken for a measurement), a real Recharts elevation-profile chart (`recharts`, a new dependency), and a saved-measurement history panel — all reusing the existing `inspection.ts`/`LayerContext`/`VisualizationContext`/authenticated API client, and explicitly preserving the Phase 5 active-vs-visible-layer inspection fix.
- **Exit criterion met**: a user can click two points in the 3D/2D view and get a real distance/elevation reading computed from stored raster data — verified through the real API and 39 backend tests (unit + integration + ownership + persistence CRUD), including one real end-to-end test against an actual calibrated DEM job. See the Phase 7 completion report for the full real-Docker-E2E verification (independent `rasterio`/GDAL cross-check, container-restart persistence check).

## Phase 8 — Disaster intelligence & terrain-derived hazard screening — ✅ complete
> Renamed from this document's original "Disaster analysis" heading — every deliverable below is a real, terrain-derived **screening** product, never a hydrological/hydraulic simulation or an ML-based prediction (§3.8 of `docs/ARCHITECTURE.md`). The original "flood/inundation extent" and "hazard results" framing is preserved in substance (a real, water-level-driven, per-pixel classification with real area statistics) but described honestly as elevation-threshold screening rather than a simulated extent, since no rainfall/drainage/flow-routing model was built.
- Real slope/aspect terrain derivatives (`geospatial/terrain_derivatives.py`) via Horn's (1981) 3×3 weighted finite-difference method — the same default ArcGIS/QGIS use — with a real ESRI/Horn compass-bearing aspect convention, independently verified against three directional test cases, and a documented flat-terrain sentinel distinct from NoData.
- User-defined water level → real elevation-threshold flood **screening** (`flood_screening` artifact) over an existing calibrated `metric_elevation`/`dsm` artifact — changing the water level and re-running produces a new, genuinely different classification and area statistics, computed fresh from the real stored elevation raster each time, never a client-side visual reinterpretation of one fixed dataset.
- Real affected-area analysis: per-class pixel counts and real areas (`pixel_area × count`, in the CRS's own real linear unit), plus terrain statistics (min/max/mean/median elevation, min/max/mean slope) — no fabricated "impact" score.
- Slope-based landslide susceptibility **screening index** (`landslide_screening` artifact) — configurable degree thresholds (Low/Moderate/High/Very High), never presented as a probability or a percentage chance.
- `disaster_status`/`disaster_metadata` columns on the existing `AnalysisJob` (mirroring `calibration_status`/`semantic_status`) rather than a new `hazard_results` table — a disaster-screening job is a real, standalone `AnalysisJob` (§3.8) whose own artifacts/metadata already carry full provenance, consistent with how Phase 4/6 extended the same table rather than inventing a parallel one.
- **Exit criterion met**: changing the water level and re-running screening triggers a new real backend computation (standalone `AnalysisJob`, real RQ worker execution) and produces a visibly different, correct flood classification and area statistics — verified through the real API, 30 backend tests (pure geospatial math + scientific-honesty + full job lifecycle + ownership + persistence), and a real Docker E2E. See the Phase 8 completion report for full verification detail.

## Phase 9 — Reports & export — ✅ complete
- Real report generation (`app/services/report_builder.py`/`report_render.py`) — a PDF (`reportlab`), a JSON export, a CSV (when tabular data exists), and a ZIP bundle, all built from one real, frozen data snapshot assembled from whatever real Phase 1-8 results (depth/calibration, terrain derivatives, hazard screening, measurements) actually exist for a dataset. GeoTIFF export of individual raster layers was already covered by the existing `.../artifacts/{id}/download` endpoint (Phase 2) and is now additionally reachable via the bundle's `artifacts/` folder — no separate GeoJSON/3D-asset export was built (out of scope for this pass, see below).
- A real, persisted `reports` table (`Report` model, migration `a0d3ac929883`) mirroring the `CalibrationStatus`/`SemanticStatus`/`DisasterStatus` status-tracking precedent, generated asynchronously by a new task enqueued to the SAME RQ `"default"` queue/worker every other analysis job already uses — no new service or container.
- Scientific-honesty carried through into every report: relative depth is never re-labeled elevation, an uncalibrated job's report says so explicitly rather than omitting the section, and flood/landslide sections carry their exact Phase 8 disclaimers verbatim.
- **Exit criterion met**: generating a report for a dataset with real completed analysis produces a real, downloadable PDF whose content (model info, calibration numbers, terrain/hazard statistics, artifact inventory) exactly matches the same dataset's real persisted analysis data — independently verified in tests by re-parsing the generated PDF with `pypdf` and the generated ZIP bundle with `zipfile`, plus a real end-to-end test against actual TERRAIN-X pipeline outputs (not mocked report content). 3D asset export and a dedicated GeoJSON export remain future work if a real need for them emerges.

## Phase 10 — Relative terrain for uncalibrated/non-georeferenced imagery — ✅ complete
> A dedicated Phase 10 readiness audit (comparing the actual repository against the original SIH26175 "single-view height estimation and 3D flythrough" requirement) found that the single most basic case this project is named for — an uncalibrated, single-view photo with no DEM/GCP reference — could not enter the 3D terrain path at all, since 3D terrain required a real, calibrated `dsm` artifact. The audit confirmed the fix was a scope reduction, not new engineering: `geospatial/terrain_grid.py`'s height-grid extraction was already fully generic to any single-band raster, and every scientific artifact (`relative_depth` included) is already resized to the source image's exact dimensions — the only real blocker was one artifact-type gate. The other audit findings (calibration residual visualization, worker-crash reconciliation, audit logs, rate limiting, model-revision pinning) were explicitly deferred, not folded into this phase.
- The job's own real `relative_depth` artifact now backs the 3D terrain (`height_kind="relative_depth"`) whenever no calibrated `dsm` exists — reusing the entire existing terrain-grid/Three.js/RGB-texture/first-person-flythrough pipeline completely unmodified, never a second visualization system.
- A new `height_kind` discriminator (`"elevation"` | `"relative_depth"`, reusing Phase 7's `value_kind` naming convention) on `VisualizationContextOut.terrain`/`TerrainMetadataOut`, additive and backward-compatible — the calibrated path's own response shape and values are unchanged and regression-tested.
- `min_elevation`/`max_elevation` are never populated for a relative-depth-backed terrain (the field names themselves would overclaim a physical unit); new, scientifically-neutral `min_height_value`/`max_height_value` fields are populated in both modes.
- A persistent, unmissable "Relative terrain preview — NOT elevation, NOT a DSM" notice in the Terrain workspace whenever relative mode is active; the RGB-texture toggle is relabeled "RGB texture on relative terrain" (never "orthomosaic"/"orthophoto") in that mode.
- Measurement tools and Phase 9 reports were verified — not merely assumed — unaffected, since both already key off the real artifact type directly rather than off which artifact happens to back the 3D view.
- **Exit criterion met**: a real uncalibrated, non-georeferenced source image (no DEM/GCP reference) produces a real relative_depth artifact that now drives a real 3D terrain mesh with working RGB texture projection and first-person flythrough, honestly labeled throughout — verified via the real API, an expanded backend test suite, and a real Docker E2E. The existing calibrated-DSM path, all existing measurement behavior, and all existing report/export behavior were confirmed unregressed.

## Phase 11 — Reliability, recovery & reproducibility hardening ✅
Scoped by an explicit readiness audit (see the Phase 11 readiness report) to
only the items that were genuine gaps for SIH26175 core-completeness — not a
general enterprise-hardening pass. Rate limiting, audit logging, calibration
residual visualization, storage-orphan cleanup, and production Docker
hardening were all identified but explicitly deferred as P2/P3 (real but not
blocking); see the readiness report for the full candidate evaluation.

- **A — Immutable model revision.** `ai/depth_anything.py`'s `MODEL_REVISION`
  was a moving `"main"` branch reference, unlike MobileSAM's already-pinned
  commit SHA. Repinned to the exact commit `main` resolved to at pin time
  (`5426e4f0f36572d16453bbda7a8389317b1bef99`), confirmed two ways: it is the
  commit already present in this project's local Hugging Face cache (i.e.
  what every prior phase's testing, including Phase 10's browser acceptance,
  actually ran against), and independently confirmed live against the
  Hugging Face Hub API at pin time. A real before/after inference comparison
  (revision="main" vs. the pinned SHA, same input) showed bit-for-bit
  identical output (max abs diff 0.0) — expected, since they were always the
  same commit; the fix is about reproducibility going forward, not a
  behavior change today.
- **B — Explicit job timeout + stale/crashed job reconciliation.** RQ's
  implicit 180s default timeout was replaced with an explicit, empirically
  justified `ANALYSIS_JOB_TIMEOUT_SECONDS=900` (see `app/core/config.py` for
  the real measured rationale: a real depth+semantic job on the largest
  allowed input took 170.7s end-to-end, 145.7s of which was MobileSAM
  inference alone — within ~10s of the old default). A new
  `app/services/job_reconciliation.py` reconciles any `running` job whose
  `updated_at` (already a real, existing "heartbeat" — it advances on every
  pipeline stage transition — no new column needed) has been frozen for
  longer than `STALE_JOB_AFTER_SECONDS=1200` to `failed`, with an honest
  error message. Runs as a periodic background task inside the backend
  process (`app/main.py`), so it does not depend on the worker itself
  recovering. Verified with a **real** Docker crash: a genuine long-running
  job was `docker kill -9`'d mid-`semantic_inference`; the job row was
  confirmed stuck `running` forever even after the worker container was
  brought back up; the real, already-running reconciliation sweep (in the
  live backend process, not a test harness) then correctly reconciled it to
  `failed` with the honest message, and the dataset's active-job lock was
  released, allowing a new job to be submitted immediately.
- **C — Running-job cooperative cancellation.** Phase 2's checkpoint
  infrastructure (`_raise_if_cancelled`, ~10 call sites across every
  pipeline stage) was already fully built and dormant — only
  `cancel_job`'s atomic UPDATE (`WHERE status='queued'`) blocked it from
  ever being reached externally. Widened to `WHERE status IN ('queued',
  'running')`; the existing checkpoints needed no changes at all. The
  completion and failure write-paths in `analysis_execution.py` were made
  equally conditional (`WHERE status='running'`) to close a lost-update race
  at the *other* end of execution (a job finishing/failing in the same
  instant a cancellation is requested) — exactly one final state is ever
  persisted, verified by a real forced race test. Cancellation is explicitly
  cooperative and never claims to interrupt an in-flight model inference
  call — verified end-to-end with a real, genuinely-blocking `predict()`
  call running in a separate OS thread while a real HTTP cancel request
  raced it. A minimal frontend affordance (`AnalysisPanel.tsx`) now shows
  the existing Cancel button for `running` jobs too, with an honest tooltip
  distinguishing immediate (queued) from cooperative (running) cancellation.
- **Zero migrations.** Every mechanism above reused existing columns
  (`updated_at`) or pure application logic — no schema change was needed.
- **Testing**: 15 new backend tests (229/229 total, up from 214), all real
  integration tests against the actual database/pipeline (no mocked
  cancellation/reconciliation logic) — covering queued/running/completed/
  failed cancellation attempts, the claim-race (pre-existing) and
  completion-race (new) atomicity properties, checkpoint observation via a
  real blocking call in a separate thread, artifact preservation across a
  mid-pipeline cancellation, and stale/active/completed/failed/cancelled/
  queued reconciliation classification. Frontend: 57/57 unchanged, tsc/
  eslint/build clean. Verified against a fresh, no-cache Docker rebuild of
  the backend/worker images.
- **Deferred (see readiness report for full rationale)**: retry policy,
  rate limiting, audit logging, calibration residual visualization,
  dependency/security hardening beyond the model-revision pin, storage
  orphan cleanup, production Docker hardening.
- Load/perf pass on the AI pipeline (batching/queueing under concurrent jobs); revisit the Phase 2/3 known limitations (worker crash reconciliation, job timeout handling, retry policy, cancellation-during-inference) — see `docs/ARCHITECTURE.md` §7 — if real usage patterns make them worth addressing. Pin `ai/depth_anything.py`'s `MODEL_REVISION` to an exact commit SHA for production reproducibility.
- Calibration diagnostic visualization (a real per-pixel/per-sample residual view and/or a DEM-reference comparison layer in the Terrain workspace) — identified in the Phase 10 readiness audit as a real, valuable gap (P1) but deferred out of Phase 10's scope.
- Cancellation of a `running` job, not just a `queued` one — identified in the same audit (P1), deferred.

## P1-2 — Calibration quality gate ✅
- A calibration is `CALIBRATED` only if three criteria pass:
  - **G0:** held-out validation is feasible.
  - **G1:** the fitted scale has the expected sign (+1, from the depth convention and the overhead calibration geometry).
  - **G2:** spatially blocked (DEM) or leave-one-out (GCP) held-out skill is > 0 against a training-mean baseline.
- Otherwise it is the existing `FAILED`, with every diagnostic persisted. There is no enum change, and production a/b are bit-identical.
- The policy settings are engineering acceptance values, not validated accuracy standards, and are persisted with every result.

## P1-3 — Bare-earth estimate (DTM) + nDSM ✅
- A raster progressive morphological filter (Zhang et al. 2003; pure numpy, van Herk/Gil-Werman openings) runs only after a gate-passed calibration.
- It writes `dtm` and `ndsm = dsm − dtm` on the DSM's grid: float32, NoData −9999, with full provenance.
- DTM ≤ DSM by construction, so nDSM ≥ 0 and is never clipped.
- Soft failure via `ground_filter_status`/`ground_filter_metadata` (one migration, `b7e2f1c4d9a3`).
- **Raster-derived estimates, not measurements.** Consumers: 2D layers and inspection, DTM elevation measurements, reports and bundles.
- nDSM measurements, per-object/building heights, disaster screening on DTM/nDSM, DTM/nDSM as 3D terrain, and SMRF-style interpolation remain future work.

## P1-5 — Calibration residual visualization ✅
- For gate-passed calibrations only, a `calibration_residuals` GeoJSON point artifact (WGS84) is written: one point per valid calibration sample, never interpolated, never a raster.
- `residual = predicted − reference`, in the reference's units. Held-out residuals (P1-2's own per-sample CV predictions, taken verbatim) are the default; in-sample fit residuals are a labelled secondary view.
- Consumers: the residuals API, 2D markers with a legend and tooltips, report JSON/PDF/CSV sections, and the ZIP bundle. No migration.
- A metric-minus-DEM difference raster, 3D residual display, residuals for gate-rejected fits, and backfill of older jobs remain out of scope.

## P1-6 — 2D georegistration + backend-authoritative pixel resolution ✅
- Each 2D layer is drawn on its own EPSG:3857 overlay grid, with exact corners (`map_overlay_bounds`, `/map-preview`). It is transparent outside the footprint and at NoData, and there is no envelope-stretch fallback.
- Georeferenced 2D clicks carry only lng/lat. Inspection and measurements resolve them on the backend (`coordinate_to_pixel`) against the sampled layer's own raster.
- Fixes wrong-pixel reads away from the UTM central meridian (up to 14 px for a 5 km scene). Verified on a new off-meridian fixture.
- Unchanged: 3D, measurement algorithms, stored artifacts, historical measurements, and migrations (none).

## P1-7 — Terrain-aware first-person flythrough ✅
- Flight follows the rendered mesh surface. Controls: WASD relative to heading, Space/Shift vertical, +/- speed, F terrain-follow (off by default).
- The camera never goes below ground + minimum clearance anywhere along a step, with no tunnelling at any dt. Nothing is invented over NoData.
- Flight is limited to the footprint + 10%, and the entry pose is deterministic.
- A HUD shows map coordinate, display-grid ground value, height above ground, heading, speed and follow state. DSM values have exaggeration removed; relative depth is labelled unitless.
- Frontend only: no backend change and no migration.

## P1-8 — Waypoint flythrough with playback and recording ✅
- Waypoints come from 3D clicks or 2D clicks. Georeferenced 2D clicks go through the new read-only `terrain/local-coordinate` endpoint, which uses the same CRS logic as the terrain grid.
- The path is a centripetal Catmull-Rom curve sampled into an authoritative polyline (≤0.25 cell). Target clearance holds along the whole path, proved at every rendered-triangle crossing.
- Playback is deterministic, with play, pause, resume, restart, stop and speed controls. WebM recording works where the browser supports it and is disabled with the reason where it doesn't. A path JSON can be downloaded.
- No database change and no migration.

## P1-9 — 3D terrain mesh export (GLB) ✅
- A physical GLB built from the authoritative terrain grid, at 256 or 512 only: cell-centre vertices and raw values, with no exaggeration, gamma or centring. NoData and sky stay holes, and regions stay disconnected.
- The source texture is embedded only when it aligns, otherwise omitted with the reason. CRS (WKT), origin, axis mapping, units, `height_kind` and provenance are in `asset.extras`.
- Relative depth is exported raw and unitless and never called elevation.
- Generated in memory with a concurrency limit. No temporary files, no persistence, no migration.

## Explicitly out of scope for v1 (documented, not silently dropped)
- Multi-GPU distributed inference.
- Real-time video/streaming input.
- Automatic GCP detection (GCPs are user-supplied for v1).
