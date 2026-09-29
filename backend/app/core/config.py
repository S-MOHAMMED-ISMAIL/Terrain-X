from functools import lru_cache

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEVELOPMENT_JWT_SECRET = "change-me-to-a-long-random-value-in-real-envs"
JWT_SECRET_CONFIGURATION_ERROR = (
    "JWT secret must be explicitly configured for non-development environments."
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        hide_input_in_errors=True,
    )

    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    DATABASE_URL: str
    DATABASE_URL_SYNC: str

    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET_KEY: SecretStr
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    CORS_ORIGINS: str = "http://localhost:5173"

    STORAGE_ROOT: str = "/data/storage"
    MAX_UPLOAD_SIZE_MB: int = 500

    # Test isolation (backend pytest ONLY — see backend/tests/conftest.py).
    # Deliberately optional so a normal application run never requires
    # these; conftest.py's own safety guard raises immediately if they are
    # unset or resolve to the same resources as DATABASE_URL/STORAGE_ROOT
    # above, refusing to run any test rather than risk touching real data.
    TEST_DATABASE_URL: str | None = None
    TEST_DATABASE_URL_SYNC: str | None = None
    TEST_STORAGE_ROOT: str | None = None
    # A different Redis DB index than REDIS_URL — real analysis/report jobs
    # flow through an RQ queue backed by Redis, and without this the real
    # development worker and the test suite's own worker would race on the
    # same queue. See backend/tests/conftest.py.
    TEST_REDIS_URL: str | None = None

    # Bounds memory use for depth inference on large remote-sensing imagery —
    # source images wider or taller than this are rejected before any model
    # work starts. See docs/ARCHITECTURE.md's depth pipeline section.
    MAX_DEPTH_INPUT_DIMENSION_PX: int = 4096

    # Phase 4 calibration: bounds how many DEM point-samples are drawn (a
    # uniform grid over the depth raster, not every pixel — see
    # geospatial/calibration.py), and the minimum number of *valid* samples
    # (post NaN/Inf/NoData/out-of-bounds filtering) required to attempt a
    # fit at all.
    CALIBRATION_MAX_SAMPLES: int = 2000
    MIN_CALIBRATION_SAMPLES: int = 3
    # Iterative sigma-clipping robust fit (see geospatial/calibration.py):
    # points whose residual exceeds this many standard deviations are
    # dropped each round, for up to this many rounds.
    CALIBRATION_OUTLIER_SIGMA: float = 2.5
    CALIBRATION_MAX_ITERATIONS: int = 5
    # A GCP CSV must declare at least this many points before calibration is
    # even attempted (a separate, smaller floor than MIN_CALIBRATION_SAMPLES,
    # which applies after in-bounds/validity filtering).
    MIN_GCP_POINTS: int = 3

    # P1-2 calibration quality gate (see geospatial/calibration.py's
    # "Quality gate" section and docs/ARCHITECTURE.md §3.4). These are
    # ENGINEERING ACCEPTANCE-POLICY settings, NOT empirically validated
    # accuracy standards — no value here was derived from any dataset
    # (GAMUS included), and passing the gate is never an accuracy guarantee.
    # The applied values are persisted with every calibration result
    # (`calibration_metadata.quality_gate.policy`) so each decision stays
    # reproducible if these defaults change later.
    #
    # Minimum pooled held-out skill, 1 - SSE_cv / SSE_baseline_cv, where the
    # baseline predicts each training fold's mean reference elevation. 0.0
    # means only "depth predicts held-out reference elevations better than
    # ignoring depth" — the natural zero point of the skill score, not a
    # tuned accuracy threshold.
    CALIBRATION_MIN_CV_SKILL: float = 0.0
    # DEM calibration cross-validation partitions the SOURCE image into
    # this many x this many contiguous spatial blocks and holds out one
    # block at a time (leave-one-spatial-block-out), so spatially
    # autocorrelated neighbours of a held-out sample are not in its training
    # fold. A design choice recorded with every result, not a validated
    # optimum. GCP calibration always uses leave-one-out instead.
    CALIBRATION_CV_BLOCKS_PER_SIDE: int = 4
    CALIBRATION_QUALITY_POLICY_VERSION: str = "v1"

    # P1-3 raster bare-earth approximation (geospatial/ground_filter.py —
    # progressive morphological filter, Zhang et al. 2003). Run only after a
    # calibration that passed the quality gate above. These are ENGINEERING
    # starting values taken from the PMF/SMRF literature, NOT tuned or
    # validated on monocular calibrated surfaces; the applied values are
    # persisted with every result (`ground_filter_metadata.policy`).
    # Largest object footprint (metres) the filter can remove — wider
    # objects stay "ground".
    GROUND_FILTER_MAX_WINDOW_M: float = 20.0
    # Assumed maximum terrain slope (dz/dx, dimensionless; 0.15 ~ 8.5°).
    GROUND_FILTER_SLOPE: float = 0.15
    # Initial / maximum elevation-difference thresholds, in metres. D3 converts
    # them to the calibrated elevation's declared vertical unit when needed.
    GROUND_FILTER_INITIAL_THRESHOLD: float = 0.5
    GROUND_FILTER_MAX_THRESHOLD: float = 3.0
    GROUND_FILTER_POLICY_VERSION: str = "v1"

    # Phase 5 visualization: bounds the longest side of a generated preview
    # PNG (a presentation derivative, never the scientific artifact itself —
    # see docs/ARCHITECTURE.md §3.5) and of a raw pixel "window" crop.
    PREVIEW_MAX_DIMENSION: int = 1024
    # Bounds the longest side of a 3D terrain height grid extracted from a
    # DSM artifact — deliberately small since it becomes a real Three.js
    # vertex grid (MAX_TERRAIN_DIMENSION^2 vertices) rendered client-side.
    MAX_TERRAIN_DIMENSION: int = 256
    # P1-9 mesh export: the only grid resolutions a GLB may be built at, the
    # longest side of an embedded texture, and how many exports may build
    # at once (each holds the grid, mesh and texture in memory).
    MESH_EXPORT_RESOLUTIONS: list[int] = [256, 512]
    MESH_EXPORT_MAX_TEXTURE_DIMENSION: int = 2048
    MESH_EXPORT_MAX_CONCURRENT: int = 2

    # Phase 6 semantic segmentation (MobileSAM, see ai/mobile_sam.py):
    # bounds memory use for automatic mask generation on a very large source
    # image — measured real inference time was roughly CONSTANT across
    # tested resolutions (SAM's own encoder preprocessing already resizes
    # internally), so this exists as a memory/array-size safety bound, not a
    # speed optimization. A detected region smaller than
    # MIN_SEMANTIC_REGION_AREA_PX is dropped before region IDs are assigned
    # (see ai/mobile_sam.py::MobileSAMEstimator.predict) — a real, documented
    # filter against noise-sized mask fragments, not a fabricated omission.
    MAX_SEMANTIC_INPUT_DIMENSION_PX: int = 2048
    MIN_SEMANTIC_REGION_AREA_PX: int = 64

    # Phase 7 measurements (geospatial/measurements.py): a terrain profile's
    # `samples` request parameter is capped here — a real, cheap windowed
    # raster read either way, but an unbounded value would let a client
    # request an arbitrarily large response/array for no scientific benefit.
    MAX_PROFILE_SAMPLES: int = 512

    # Phase 11: explicit RQ job timeout, replacing RQ's implicit 180s
    # default (see docs/ARCHITECTURE.md's Phase 11 section). Empirically
    # measured via a real end-to-end run against this project's own worker
    # (depth estimation + MobileSAM semantic segmentation, both real model
    # inference, on a 2048x2048 image — the largest input
    # MAX_SEMANTIC_INPUT_DIMENSION_PX allows): total job wall-clock 170.7s,
    # of which MobileSAM automatic mask generation alone was 145.7s on CPU.
    # That real worst-case measurement came within ~10s of RQ's old 180s
    # default — the exact latent risk this setting closes. Set generously
    # above it (~5x) rather than tightly, since a slower CPU or a cold
    # model-load adds real time this measurement doesn't include.
    ANALYSIS_JOB_TIMEOUT_SECONDS: int = 900
    # R1 report generation runs in the same RQ worker and may copy every
    # completed analysis artifact into the export bundle. Use the project's
    # existing, empirically justified worker budget rather than RQ's implicit
    # 180s default; report fixtures complete far below this ceiling.
    REPORT_GENERATION_TIMEOUT_SECONDS: int = 900
    # Stale-job reconciliation (app/services/job_reconciliation.py): a
    # `running` job whose `updated_at` (already bumped by every real stage
    # transition throughout the pipeline — no new column needed) hasn't
    # moved for longer than this is presumed to have lost its worker
    # (crashed process/container, or silently killed by the RQ timeout
    # above without going through this app's own exception handling) and is
    # reconciled to `failed`. Deliberately set well above
    # ANALYSIS_JOB_TIMEOUT_SECONDS (which already bounds how long a single
    # real job can legitimately run) plus a grace margin for this sweep's
    # own polling interval, so a genuinely active job is never falsely
    # reaped.
    STALE_JOB_AFTER_SECONDS: int = 1200
    # R1 report reconciliation. This must remain safely above
    # REPORT_GENERATION_TIMEOUT_SECONDS so a legitimate work horse cannot be
    # reaped while RQ still permits it to run.
    STALE_REPORT_AFTER_SECONDS: int = 1200
    # How often the reconciliation sweep runs (app/main.py startup task).
    STALE_JOB_SWEEP_INTERVAL_SECONDS: int = 60

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_non_empty_jwt_secret(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("JWT secret must not be empty.")
        return value

    @model_validator(mode="after")
    def validate_jwt_secret_safety(self):
        if (
            self.ENVIRONMENT.strip().lower() != "development"
            and self.JWT_SECRET_KEY.get_secret_value() == DEVELOPMENT_JWT_SECRET
        ):
            raise ValueError(JWT_SECRET_CONFIGURATION_ERROR)
        return self

    @model_validator(mode="after")
    def validate_report_timeout_relationship(self):
        if self.STALE_REPORT_AFTER_SECONDS <= self.REPORT_GENERATION_TIMEOUT_SECONDS:
            raise ValueError(
                "STALE_REPORT_AFTER_SECONDS must be greater than "
                "REPORT_GENERATION_TIMEOUT_SECONDS"
            )
        return self

    @property
    def max_upload_size_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
