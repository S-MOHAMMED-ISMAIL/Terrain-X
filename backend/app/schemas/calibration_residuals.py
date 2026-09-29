"""P1-5: calibration residuals at calibration sample locations — the
response of `GET .../artifacts/{artifact_id}/calibration-residuals`. A
sample-point GeoJSON FeatureCollection (WGS84 lon/lat), never a raster.
Residual convention: predicted - reference, in the reference's own units."""

import uuid
from typing import Literal

from pydantic import BaseModel


class ResidualPointGeometry(BaseModel):
    type: Literal["Point"]
    coordinates: tuple[float, float]  # [lon, lat], WGS84


class ResidualFeatureProperties(BaseModel):
    sample_index: int
    row: int
    col: int
    source_x: float
    source_y: float
    relative_depth: float
    reference_elevation: float
    predicted_heldout: float
    residual_heldout: float
    predicted_fit: float
    residual_fit: float
    inlier_in_production_fit: bool
    fold_id: int
    block_id: int | None = None  # DEM only
    reference_cell_id: int | None = None  # DEM only
    gcp_index: int | None = None  # GCP only


class ResidualFeature(BaseModel):
    type: Literal["Feature"]
    geometry: ResidualPointGeometry
    properties: ResidualFeatureProperties


class ResidualFeatureCollection(BaseModel):
    type: Literal["FeatureCollection"]
    features: list[ResidualFeature]


class CalibrationResidualsOut(BaseModel):
    artifact_id: uuid.UUID
    analysis_job_id: uuid.UUID
    # The artifact's own persisted summary/provenance (definition, units,
    # disclaimer, per-kind statistics, per-block held-out summaries, ...).
    summary: dict
    feature_collection: ResidualFeatureCollection
