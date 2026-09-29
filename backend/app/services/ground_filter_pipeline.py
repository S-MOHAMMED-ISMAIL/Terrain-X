"""P1-3 backend-side orchestration for the raster bare-earth approximation
(DTM) and nDSM. The filter itself lives in geospatial/ground_filter.py (pure,
backend-independent); this module applies the configured policy and turns
the result into a soft-failure outcome — the same separation as
app/services/semantic_pipeline.py.

Only ever called for a job whose calibration ended CALIBRATED, i.e. passed
the P1-2 calibration quality gate (see analysis_execution.py). A failure here
never fails the job: the calibrated metric elevation/DSM remain valid.
"""

import logging
import time
from dataclasses import asdict, dataclass

import numpy as np
from affine import Affine
from rasterio.crs import CRS

from app.core.config import Settings
from app.models.analysis_job import GroundFilterStatus
from geospatial.ground_filter import (
    GroundFilterError,
    GroundFilterParameters,
    GroundFilterResult,
    progressive_morphological_filter,
)
from geospatial.vertical_units import (
    VerticalUnitError,
    VerticalUnitResolution,
    require_known,
    unit_provenance,
)

logger = logging.getLogger("terrainx.backend.ground_filter")

POLICY_STATEMENT = (
    "Engineering starting parameters taken from the progressive morphological "
    "filter literature (Zhang et al. 2003; Pingel et al. 2013), not tuned or "
    "validated on monocular calibrated surfaces."
)

DTM_VALUE_SEMANTICS = (
    "ESTIMATED bare-earth elevation — a raster progressive-morphological-filter "
    "approximation derived only from this job's calibrated surface (DSM). Not a "
    "measured or true DTM: no point cloud, stereo or classified ground exists. "
    "In the DSM's vertical unit (the calibration reference's declared unit; see "
    "vertical_unit). Equals the DSM on cells "
    "classified as ground; on other cells it is the filter's opened surface."
)

NDSM_VALUE_SEMANTICS = (
    "ESTIMATED height of the visible surface above the ESTIMATED bare earth "
    "(nDSM = DSM - DTM), in the DSM's vertical unit (see vertical_unit). Not a "
    "measured object/building height. 0 exactly on cells classified as ground; never "
    "negative by construction and never clipped."
)

GROUND_FILTER_LIMITATIONS = (
    "Raster-derived estimates, not physical measurements. Objects wider than the "
    "maximum window remain 'ground'; terrain steeper than the slope parameter can be "
    "cut down and appear as objects; objects lower than the initial threshold remain "
    "'ground'; under objects on sloping ground the fill is flat, so nDSM is "
    "overestimated on the downhill side. Windows, the slope parameter and thresholds "
    "are applied in metres, using the DSM's declared vertical unit (never assumed). The "
    "monocular calibrated surface represents "
    "objects only approximately, and the calibration quality gate validates overall "
    "agreement with the reference, not object heights."
)


@dataclass
class GroundFilterOutcome:
    status: GroundFilterStatus
    metadata: dict
    result: GroundFilterResult | None = None


def ground_filter_parameters(settings: Settings) -> GroundFilterParameters:
    return GroundFilterParameters(
        max_window_m=settings.GROUND_FILTER_MAX_WINDOW_M,
        slope=settings.GROUND_FILTER_SLOPE,
        initial_threshold=settings.GROUND_FILTER_INITIAL_THRESHOLD,
        max_threshold=settings.GROUND_FILTER_MAX_THRESHOLD,
    )


def ground_filter_policy(settings: Settings) -> dict:
    return {
        "version": settings.GROUND_FILTER_POLICY_VERSION,
        "parameters": asdict(ground_filter_parameters(settings)),
        "statement": POLICY_STATEMENT,
    }


def run_ground_filter(
    dsm: np.ndarray,
    *,
    crs: CRS | None,
    transform: Affine | None,
    settings: Settings,
    vertical_unit: VerticalUnitResolution | None,
    nodata: float | None = None,
) -> GroundFilterOutcome:
    """Always returns an outcome (never raises): COMPLETED with the result,
    or FAILED with the real reason plus the policy that was applied. D3: an
    unknown/unsupported/conflicting vertical unit is a FAILED outcome with its
    diagnostic — metric thresholds are never applied to values of unknown
    unit."""
    policy = ground_filter_policy(settings)
    start = time.monotonic()
    unit_metadata = vertical_unit.as_dict() if vertical_unit is not None else None
    try:
        unit = require_known(
            vertical_unit
            or VerticalUnitResolution("unknown", None, None, None, "No vertical unit recorded."),
            "Ground filtering (DTM/nDSM)",
        )
    except VerticalUnitError as exc:
        return GroundFilterOutcome(
            status=GroundFilterStatus.FAILED,
            metadata={"error": str(exc), "policy": policy, "vertical_unit": unit_metadata},
        )
    try:
        result = progressive_morphological_filter(
            dsm,
            crs=crs,
            transform=transform,
            parameters=ground_filter_parameters(settings),
            vertical_unit=unit,
            nodata=nodata,
        )
    except GroundFilterError as exc:
        return GroundFilterOutcome(
            status=GroundFilterStatus.FAILED, metadata={"error": str(exc), "policy": policy}
        )
    except Exception:
        logger.exception("Unexpected error during ground filtering")
        return GroundFilterOutcome(
            status=GroundFilterStatus.FAILED,
            metadata={
                "error": "An internal error occurred during ground filtering.",
                "policy": policy,
            },
        )

    metadata = {
        **result.metadata(),
        "policy": policy,
        "input_artifact_type": "dsm",
        "duration_seconds": time.monotonic() - start,
        "dtm_value_semantics": DTM_VALUE_SEMANTICS,
        "ndsm_value_semantics": NDSM_VALUE_SEMANTICS,
        "limitations": GROUND_FILTER_LIMITATIONS,
        "vertical_unit": unit_metadata,
        "unit_provenance": unit_provenance(
            vertical_unit,
            horizontal_unit="m (cell size measured in metres: " + result.cell_size.method + ")",
            crs=crs.to_string() if crs is not None else None,
            method=result.metadata()["method"],
        ),
    }
    return GroundFilterOutcome(
        status=GroundFilterStatus.COMPLETED, metadata=metadata, result=result
    )
