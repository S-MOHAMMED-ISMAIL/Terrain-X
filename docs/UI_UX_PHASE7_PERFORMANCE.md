# UI/UX Phase 7 Performance Report

## Scope and method

Measurements used the local Docker stack, Chromium Playwright instrumentation,
Vite production builds, browser request observation, and source cleanup review.
Numbers are local measurements, not service-level guarantees. Optimization was
limited to frontend loading and duplicate requests; backend APIs and scientific
engines were unchanged.

## Bundle before and after

| Artifact | Before Phase 7 | After Phase 7 |
| --- | ---: | ---: |
| Initial JavaScript | 1,430.38 kB | 208.24 kB |
| Initial JavaScript gzip | 400.06 kB | 65.54 kB |
| Initial CSS | 56.25 kB | 41.15 kB |
| Initial CSS gzip | 14.60 kB | 8.25 kB |

Initial JavaScript fell 85.4% minified and 83.6% gzip. The former single bundle
is now split by actual use:

- Project Workspace: 460.32 kB / 132.01 kB gzip;
- Three.js terrain: 399.74 kB / 98.93 kB gzip, loaded on first 3D request;
- Recharts profile chart: 362.53 kB / 106.69 kB gzip, loaded for a profile result.

The initial route no longer downloads workspace, Three.js, Leaflet-heavy
workspace code, or Recharts. Vite's previous 500 kB chunk warning is gone.

## Cold route

The same Vite development route was measured before and after splitting:

| Measurement | Before | After | Change |
| --- | ---: | ---: | ---: |
| Script/module requests | 89 | 36 | -59.6% |
| Transfer bytes | 6,564,731 | 1,690,551 | -74.2% |
| Decoded bytes | 6,538,031 | 1,679,751 | -74.3% |

Development-module totals are not production bundle sizes, but they confirm the
route boundary works in the browser as well as in the production build.

## Dashboard requests

With two projects, the current APIs require one project list plus one dataset and
one analysis list per project: `1 + 2N`, or five data requests. Before Phase 7,
React Strict Mode started that load twice in development, producing ten data
requests. An in-flight promise now deduplicates concurrent mounts: five data
requests were observed. Authentication hydration is separate.

Production still scales as `1 + 2N`. A true one-request dashboard requires a
backend summary endpoint and is intentionally deferred. Local completion varied
from 1.02 to 1.82 seconds after the change versus one 1.38 second baseline, so no
latency improvement is claimed beyond deterministic request reduction.

## Route and workspace timing

Representative real calibrated flow measurements:

| Surface | Observed local time |
| --- | ---: |
| Analysis presentation | 67-101 ms |
| 2D workspace and Leaflet visible | 450-1,000 ms |
| Deferred 3D chunk and canvas visible | 199-400 ms |
| Report generation to terminal completed UI | 3,524-4,079 ms |

The 3D number includes first-time dynamic import in that browser session.

## Workspace render and network findings

- The terrain host retained the same DOM identity through Workspace -> Analysis
  -> Reports navigation.
- After intentional 2D -> 3D -> 2D switching settled, Analysis and Reports
  navigation caused zero additional preview/map-preview requests.
- Layer/tool/results panel changes continue to preserve the viewport instance.
- Analysis, reports, and disaster polling remain active-only.
- Full report contents are not fetched for report history.
- No frame-level values were moved into React state.

Broad component rerender decomposition was not attempted. Browser evidence did
not show remounts or request storms, and changing the workspace ownership model
would add risk without a measured user-visible gain.

## Memory and cleanup

Source inspection confirmed cleanup for polling intervals, animation frames,
visibility/pointer/key listeners, media URLs, authenticated blob URLs, Leaflet
overlay URLs, Three.js controls, geometries, materials, textures, renderer, and
MediaRecorder downloads. Navigation tests did not reveal duplicate engines or
animation loops.

## Deferred performance work

- Backend dashboard summary endpoint for replacing `1 + 2N`.
- Further splitting inside the 460 kB workspace chunk, only if field metrics
  show 2D startup pressure.
- Production RUM and device-class profiling; local timings are insufficient for
  capacity claims.
- Decorative background power profiling on low-end hardware.

## Validation

- Focused Phase 7 Playwright: 2 passed.
- Full Vitest: 256 passed across 24 files.
- TypeScript: clean.
- ESLint: 0 errors and 5 existing Fast Refresh warnings.
- Vite build: passed, 699 modules, no large-chunk warning.
- All 28 unique Playwright tests passed after affected-spec reconciliation; see
  the final report for the host-stall details.

