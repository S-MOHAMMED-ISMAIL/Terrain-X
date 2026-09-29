"""Generates the P1-6 Playwright off-meridian fixtures
`frontend/e2e/fixtures/offmeridian-source.tif` and
`frontend/e2e/fixtures/offmeridian-dem.tif`.

THESE FIXTURES VALIDATE PIPELINE / GEOREGISTRATION BEHAVIOR AND ARE NOT
ACCURACY EVIDENCE.

The source is the structured test scene (tests/fixtures.py) at 128 x 128 px,
40 m/px, in UTM 33N at origin (660000 E, 6660000 N): about 60 N and 2.9 deg
east of the zone's central meridian. There the pre-P1-6 2D map (the source
preview stretched over its WGS84 envelope, clicks mapped linearly inside it)
placed the image up to ~220 m off and picked the wrong source pixel for ~98%
of true locations, by up to 5 px — unlike the other E2E fixtures, which sit
exactly on the central meridian where that error is zero.

The DEM is built exactly like calibrated-dem.tif
(scripts/generate_e2e_calibration_fixture.py): DEM = 10 * depth + 100 from
the pinned Depth Anything V2 model's own depth for this source, with a 5x5
NoData corner, so the calibration quality gate accepts it.

Run inside the backend container (model cache, ai/, tests/):

    docker compose -f docker/docker-compose.yml cp \
        scripts/generate_e2e_calibration_fixture.py backend:/tmp/gen_cal.py
    docker compose -f docker/docker-compose.yml exec -T backend \
        python - /tmp/source.tif /tmp/dem.tif < scripts/generate_e2e_offmeridian_fixture.py
    docker compose -f docker/docker-compose.yml cp \
        backend:/tmp/source.tif frontend/e2e/fixtures/offmeridian-source.tif
    docker compose -f docker/docker-compose.yml cp \
        backend:/tmp/dem.tif frontend/e2e/fixtures/offmeridian-dem.tif
"""

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from tests.fixtures import make_structured_scene_geotiff_bytes  # noqa: E402

SIZE = 128
PIXEL_SIZE = 40.0
ORIGIN_X, ORIGIN_Y = 660000.0, 6660000.0
CRS = "EPSG:32633"


def main(source_path: str, dem_path: str) -> None:
    Path(source_path).write_bytes(
        make_structured_scene_geotiff_bytes(
            width=SIZE,
            height=SIZE,
            crs=CRS,
            origin_x=ORIGIN_X,
            origin_y=ORIGIN_Y,
            pixel_size=PIXEL_SIZE,
        )
    )
    spec = importlib.util.spec_from_file_location("gen_cal", "/tmp/gen_cal.py")
    gen_cal = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen_cal)
    gen_cal.main(source_path, dem_path)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
