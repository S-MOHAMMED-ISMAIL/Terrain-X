# TERRAIN-X

Single-View Terrain Reconstruction and Disaster Intelligence Platform — SIH26175 (ISRO / Department of Space).

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), [`docs/ROADMAP.md`](docs/ROADMAP.md), and [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for the full design, phased plan, and coding conventions.

## Status

**Phase 0 complete**: local full-stack foundation — Docker Compose (PostgreSQL/PostGIS, Redis, FastAPI backend, RQ worker, React frontend), JWT auth, and project management CRUD with ownership enforcement.

**Phase 1 complete**: real dataset ingestion — authenticated multipart upload, server-side format detection (never trusting client extension/MIME), real raster metadata extraction via GDAL/rasterio (dimensions, bands, actual CRS/georeferencing), persistent file storage, and a dataset UI inside each project's workspace.

**Phase 2 complete**: real analysis job orchestration — an `AnalysisJob` lifecycle (`queued → running → completed/failed/cancelled`) executed by the separate RQ worker container, with real stage-by-stage verification of the dataset (re-opens the stored file via GDAL, cross-checks structural metadata, computes real per-band pixel statistics) rather than a simulated pipeline. No depth estimation, calibration, DSM generation, or terrain output exists yet — see `docs/ARCHITECTURE.md` for the explicit boundary.

**Phase 3 complete**: real monocular depth estimation — the worker loads [Depth Anything V2 (Small)](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf) (Apache-2.0, CPU inference — no NVIDIA GPU required or used) and runs genuine inference over an uploaded RGB image, producing a real single-band float32 **relative depth** raster (`AnalysisArtifact`, `artifact_type=relative_depth`) — explicitly *not* metric elevation, *not* a DSM, and not calibrated in any way. See `docs/ARCHITECTURE.md` for the model card, input policy, output semantics, and known limitations.

**Phase 4 complete**: real metric calibration and DSM generation — attach a real DEM raster or a surveyed Ground Control Point CSV as a reference, and the worker fits a documented affine model (`Z = a·D + b`, OLS + robust outlier rejection) between Phase 3's relative depth and the real reference elevations it samples (CRS-aware, never index-matched), producing a real calibrated **metric elevation** raster and a **DSM** raster (`AnalysisArtifact`, `artifact_type=metric_elevation`/`dsm`) alongside honest validation metrics (MAE/RMSE/bias — never an "accuracy percentage"). A non-georeferenced source, a missing/invalid reference, or too few valid samples correctly produces `calibration_status=failed` (with a real reason) rather than a fabricated result — the job itself still completes with its relative-depth artifact either way. See `docs/ARCHITECTURE.md` §3.4 for the full calibration mathematics, CRS handling, and scientific limitations.

**Phase 5 complete**: a real interactive Terrain/GIS workspace over the actual Phase 3/4 artifacts — a Leaflet 2D viewer (real geographic bounds, reprojected to EPSG:4326 server-side, for a georeferenced dataset; local pixel coordinates and a clear banner for a non-georeferenced one) and a Three.js 3D terrain viewer whose mesh is built **exclusively from the real DSM height grid** (never mathematically generated). Layer availability, legends, and cursor-inspection values are all real — a layer that doesn't exist yet (e.g. metric elevation before calibration succeeds) is shown as genuinely unavailable with the actual reason, never a fake placeholder. See `docs/ARCHITECTURE.md` §3.5 for the full visualization architecture, CRS handling, and known limitations.

**Phase 6 complete**: real, **class-agnostic** region segmentation — an opt-in job parameter runs [MobileSAM](https://github.com/ChaoningZhang/MobileSAM) (Apache-2.0, pinned commit, CPU inference) over the source image and produces a real categorical raster of detected regions (`AnalysisArtifact`, `artifact_type=semantic_segmentation`, `display_label="Distinct Surface Regions"`) — **region IDs are not semantic land-cover/object labels**; this model has no notion of "building," "road," or "vegetation," and none is invented. Every job also gets real, separately-labeled image-quality indicators (sharpness/exposure/valid-pixel-ratio) computed directly from the uploaded pixels — never combined into a fake "AI confidence" score, and explicitly distinct from the segmentation model's own mask-stability signal and from Phase 4's calibration residuals. A failed segmentation attempt never fails the job — the real depth/calibration result already produced remains valid on its own. See `docs/ARCHITECTURE.md` §3.6 for the full model card, deterministic rasterization policy, quality-metrics documentation, and scientific-honesty statement.

**Phase 7 complete**: real point elevation, planimetric distance, full-resolution terrain-profile, and coordinate-inspection measurements over the actual Phase 3/4/6 artifacts, with a real persisted history (`measurements` table). Every measurement is honestly labeled: `relative_depth` is never called elevation, a calibrated elevation's real-world unit is reported as genuinely unspecified (never assumed metres), a geographic-CRS distance is computed via a real local-UTM reprojection (never treating degrees as metres), and a non-georeferenced raster reports pixel distance only. Saving a measurement always recomputes it server-side — a client can never persist a fabricated result. This phase also fixed a real, previously-unflagged bug: the 3D terrain viewer's click handling was silently sampling the wrong pixel for any DSM larger than its ~256px display grid, now resolved against the artifact's own real full-resolution transform. See `docs/ARCHITECTURE.md` §3.7 for the full measurement architecture, CRS handling, persistence model, and the coordinate-space bug fix.

**Phase 8 complete**: real terrain-derived hazard **screening** — slope/aspect terrain derivatives (Horn's method) plus flood and landslide susceptibility screening, computed directly from an existing, already-calibrated `metric_elevation`/`dsm` artifact. A standalone `AnalysisJob` (never combined with depth/calibration/segmentation in the same run) cites the elevation artifact via `disaster_source_artifact_id`; the worker reprojects a geographic CRS to a real local UTM CRS first, computes real slope/aspect rasters, an elevation-threshold flood screen, and a slope-threshold landslide susceptibility index, and persists all four as new `AnalysisArtifact` rows (`slope`, `aspect`, `flood_screening`, `landslide_screening`) with a real fixed value→label legend. **Flood screening is never called a prediction** (no rainfall, drainage, or hydraulic simulation); **landslide screening is never presented as a probability or percentage chance** — both carry their real methodology disclaimer verbatim in `AnalysisJob.disaster_metadata`. A real failure here fails the whole job (`disaster_status=failed`), unlike Phase 4/6's soft-failure add-ons, since a disaster job has no other independently valid result to fall back on. See `docs/ARCHITECTURE.md` §3.8 for the full terrain-derivative mathematics, hazard-screening methodology, scope disclaimers, and known limitations.

**Phase 9 complete**: real report generation and export — a persisted `Report` row per dataset, generated asynchronously by the same RQ worker from whichever real Phase 1-8 results actually exist (depth/calibration, terrain derivatives, hazard screening, measurements), rendered into a real PDF (`reportlab`), a real JSON export (the exact data the PDF was built from), a real CSV (only when genuine tabular data exists), and a real ZIP bundle (report + JSON + CSV + every real generated raster artifact, archived from its existing storage location). A report has no partially-valid fallback — a real failure marks the whole report `failed` with the real reason. See `docs/ARCHITECTURE.md` §3.9 for the full report architecture and scientific-honesty statement.

**Phase 10 complete**: a real 3D terrain view for uncalibrated/non-georeferenced imagery — the single-view case this project is named for. Previously, 3D terrain required a successful DEM/GCP calibration; now, the job's own real `relative_depth` artifact backs the terrain when no calibrated DSM exists, reusing the entire existing terrain-grid/Three.js/texture/flythrough pipeline unmodified (it was already generic to any single-band raster). An explicit `height_kind` (`"elevation"` | `"relative_depth"`) discriminator, never populating elevation-shaped fields for the relative case, and a persistent "Relative terrain preview — NOT elevation, NOT a DSM" notice keep the two cases impossible to confuse. See `docs/ARCHITECTURE.md` §3.10 for the full architecture and scientific-honesty statement.

**Phase 11 complete**: reliability, recovery, and reproducibility hardening — scoped by an explicit readiness audit to only the items that were genuine gaps, after confirming the full SIH26175 pipeline had no remaining functional gap. The Depth Anything model revision is now pinned to an exact, verified immutable commit (never the moving `"main"` branch it used before). Analysis jobs now run under an explicit, empirically-justified timeout instead of RQ's implicit default, and a periodic background sweep reconciles any job whose worker crashed or ran past that timeout — verified with a real Docker worker kill, not a mock. Cancellation now works cooperatively for a job that is already `running`, not just `queued`, reusing the checkpoint infrastructure already built for this in Phase 2 — honestly incapable of interrupting an in-flight model inference call, and says so. See `docs/ARCHITECTURE.md` §3.11 for the full architecture, empirical rationale, and real crash-verification record.

**P1-2 complete**: calibration quality gate. A DEM/GCP calibration is accepted only if three conditions hold:
- held-out validation is feasible;
- the fitted scale has the sign the depth convention implies;
- spatially blocked (DEM) or leave-one-out (GCP) cross-validation shows depth predicting held-out reference elevations better than ignoring depth.

Rejected fits keep all their diagnostics and fall back to relative terrain. The gate is an engineering acceptance policy, not an accuracy guarantee. See `docs/ARCHITECTURE.md` §3.4.

**P1-3 complete**: bare-earth **estimate** (DTM) and **nDSM**. After a gate-passed calibration, a raster progressive morphological filter (pure numpy) derives `dtm` (estimated bare-earth elevation) and `ndsm = dsm − dtm` (estimated height above that ground) on the DSM's own grid.
- **These are raster-derived estimates, not a measured bare-earth model or measured object heights.** No point cloud or classified ground exists.
- `dsm`, `metric_elevation` and `relative_depth` are unchanged.
- DTM/nDSM appear as 2D layers, in pixel inspection, in reports and in bundles.
- DTM is measurable as elevation. nDSM measurements, disaster screening on DTM/nDSM, and DTM/nDSM as 3D terrain are deliberately not supported.

See `docs/ARCHITECTURE.md` §3.12.

## Running locally

Prerequisites: Docker Desktop with Compose v2, WSL2 backend (Windows).

```bash
cp .env.example .env   # already done for local dev; edit if you need different ports/secrets
cd docker
docker compose up --build
```

Then open:
- Frontend: http://localhost:5173
- Backend API docs: http://localhost:8000/docs
- Backend health: http://localhost:8000/api/v1/health

Register an account, log in, create a project, then open it to upload a dataset (JPEG/PNG/TIFF/GeoTIFF) and run a real depth-estimation analysis job on it from the Analysis tab — everything is backed by the real PostgreSQL database, a persistent file store, a real separate worker process, and (from Phase 3) a real downloaded AI model, no mock data.

The first analysis job on a fresh environment downloads the ~99 MB depth model from Hugging Face on first use (needs outbound network access once) and caches it under the persistent `storage/model_cache/` bind mount — later jobs, including after a container restart, load it from that local cache.

## Running backend tests

```bash
docker compose -f docker/docker-compose.yml exec backend python -m pytest -v
```

## Dataset storage

Uploaded files are never stored in PostgreSQL — only metadata and a storage key reference are. The actual bytes live under `storage/data/` (gitignored, bind-mounted into the backend/worker containers), laid out as:

```
storage/data/projects/<project_uuid>/datasets/<dataset_uuid>/original.<ext>
```

The dataset's UUID — not the client-supplied filename — determines the on-disk path, which prevents path traversal and filename collisions. This survives `docker compose restart`/container recreation because it's a bind mount to the host, not container-internal storage. See `storage/backend.py` for the storage abstraction (swappable for S3-compatible storage later without changing calling code) and `docs/ARCHITECTURE.md` for the full ingestion pipeline.

Supported formats: JPEG, PNG, TIFF, GeoTIFF. Format is detected from the file's actual magic bytes, never from its extension or client-supplied Content-Type. Whether a TIFF is georeferenced is decided by whether GDAL reports a real CRS for it — a `.tif` extension alone never implies georeferencing. Max upload size is configurable via `MAX_UPLOAD_SIZE_MB` in `.env`.

## Analysis jobs

`POST /api/v1/projects/{project_id}/datasets/{dataset_id}/analysis` creates an `AnalysisJob` row and enqueues it to Redis/RQ; the separate `worker` container picks it up and runs it through real stages — `queued → preparing → validating_input → loading_model → preprocessing → inference → writing_depth → finalizing → completed` (or `failed`/`cancelled`), extended to `... → writing_depth → calibrating → writing_metric_elevation → writing_dsm → validating_results → finalizing → completed` whenever the job requests calibration, and further extended to `... → validating_results → loading_semantic_model → semantic_preprocessing → semantic_inference → writing_semantic → finalizing → completed` whenever the job requests segmentation (see below) — either, both, or neither may be requested independently. The frontend's Analysis tab polls `GET /api/v1/projects/{project_id}/analysis` at a configurable interval (`VITE_ANALYSIS_POLL_INTERVAL_MS`) while a job is queued/running and stops once it reaches a terminal state — the database is always the source of truth, never a client-side timer. At most one active job per dataset is enforced by a database constraint, not just an app-level check. See `docs/ARCHITECTURE.md` for the full lifecycle, failure handling, and cancellation semantics.

## Depth estimation (Phase 3)

**Model**: [Depth Anything V2 — Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf) (Apache-2.0). Loaded via `transformers`, CPU-only (this project's reference environment has no NVIDIA GPU; the code detects CUDA at runtime and only uses it if genuinely available, never assumes it).

**Input policy**: depth estimation requires 8-bit RGB imagery — exactly 3 bands, or 4 bands for PNG (alpha dropped). Grayscale, ambiguous 4-band TIFF/GeoTIFF, non-uint8 dtypes, and oversized images (over `MAX_DEPTH_INPUT_DIMENSION_PX`) are rejected with a clear error rather than silently reinterpreted.

**Output**: a real single-band float32 GeoTIFF (`AnalysisArtifact`, `artifact_type=relative_depth`) — the model's actual prediction, resized back to the source resolution, never a fabricated array or an 8-bit-only visualization. Preserves the source's real CRS/transform when the source was genuinely georeferenced; never fabricates georeferencing for a plain photo. **This is relative, unitless, uncalibrated depth — not metric elevation, not a DSM.** Larger values mean closer to the camera; see `docs/ARCHITECTURE.md` for the full model card and limitations (scale ambiguity, training-domain mismatch with remote-sensing imagery, etc.).

Retrieve results via `GET /api/v1/projects/{project_id}/analysis/{job_id}/artifacts` (list) and `.../artifacts/{artifact_id}/download` (authenticated download).

## Metric calibration + DSM (Phase 4)

Relative depth alone has no real-world scale. To get real metric elevation, upload a **reference dataset** into the same project before creating the analysis job:

- **DEM reference**: upload any georeferenced raster (GeoTIFF, etc.) with `role=dem_reference` — it's rejected at upload (`status=invalid`) if it has no CRS, since an un-georeferenced DEM can never be aligned to a source image.
- **GCP reference**: upload a CSV with an `x,y,z` header (case-insensitive) and pass `gcp_crs` (e.g. `EPSG:32633`) — the CRS of a CSV's coordinates is never assumed, only declared explicitly.

```bash
curl -F "file=@dem.tif" -F "role=dem_reference" \
  -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/datasets

curl -F "file=@gcps.csv" -F "role=gcp_reference" -F "gcp_crs=EPSG:32633" \
  -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/datasets
```

Then create the analysis job citing exactly one reference (both is rejected):

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"parameters":{"version":"v1","dem_reference_dataset_id":"'"$DEM_ID"'"}}' \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/datasets/$SOURCE_ID/analysis
```

The worker samples real, CRS-aware `(relative_depth, reference_elevation)` pairs (a uniform grid over the DEM, or each GCP's declared coordinates reprojected into the source image), fits `Z = a·D + b` by OLS with iterative sigma-clipping to reject outliers, and — only on success — writes a real calibrated **metric elevation** raster and a **DSM** raster, both float32 GeoTIFFs preserving the source's CRS/transform. The job's `calibration_status` (`uncalibrated`/`calibrating`/`calibrated`/`failed`) and `calibration_metadata` (scale/offset, sample/inlier/outlier counts, MAE/RMSE/bias, CRSs involved, and a stated limitations string) are always real and never fabricated — a non-georeferenced source or too few valid samples correctly yields `failed`, not a guessed result, and the analysis job still completes with its relative-depth artifact regardless. See `docs/ARCHITECTURE.md` §3.4 for the full calibration mathematics, CRS handling, robust fitting, and scientific limitations (scale ambiguity, no terrain/object-top separation, nearest-pixel GCP correspondence).

## Terrain/GIS workspace (Phase 5)

Open a project's **Terrain** tab, pick a source dataset, and the workspace loads its real visualization context — which layers actually exist (`rgb`, `relative_depth`, `metric_elevation`, `dsm`), with real dimensions/CRS/value ranges, or an honest "unavailable" reason (e.g. "Metric elevation unavailable — calibration failed: ..."). Nothing here is mock data:

- **2D map**: real Leaflet viewer. A georeferenced dataset is placed on an OpenStreetMap-compatible basemap using bounds the backend actually reprojects to EPSG:4326 (`rasterio`/GDAL/PROJ); a non-georeferenced dataset uses plain pixel coordinates with a clear "Local coordinate view" banner instead — never a fabricated location.
- **3D terrain**: real Three.js viewer. The mesh is built directly from a `GET .../artifacts/{dsm_artifact_id}/visualization/terrain/grid` binary Float32 height grid (metadata via the sibling `.../terrain/metadata` endpoint) — orbit controls, WASD+mouse-look flythrough, a vertical-exaggeration slider (rendering-only, never touches stored data), grid toggle, and an RGB texture applied only when the source image's pixel dimensions actually match the terrain grid's.
- **Layers/legends**: real per-layer min/max (from persisted artifact metadata, or computed on demand), opacity/visibility controls, and cursor inspection showing the actual stored pixel value (or "No data").
- Large rasters are never sent to the browser at full resolution — the backend serves decimated PNG previews (`PREVIEW_MAX_DIMENSION`, default 1024px) and a downsampled terrain grid (`MAX_TERRAIN_DIMENSION`, default 256px); both are configurable via `.env`.

See `docs/ARCHITECTURE.md` §3.5 for the full API surface, CRS handling (projected vs. geographic vs. non-georeferenced), and known limitations.

## Semantic segmentation + quality metrics (Phase 6)

Opt into real region segmentation by setting `enable_semantic_segmentation: true` when creating the analysis job:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"parameters":{"version":"v1","enable_semantic_segmentation":true}}' \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/datasets/$SOURCE_ID/analysis
```

The worker loads **MobileSAM** (a real class-agnostic Segment Anything distillation, Apache-2.0, pinned GitHub commit, checkpoint verified via its real git blob SHA-1) and runs genuine automatic mask generation over the source RGB image — no prompts, no user interaction required. The result is a real single-band `uint32` categorical GeoTIFF (`AnalysisArtifact`, `artifact_type=semantic_segmentation`) where `0` is background/NoData and each positive integer is one detected region's arbitrary ID, assigned by a documented deterministic policy (largest-area region first, smaller regions painted on top where they overlap). **Region IDs are not, and must never be presented as, semantic land-cover or object classes** — this model cannot tell a building from a road from a shadow. The job's `semantic_status` (`not_requested`/`processing`/`completed`/`failed`) and `semantic_metadata` (model/device/timing, region count, per-region area/bbox/mask-stability score) work exactly like Phase 4's `calibration_status` — a failed segmentation attempt never fails the job, and the real depth/calibration result already produced stays valid.

Every job (whether or not segmentation is requested) also gets real, model-independent **image-quality indicators** in its `execution_summary.image_quality` — sharpness (a Laplacian-variance blur proxy), under/overexposed pixel fractions, luminance range, and valid-pixel ratio — computed directly from the uploaded RGB pixels. These measure the *image's own* properties only: never model confidence, never segmentation correctness, never elevation accuracy. Depth Anything V2 has no native per-pixel uncertainty, and Phase 6 does not fabricate one — anywhere this would be shown, it's stated as "unavailable for this model."

In the Terrain workspace, toggle the **Distinct Surface Regions** layer to see a deterministic per-region color legend (bounded to a real "+N more" summary for a heavily-segmented image, never thousands of swatches) and click a region to inspect its real ID, pixel area, and model-reported mask stability — never a class name. See `docs/ARCHITECTURE.md` §3.6 for the full model card, the deterministic rasterization policy, quality-metrics documentation, and the complete scientific-honesty statement.

## Measurements & terrain analysis (Phase 7)

Open the **Measurements** tab (the same Terrain workspace, with measurement mode enabled) and pick a mode: point elevation, distance, profile, or coordinate. Real point/distance/profile/coordinate values can also be queried directly:

```bash
# Real point elevation (or relative-depth reading, honestly labeled either way)
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/projects/$PROJECT_ID/analysis/$JOB_ID/artifacts/$ARTIFACT_ID/measurements/point?row=10&col=10"

# Real planimetric distance between two points
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/projects/$PROJECT_ID/analysis/$JOB_ID/artifacts/$ARTIFACT_ID/measurements/distance?row1=0&col1=0&row2=10&col2=0"

# Save a measurement (the server recomputes it server-side — never trusts a client-supplied result)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"measurement_type\":\"distance\",\"analysis_job_id\":\"$JOB_ID\",\"artifact_id\":\"$ARTIFACT_ID\",\"row1\":0,\"col1\":0,\"row2\":10,\"col2\":0}" \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/measurements
```

**Scientific rules enforced throughout**: `relative_depth` values are never called elevation; a job that never calibrated has no `metric_elevation`/`dsm` artifact to measure elevation from at all; a calibrated elevation's real-world unit is reported as genuinely **unspecified** (never assumed metres — the reference data's own unit is unknown); a geographic (lat/lon) source's distance is computed via a real reprojection into a local UTM CRS, never by treating degrees as metres; and a non-georeferenced raster reports pixel distance only, never a fabricated real-world unit. Saved measurements (`measurements` table) persist across restarts and are only ever visible to their owning project's user, via the exact same ownership chain as every other resource.

See `docs/ARCHITECTURE.md` §3.7 for the full measurement architecture, the terrain-profile full-resolution sampling policy, the CRS/reprojection handling, and the 3D-terrain coordinate-space bug fix.

## Disaster intelligence & terrain-derived hazard screening (Phase 8)

Run real hazard screening against an existing calibrated elevation artifact — the Terrain workspace's **Disaster screening** panel lets you pick a `metric_elevation`/`dsm` layer, a water level, and which screenings to run, or call the API directly:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"parameters":{"version":"v1","disaster_source_artifact_id":"'"$DSM_ARTIFACT_ID"'","run_flood_screening":true,"water_level":121.0,"run_landslide_screening":true}}' \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/datasets/$SOURCE_ID/analysis
```

This creates a standalone `AnalysisJob` (`disaster_source_artifact_id` set — never combined with a calibration/segmentation request in the same job) that the worker runs through its own real stages: `validating_input → loading_terrain_data → computing_slope → computing_aspect → computing_terrain_statistics → running_flood_screening → running_landslide_screening → writing_hazard_artifacts → finalizing → completed`. Real terrain derivatives are always computed once the cited elevation artifact is validated:

- **Slope/aspect**: Horn's (1981) 3×3 weighted finite-difference method — the same default ArcGIS/QGIS use — computed on the real elevation raster after reprojecting a geographic CRS to a real local UTM CRS (never mixing degrees with the elevation's own z-unit). Aspect is a real compass bearing (0°=North, clockwise); a genuinely flat pixel (slope==0) reports a documented sentinel (`-1.0`), distinct from NoData, since its downslope direction is mathematically undefined.
- **Flood screening** (`flood_screening` artifact): a real elevation-threshold screening scenario — `elevation <= water_level` is classified "potentially inundated," everything else "not potentially inundated." This is **never called a prediction**: it does not model rainfall, drainage, rivers, flow routing, infiltration, tides, storm surge, or temporal flood dynamics.
- **Landslide screening** (`landslide_screening` artifact): a real, transparent slope-based **susceptibility screening index** (configurable degree thresholds, sensible defaults) — classified Low/Moderate/High/Very High. This is **never presented as a probability or a percentage chance of occurrence** — it does not account for soil, geology, hydrology, or triggering events.

Both hazard classifications, plus real terrain statistics (min/max/mean elevation and slope, valid-pixel counts), are persisted verbatim in the job's `disaster_metadata`, along with the exact scope disclaimer: *"Disaster outputs are terrain-derived screening products and are not substitutes for validated hydrological, hydraulic, geotechnical, or operational disaster models."* A real failure anywhere in this pipeline (e.g. no valid elevation pixels) fails the whole job — unlike Phase 4/6's soft-failure calibration/segmentation add-ons, a disaster job has no independently valid fallback result. In the Terrain workspace, `slope`/`aspect`/`flood_screening`/`landslide_screening` appear as real layers with a fixed value→label legend (flood/landslide only — never fabricated for the continuous slope/aspect rasters), and cursor inspection reports the real classification/value at the clicked pixel.

See `docs/ARCHITECTURE.md` §3.8 for the full terrain-derivative mathematics, the flood/landslide methodology and class encodings, CRS/reprojection handling, the standalone-job architecture, and the complete scientific-honesty statement.

## Reports & export (Phase 9)

Generate a real report for any dataset with at least one completed analysis — the Reports tab lets you pick a dataset and click **Generate report**, or call the API directly:

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/datasets/$SOURCE_ID/reports

# Poll status, then download once status == "completed"
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/reports/$REPORT_ID

curl -H "Authorization: Bearer $TOKEN" -o report.pdf \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/reports/$REPORT_ID/pdf
curl -H "Authorization: Bearer $TOKEN" -o analysis.json \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/reports/$REPORT_ID/json
curl -H "Authorization: Bearer $TOKEN" -o bundle.zip \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/reports/$REPORT_ID/bundle
```

This creates a real, persisted `Report` row and enqueues real generation to the same RQ worker every other analysis job already runs on (no separate job system). The worker gathers whatever real Phase 1-8 results actually exist for that dataset — the latest completed depth/calibration job, the latest completed disaster-screening job (both reused from Phase 5's own dataset-to-job lookup, never re-implemented), the artifacts each produced, and any measurements taken against them — assembles one real, frozen data snapshot, and renders it into:

- **A real PDF** (`reportlab`, no static template): project/dataset identity, real depth-model info, real calibration scale/offset/validation metrics (or an honest "uncalibrated" note), real terrain statistics and flood/landslide summaries (or an honest "no disaster screening" note), a real generated-artifact inventory, a real measurement table, and every applicable scientific disclaimer verbatim.
- **A real JSON export** (`GET .../reports/{id}/json`) — the exact same data snapshot the PDF was rendered from, so the two can never disagree.
- **A real CSV** (`GET .../reports/{id}/csv`) — only produced when genuine tabular data exists (measurements, terrain statistics, or hazard class counts); a dataset with none of those has no CSV to download, never an empty placeholder file.
- **A real ZIP bundle** (`GET .../reports/{id}/bundle`) — `report.pdf` + `analysis.json` + `report.csv` (if present) + every real generated raster artifact archived directly from its existing storage location, under `artifacts/`.

A report has no partially-valid fallback (unlike Phase 4/6's soft-failure calibration/segmentation add-ons): a real failure (e.g. the dataset's analysis was deleted between report creation and generation) marks the whole report `failed` with the real reason, never a partial or fabricated result. See `docs/ARCHITECTURE.md` §3.9 for the full report architecture, data-snapshot schema, and scientific-honesty statement.

## Relative terrain for uncalibrated imagery (Phase 10)

A plain uploaded photo with no DEM/GCP reference now still gets a real 3D terrain view. Previously, 3D terrain required a successful calibration (a real `dsm` artifact); an uncalibrated or non-georeferenced source only ever showed a flat 2D relative-depth layer. The visualization context now reports an explicit `height_kind`:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/projects/$PROJECT_ID/datasets/$SOURCE_ID/visualization/context
# "terrain": { "available": true, "height_kind": "relative_depth", "artifact_id": "...", ... }
```

- `height_kind: "elevation"` — a real calibrated DSM backs the terrain (unchanged from Phase 5).
- `height_kind: "relative_depth"` — no calibration has succeeded (or none was requested), so the job's own real `relative_depth` artifact backs the terrain instead. `min_elevation`/`max_elevation` are always `null` in this mode — never populated for a relative-depth-backed terrain, since those field names imply a physical quantity this data doesn't have; a scientifically-neutral `min_height_value`/`max_height_value` pair is populated in both modes instead.
- `terrain.available: false` only when the job has produced neither a DSM nor a relative_depth artifact yet.

This reuses the entire existing 3D pipeline unmodified — `geospatial/terrain_grid.py`'s height-grid extraction was already generic to any single-band raster; only the gate that previously required specifically a `dsm` artifact was relaxed. The same real RGB-texture projection (UV-mapped onto the mesh, real source image, no placeholder), the same real `PointerLockControls` WASD + mouse-look first-person flythrough, and the same orbit controls all work identically in relative mode. The Terrain workspace shows a persistent, unmissable notice — **"Relative terrain preview — NOT elevation, NOT a DSM"** — whenever `height_kind` is `"relative_depth"`, and the "Show RGB texture" control is relabeled "RGB texture on relative terrain" in that mode (never "orthomosaic"/"orthophoto", which would overclaim georeferencing that isn't there). Measurement tools and Phase 9 reports were verified unaffected — both already keyed off the actual artifact type, never off which artifact happens to back the 3D view. See `docs/ARCHITECTURE.md` §3.10 for the full architecture and scientific-honesty statement.

## Repository layout

```
frontend/     React + TypeScript + Vite + Tailwind + Three.js + Leaflet + Recharts (Phase 7)
backend/      FastAPI: auth, projects, datasets, analysis jobs, visualization API, measurements API, reports/export API (Phase 9), Alembic migrations, RQ worker
ai/           Depth Anything V2 (relative depth) + MobileSAM (class-agnostic region segmentation, Phase 6)
geospatial/   GDAL/rasterio raster metadata/pixel I/O, metric calibration, preview/terrain-grid extraction, image quality (Phase 6), measurements (Phase 7), terrain derivatives + hazard screening (Phase 8)
storage/      File storage abstraction + persistent model_cache/ (local filesystem now, S3-compatible later)
database/     DB init scripts (extensions)
docker/       docker-compose.yml, service Dockerfiles referenced from here
docs/         Architecture, roadmap, development conventions
```
