"""Regenerates the Playwright calibration fixture
`frontend/e2e/fixtures/calibrated-dem.tif` from
`frontend/e2e/fixtures/calibrated-source.tif`.

THESE FIXTURES VALIDATE PIPELINE BEHAVIOR AND ARE NOT ACCURACY EVIDENCE.
The "DEM" is constructed from the pinned Depth Anything V2 model's OWN
relative-depth prediction for the source image:

    DEM = SCALE * depth + OFFSET        (5x5 NoData corner, value -9999)

so calibrating the source against it only proves the pipeline accepts a
genuinely depth-consistent reference (positive scale, positive held-out
skill) — it says nothing about how well monocular depth matches real
terrain. SCALE/OFFSET keep elevations roughly in the 100-145 range the
golden-path E2E's flood-screening water level (125) was written against.

Run inside the backend container (which has the model cache and ai/):

    docker compose -f docker/docker-compose.yml cp \
        frontend/e2e/fixtures/calibrated-source.tif backend:/tmp/source.tif
    docker compose -f docker/docker-compose.yml exec -T backend \
        python - /tmp/source.tif /tmp/dem.tif < scripts/generate_e2e_calibration_fixture.py
    docker compose -f docker/docker-compose.yml cp \
        backend:/tmp/dem.tif frontend/e2e/fixtures/calibrated-dem.tif
"""

import sys

import numpy as np
import rasterio

from ai.depth_anything import DepthAnythingV2Estimator

SCALE = 10.0
OFFSET = 100.0
NODATA = -9999.0
NODATA_CORNER = 5


def main(source_path: str, dem_path: str) -> None:
    with rasterio.open(source_path) as src:
        rgb = np.transpose(src.read(), (1, 2, 0)).copy()
        profile = {
            "driver": "GTiff",
            "height": src.height,
            "width": src.width,
            "count": 1,
            "dtype": "float32",
            "crs": src.crs,
            "transform": src.transform,
            "nodata": NODATA,
        }

    estimator = DepthAnythingV2Estimator()
    estimator.load()
    depth = estimator.predict(rgb).depth

    dem = (SCALE * depth.astype("float64") + OFFSET).astype("float32")
    dem[:NODATA_CORNER, :NODATA_CORNER] = NODATA
    with rasterio.open(dem_path, "w", **profile) as dst:
        dst.write(dem, 1)
        dst.set_band_unit(1, "metre")

    valid = dem[dem != NODATA]
    print(
        f"depth range {depth.min():.4f}..{depth.max():.4f}; "
        f"DEM range {valid.min():.3f}..{valid.max():.3f}; wrote {dem_path}"
    )


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
