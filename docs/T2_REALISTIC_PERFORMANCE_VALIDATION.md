# T2 Realistic Performance Validation

## Decision

**T2 = CLOSED (PASS).** The unchanged production pipeline completed a realistic
4096x4096 workload without timeout, out-of-memory termination, corrupt output,
worker failure, report failure, or unusable browser visualization. No production
code, schema, algorithm, calibration rule, model, or timeout was changed.

This is performance and operational validation. It is not a new scientific
accuracy claim.

## Scope and method

The source fixture is a deterministic bilinear resampling of the real Boulder
NAIP image from 2000x2000 to 4096x4096. It preserves the source CRS and extent
(EPSG:26913, 1200 m square), is labeled `PERFORMANCE FIXTURE ONLY`, and has SHA-256
`831b7ea93a56f26fc07b5a6286ebb37b550c9c75c5dc72638b3b7abdfedc1a45`.
The fixture is useful for testing maximum configured raster dimensions, but it is
not native 4096 imagery and does not add spatial detail.

Two measurement paths were kept separate:

1. **Real-data performance:** the resampled real Boulder image plus the real
   Boulder DEM passed through public APIs, RQ, artifact generation,
   visualization, browser rendering, and report generation. Its calibration was
   honestly rejected by the existing quality gate.
2. **Synthetic calibrated performance:** a labeled affine reference
   (`2.5 * relative_depth + 100`) exercised calibration, metric elevation,
   ground filtering, DTM/nDSM, derivatives, previews, grids, and meshes. This is
   throughput evidence only, not scientific validation.

The expensive depth path was run twice. The retained scripts and raw captures
are under `scratchpad/t2/`; consolidated values are in
`scratchpad/t2/results.json`.

## Environment and limits

| Item | Value |
| --- | ---: |
| Docker Desktop / Engine | 4.90.0 / 29.7.2 |
| Docker platform | Linux x86_64 |
| Available CPU / memory | 12 CPUs / 7,976,144,896 bytes |
| Container resource limits | none configured |
| Worker Python | 3.12.14 |
| NumPy / Rasterio | 2.1.3 / 1.4.3 |
| Torch / CUDA | 2.5.1+cpu / unavailable |
| Depth model | Depth Anything V2 Small, pinned revision `5426e4f...` |
| Maximum depth input | 4096 px |
| Analysis / report RQ timeout | 900 s / 900 s |
| Analysis / report stale threshold | 1200 s / 1200 s |
| Upload limit | 500 MiB |
| Frontend polling interval | 3000 ms |
| Explicit backend request timeout | none found |

The stale thresholds remain greater than their corresponding 900-second worker
execution limits. The Docker health-check timeout is 5 seconds and is not an HTTP
request-duration limit. Frontend polling has no separate client lifecycle timeout;
it follows terminal job/report state.

## Depth repeatability

The model consumed 518x518 tensors and wrote 4096x4096 float32 GeoTIFFs. Thus the
4096 test principally stresses source raster handling, preprocessing, output
resizing/writing, downstream artifacts, and memory rather than transformer
inference at 4096.

| Stage | Run 1 (s) | Run 2 (s) | Representative median (s) |
| --- | ---: | ---: | ---: |
| Raster read | 0.579 | 0.653 | 0.616 |
| RGB preprocessing | 0.167 | 0.255 | 0.211 |
| Model load | 3.389 | 3.557 | 3.473 |
| Predict total | 3.292 | 3.271 | 3.281 |
| Model forward + resize | 2.574 | 2.365 | 2.469 |
| Depth write | 0.244 | 0.246 | 0.245 |
| End-to-end | 10.425 | 10.809 | 10.617 |
| Peak RSS (MiB) | 834.711 | 847.707 | 841.209 |

Both runs produced the same 67,133,798-byte output and identical min, max, and
mean summaries. The output was finite, 4096x4096, float32, and EPSG:26913.

## Real data performance

The public API/RQ run completed analysis in 19.885 seconds wall time
(16.973 seconds from worker timestamps), with a measured peak worker-container
memory of 1,617.348 MiB. The relative-depth artifact was valid and readable.

Observed stage boundaries were approximately: queued 0.09 s, validating input
2.47 s, loading model 2.82 s, preprocessing 9.02 s, inference 13.02 s, writing
depth 16.02 s, calibrating 18.47 s, and completed 19.49 s. These are 250 ms poll
observations, not internal profiler spans.

