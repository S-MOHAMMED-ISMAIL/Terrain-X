"""Generates the D2 Playwright fixture `frontend/e2e/fixtures/geographic-source.tif`.

THIS FIXTURE VALIDATES TEXTURE-COMPATIBILITY BEHAVIOR AND IS NOT ACCURACY
EVIDENCE.

The structured test scene (tests/fixtures.py) at 32 x 32 px — the same size
as calibrated-source.tif — but in geographic EPSG:4326 (0.0005 deg/px at
17.87 E, 60.045 N). Its analysis artifacts keep the same 32 x 32 dimensions,
yet the 3D terrain grid is reprojected into UTM, so the RGB image must NOT be
draped on it: equal dimensions alone are not pixel correspondence.

Run inside the backend container (tests/ on the path):

    docker compose -f docker/docker-compose.yml exec -T backend \
        python - /tmp/geographic-source.tif < scripts/generate_e2e_geographic_fixture.py
    docker compose -f docker/docker-compose.yml cp \
        backend:/tmp/geographic-source.tif frontend/e2e/fixtures/geographic-source.tif
"""

import sys
from pathlib import Path

sys.path.insert(0, "/app")

from tests.fixtures import make_structured_scene_geotiff_bytes  # noqa: E402

SIZE = 32
PIXEL_SIZE = 0.0005
ORIGIN_X, ORIGIN_Y = 17.87, 60.045
CRS = "EPSG:4326"


def main(source_path: str) -> None:
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


if __name__ == "__main__":
    main(sys.argv[1])