The real Boulder calibration used 2,116 valid samples and took 0.518 seconds. It
failed the existing expected-scale-sign and held-out-skill gates. This is the
correct failure behavior for this image/reference pairing: the analysis job still
completed, no misleading metric elevation was written, and ground filtering was
not requested. This result is not treated as a performance or correctness defect.

### Visualization

| Endpoint/output | Time (s) | Size |
| --- | ---: | ---: |
| Depth preview | 2.248 | 246,073 B |
| Depth map preview | 2.322 | 250,530 B |
| Terrain grid (256x256 float32) | 2.222 | 262,144 B |
| Textured GLB 256 | 6.676 | 9,910,812 B |
| Textured GLB 512 | 6.729 | 20,908,588 B |

The browser consumed metadata describing a 256x256 terrain derived from the
4096x4096 source. Playwright observed a visible 806x598 WebGL canvas, no console
errors, and no page errors. Manual screenshot inspection confirmed a nonblank,
properly framed relative-depth terrain and honest failed-calibration messaging.
The evidence image is `scratchpad/t2/browser_4096_terrain.png`.

### Report generation

The report completed in 13.609 seconds API wall time and 13.360 seconds by
timestamps, using 758.469 MiB peak container memory. Recorded internals were
1.922 seconds to build report data, 0.041 seconds to render PDF, 0.000025 seconds
to render CSV, and 8.269 seconds for ZIP work. The PDF was 8,341 bytes and the
bundle 54,416,331 bytes. This is far below the 900-second report worker timeout
and the 1200-second stale threshold.

## Synthetic calibrated performance

The synthetic calibrated fixture passed its intentionally trivial affine quality
gate with 2,116 samples, 16 cross-validation folds, and CV skill
0.999999999991. Total measured time was 23.372 seconds and peak process RSS was
1,246.293 MiB. DTM and nDSM GeoTIFFs were both written successfully.

| Stage | Time (s) | Peak RSS (MiB) |
| --- | ---: | ---: |
| Calibration sampling | 0.210 | 174.035 |
| Metric elevation compute/write | 0.275 | 367.945 |
| Ground filter | 17.974 | 1,136.359 |
| DTM write | 0.208 | 544.332 |
| nDSM write | 0.175 | 544.332 |
| Derivative preparation | 0.221 | 660.105 |
| Slope/aspect | 1.581 | 988.156 |
| Hillshade | 1.502 | 1,246.293 |
| Preview (1024 max) | 0.257 | 530.723 |
| Direct GLB 256 / 512 | 0.051 / 0.245 | 484.789 / 533.145 |

The progressive ground filter completed seven configured scales and accounted
for about 77% of this synthetic path's elapsed time. Hillshade was the process
memory high-water mark. Neither approached available Docker memory or worker
timeouts. Live textured mesh requests took about 6.7 seconds while direct
geometry generation took 0.05/0.25 seconds; texture preparation and API artifact
handling are therefore the likely dominant difference, though this was not
instrumented finely enough to assign exact shares.

## Regression and tooling validation

- Focused backend/config/auth/report/reconciliation/terrain/D1/D2/D3 slice:
  **193 passed** in 158.34 seconds.
- Existing combined D1/D2/D3 browser regression: **5 passed**.
- T2 benchmark harness Ruff: **passed**.
- T2 benchmark harness Python compilation: **passed**.
- Browser 4096 terrain check: **passed**, nonblank WebGL, no console/page errors.

The first browser invocation selected a host Python installation without
Rasterio, causing two test-helper startup failures. The unchanged five-test slice
was rerun with the existing project virtual environment on PATH and passed. This
was a validation-environment issue, not an application or assertion failure.

## Bottlenecks and operational conclusion

The dominant measured CPU stage in the calibrated path is the 4096 ground
filter; the largest process memory observation is hillshade at about 1.25 GiB.
The highest live worker-container memory observation is about 1.62 GiB during
analysis. Analysis and report times retain very large margins against their
900-second execution limits, and measured memory remains well below the roughly
7.6 GiB Docker allocation.

No timeout tuning, optimization, migration, or implementation change is required
for T2. The current 4096 maximum is operationally supported in this measured CPU
environment.

## Limitations

- The 4096 source is resampled real imagery, not native 4096 acquisition.
- Real-data calibration correctly rejected this pairing, so calibrated
  ground-filter/derivative throughput comes from a labeled synthetic affine
  reference and is not an accuracy result.
- API polling stage boundaries are approximate at 250 ms resolution.
- Peak RSS and cgroup memory are sampled high-water observations, not allocation
  traces.
- No explicit backend HTTP request timeout exists to compare; worker execution,
  stale reconciliation, API completion, and frontend terminal polling were
  validated instead.
