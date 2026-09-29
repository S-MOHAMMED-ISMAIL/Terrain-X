"""Phase 9: real report/export tests.

Covers pure unit tests of the report data builder (`app/services/
report_builder.py`) against real (if manually-constructed, unpersisted) ORM
model instances, pure rendering tests of the PDF/CSV/ZIP renderers
(`app/services/report_render.py`) — independently re-parsed with `pypdf`/
`zipfile`, never trusting only the renderer's own claimed success — the real
report-generation service pipeline end to end (creation validation,
generation, persisted `Report` row, real files written to the real storage
backend), ownership/cross-project/cross-user enforcement, restart
persistence, and scientific-honesty wording. One test
(`test_real_e2e_report_reflects_actual_pipeline_outputs`) runs a genuine
end-to-end TERRAIN-X pipeline (real DEM calibration + real disaster
screening, only the depth *model* faked for speed — the same established
technique `tests/test_semantic_segmentation.py` already uses) and generates
a real report from those real outputs — never mocking the report's own
content.
"""

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime

import numpy as np
import pytest
import rasterio
from pypdf import PdfReader
from rq.timeouts import JobTimeoutException

from app.core.storage import get_storage
from app.db.session import AsyncSessionLocal
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import (
    AnalysisJob,
    AnalysisJobStatus,
    AnalysisStage,
    CalibrationStatus,
    DisasterStatus,
    SemanticStatus,
)
from app.models.dataset import Dataset, DatasetRole, DatasetStatus
from app.models.project import Project
from app.models.report import Report, ReportStatus
from app.services import analysis_execution, report_execution
from app.services.report_builder import ReportContext, build_report_data
from app.services.report_execution import generate_report
from app.services.report_render import build_bundle_zip, render_csv, render_pdf
from tests.fixtures import (
    make_depth_consistent_dem_geotiff_bytes,
    make_structured_scene_geotiff_bytes,
)

_ORIGIN_X, _ORIGIN_Y, _PIXEL_SIZE, _UTM_CRS = 500000.0, 4649984.0, 2.0, "EPSG:32633"


# --------------------------------------------------------------------------
# Shared helpers (mirrors test_disaster_screening.py / test_measurements.py)
# --------------------------------------------------------------------------


async def _register_and_login(client, email: str, password: str = "supersecret123") -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_project(client, headers: dict, name: str = "Report Test Project") -> str:
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    return resp.json()["id"]


async def _current_user_id(client, headers: dict) -> str:
    return (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]


async def _upload_source_image(client, headers, project_id, filename="scene.tif") -> dict:
    content = make_structured_scene_geotiff_bytes(
        width=32,
        height=32,
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
    )
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": (filename, content, "image/tiff")},
        headers=headers,
    )
    return resp.json()


async def _create_job_row(project_id, dataset_id, user_id, **kwargs) -> uuid.UUID:
    status = kwargs.pop("status", AnalysisJobStatus.COMPLETED)
    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset_id),
            user_id=uuid.UUID(user_id),
            status=status,
            parameters={"version": "v1"},
            completed_at=datetime.now(UTC) if status == AnalysisJobStatus.COMPLETED else None,
            **kwargs,
        )
        db.add(job)
        await db.commit()
        return job.id


async def _create_artifact_row(job_id, artifact_type, storage_key, file_size, metadata=None):
    async with AsyncSessionLocal() as db:
        artifact = AnalysisArtifact(
            analysis_job_id=job_id,
            artifact_type=artifact_type,
            storage_key=storage_key,
            mime_type="image/tiff",
            file_size_bytes=file_size,
            artifact_metadata=metadata,
        )
        db.add(artifact)
        await db.commit()
        return artifact.id


def _write_raw_bytes(storage_key: str, content: bytes) -> int:
    storage = get_storage()
    path = storage.absolute_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return len(content)


def _write_float_raster(storage_key, array, *, crs=None, transform=None, nodata=None) -> int:
    storage = get_storage()
    path = storage.absolute_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": array.shape[0],
        "width": array.shape[1],
        "count": 1,
        "dtype": "float32",
    }
    if crs is not None and transform is not None:
        profile["crs"] = crs
        profile["transform"] = transform
    if nodata is not None:
        profile["nodata"] = nodata
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(array.astype("float32"), 1)
    return path.stat().st_size


async def _create_report_row(project_id, dataset_id, user_id, **kwargs) -> uuid.UUID:
    async with AsyncSessionLocal() as db:
        report = Report(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset_id),
            user_id=uuid.UUID(user_id),
            status=kwargs.pop("status", ReportStatus.PENDING),
            **kwargs,
        )
        db.add(report)
        await db.commit()
        return report.id


# --------------------------------------------------------------------------
# Unit tests: app/services/report_builder.py (pure, real model instances)
# --------------------------------------------------------------------------


def _make_project() -> Project:
    return Project(
        id=uuid.uuid4(),
        owner_id=uuid.uuid4(),
        name="Unit Test Project",
        description="A test project",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _make_dataset(**overrides) -> Dataset:
    defaults = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        original_filename="scene.tif",
        storage_key="projects/x/datasets/y/original.tif",
        file_type="tiff",
        mime_type="image/tiff",
        file_size_bytes=1234,
        role=DatasetRole.SOURCE_IMAGE,
        status=DatasetStatus.VALID,
        width=64,
        height=64,
        bands=3,
        is_georeferenced=True,
        crs="EPSG:32633",
        bbox_min_x=0.0,
        bbox_min_y=0.0,
        bbox_max_x=128.0,
        bbox_max_y=128.0,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Dataset(**defaults)


def _make_depth_job(**overrides) -> AnalysisJob:
    defaults = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status=AnalysisJobStatus.COMPLETED,
        current_stage=AnalysisStage.COMPLETED,
        parameters={"version": "v1"},
        execution_summary={
            "depth_estimation": {
                "model_name": "depth-anything/Depth-Anything-V2-Small-hf",
                "model_revision": "main",
                "device": "cpu",
                "inference_seconds": 1.23,
                "output_width": 64,
                "output_height": 64,
                "artifact_id": str(uuid.uuid4()),
            },
            "image_quality": {"sharpness_laplacian_variance": 42.0},
        },
        calibration_status=CalibrationStatus.UNCALIBRATED,
        calibration_metadata=None,
        semantic_status=SemanticStatus.NOT_REQUESTED,
        semantic_metadata=None,
        disaster_status=DisasterStatus.NOT_REQUESTED,
        disaster_metadata=None,
        created_at=datetime.now(UTC),
        started_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return AnalysisJob(**defaults)


def test_build_report_data_with_only_depth_job_reports_uncalibrated_honestly():
    project = _make_project()
    dataset = _make_dataset()
    depth_job = _make_depth_job()

    data = build_report_data(
        ReportContext(
            project=project,
            dataset=dataset,
            generated_by_user_id=uuid.uuid4(),
            depth_job=depth_job,
        )
    )

    assert data["depth_analysis"]["calibration"]["status"] == "uncalibrated"
    assert "No DEM/GCP reference" in data["depth_analysis"]["calibration"]["note"]
    assert data["disaster_screening"] is None
    assert data["depth_analysis"]["semantic_segmentation"]["status"] == "not_requested"
    assert data["measurements"] == []
    # Relative depth is never presented as elevation anywhere in the report.
    assert "NOT metric elevation" in data["depth_analysis"]["depth_estimation"]["value_semantics"]


def test_build_report_data_calibrated_job_includes_real_calibration_fields():
    depth_job = _make_depth_job(
        calibration_status=CalibrationStatus.CALIBRATED,
        calibration_metadata={
            "method": "affine_least_squares_with_iterative_sigma_clipping",
            "scale_a": 2.94,
            "offset_b": 119.46,
            "source_crs": "EPSG:32633",
            "reference_crs": "EPSG:32633",
            "reference_type": "dem",
            "reference_dataset_id": str(uuid.uuid4()),
            "valid_samples": 4071,
            "inlier_samples": 4071,
            "outlier_samples": 0,
            "validation_metrics": {"mae": 7.68, "rmse": 9.05, "bias": 5.4e-14},
            "limitations": "This affine fit cannot distinguish terrain from object tops...",
            "metric_elevation_artifact_id": str(uuid.uuid4()),
            "dsm_artifact_id": str(uuid.uuid4()),
        },
    )
    data = build_report_data(
        ReportContext(
            project=_make_project(),
            dataset=_make_dataset(),
            generated_by_user_id=uuid.uuid4(),
            depth_job=depth_job,
        )
    )
    cal = data["depth_analysis"]["calibration"]
    assert cal["status"] == "calibrated"
    assert cal["scale_a"] == pytest.approx(2.94)
    assert cal["offset_b"] == pytest.approx(119.46)
    assert cal["validation_metrics"]["rmse"] == pytest.approx(9.05)
    assert "cannot distinguish terrain from object tops" in cal["limitations"]
    assert cal["limitations"] in data["limitations"]


def test_build_report_data_never_fabricates_missing_disaster_section():
    data = build_report_data(
        ReportContext(
            project=_make_project(),
            dataset=_make_dataset(),
            generated_by_user_id=uuid.uuid4(),
            depth_job=_make_depth_job(),
            disaster_job=None,
        )
    )
    assert data["disaster_screening"] is None
    assert data["provenance"]["disaster_analysis_job_id"] is None


def test_build_report_data_disaster_section_never_claims_prediction_or_probability():
    disaster_job = AnalysisJob(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        dataset_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status=AnalysisJobStatus.COMPLETED,
        current_stage=AnalysisStage.COMPLETED,
        parameters={"version": "v1"},
        disaster_status=DisasterStatus.COMPLETED,
        disaster_metadata={
            "source_artifact_id": str(uuid.uuid4()),
            "source_artifact_type": "dsm",
            "source_crs": "EPSG:32633",
            "analysis_crs": "EPSG:32633",
            "reprojected_for_analysis": False,
            "pixel_width": 2.0,
            "pixel_height": 2.0,
            "width": 64,
            "height": 64,
            "terrain_statistics": {
                "min_elevation": 100.0,
                "max_elevation": 130.0,
                "mean_elevation": 115.0,
                "median_elevation": 114.0,
                "elevation_range": 30.0,
                "min_slope_deg": 0.0,
                "max_slope_deg": 40.0,
                "mean_slope_deg": 5.0,
                "valid_pixel_count": 4096,
                "total_pixel_count": 4096,
            },
            "flood": {
                "water_level": 120.0,
                "min_elevation": 100.0,
                "max_elevation": 130.0,
                "potentially_inundated_pixel_count": 2000,
                "valid_pixel_count": 4096,
                "pixel_area": 4.0,
                "area_unit": "square metre",
                "potentially_inundated_area": 8000.0,
                "valid_area": 16384.0,
                "potentially_inundated_percentage": 48.8,
                "method": "terrain_threshold",
                "class_labels": {"1": "Not potentially inundated", "2": "Potentially inundated"},
                "disclaimer": (
                    "This is a terrain-based inundation screening scenario. It does not "
                    "model rainfall, drainage, rivers, flow routing, infiltration, tides, "
                    "storm surge, hydraulic connectivity, or temporal flood dynamics."
                ),
            },
            "landslide": {
                "method": "slope_based",
                "thresholds": {"low_max_deg": 10.0, "moderate_max_deg": 20.0, "high_max_deg": 30.0},
                "class_pixel_counts": {"low": 2000, "moderate": 1500, "high": 500, "very_high": 96},
                "class_areas": {
                    "low": 8000.0,
                    "moderate": 6000.0,
                    "high": 2000.0,
                    "very_high": 384.0,
                },
                "class_percentages": {
                    "low": 48.8,
                    "moderate": 36.6,
                    "high": 12.2,
                    "very_high": 2.3,
                },
                "max_slope_deg": 40.0,
                "mean_slope_deg": 5.0,
                "pixel_area": 4.0,
                "area_unit": "square metre",
                "valid_pixel_count": 4096,
                "class_labels": {"1": "Low", "2": "Moderate", "3": "High", "4": "Very High"},
                "disclaimer": (
                    "This is a terrain-derived landslide susceptibility screening index, "
                    "not a calibrated or validated probability of landslide occurrence."
                ),
            },
            "timings_seconds": {"loading_terrain_data_seconds": 0.02},
            "artifact_ids": {"slope": str(uuid.uuid4())},
            "disclaimer": (
                "Disaster outputs are terrain-derived screening products and are not "
                "substitutes for validated hydrological, hydraulic, geotechnical, or "
                "operational disaster models."
            ),
        },
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    data = build_report_data(
        ReportContext(
            project=_make_project(),
            dataset=_make_dataset(),
            generated_by_user_id=uuid.uuid4(),
            disaster_job=disaster_job,
        )
    )
    flood_disclaimer = data["disaster_screening"]["flood"]["disclaimer"]
    landslide_disclaimer = data["disaster_screening"]["landslide"]["disclaimer"]
    assert "does not model rainfall" in flood_disclaimer
    assert "not a calibrated or validated probability" in landslide_disclaimer
    assert flood_disclaimer in data["limitations"]
    assert landslide_disclaimer in data["limitations"]
    assert data["depth_analysis"] is None  # never fabricated when no depth job exists


# --------------------------------------------------------------------------
# Unit tests: app/services/report_render.py (pure rendering, independently
# re-parsed with pypdf/zipfile — never trusting only the renderer's own
# claimed success)
# --------------------------------------------------------------------------


def _minimal_report_data(**overrides) -> dict:
    data = {
        "report_schema_version": "v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "project": {
            "id": str(uuid.uuid4()),
            "name": "Render Test Project",
            "description": None,
            "created_at": datetime.now(UTC).isoformat(),
        },
        "dataset": {
            "id": str(uuid.uuid4()),
            "original_filename": "scene.tif",
            "file_type": "tiff",
            "role": "source_image",
            "status": "valid",
            "width": 64,
            "height": 64,
            "bands": 3,
            "is_georeferenced": True,
            "crs": "EPSG:32633",
            "bounds": {"min_x": 0, "min_y": 0, "max_x": 1, "max_y": 1},
        },
        "depth_analysis": None,
        "disaster_screening": None,
        "artifacts": [],
        "measurements": [],
        "provenance": {
            "source_dataset_id": str(uuid.uuid4()),
            "depth_analysis_job_id": None,
            "disaster_analysis_job_id": None,
            "generated_by_user_id": str(uuid.uuid4()),
        },
        "limitations": ["No output in this report is independently surveyed or ground-truthed."],
    }
    data.update(overrides)
    return data


def test_render_pdf_produces_real_parseable_pdf_with_expected_text():
    data = _minimal_report_data()
    pdf_bytes = render_pdf(data)

    assert pdf_bytes.startswith(b"%PDF-")
    reader = PdfReader(io.BytesIO(pdf_bytes))
    assert len(reader.pages) >= 1
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "TERRAIN-X Analysis Report" in text
    assert data["project"]["name"] in text
    assert "independently surveyed" in text
    assert "No depth-analysis job has completed" in text
    assert "No disaster-screening job has completed" in text


def test_render_pdf_never_claims_survey_grade_accuracy():
    data = _minimal_report_data()
    pdf_bytes = render_pdf(data)
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf_bytes)).pages)
    assert "survey-grade" not in text.lower() or "not independently surveyed" in text.lower()


def test_render_csv_returns_none_when_no_tabular_data_exists():
    data = _minimal_report_data()
    assert render_csv(data) is None


def test_render_csv_includes_measurements_section_when_present():
    data = _minimal_report_data(
        measurements=[
            {
                "id": str(uuid.uuid4()),
                "measurement_type": "point_elevation",
                "analysis_job_id": str(uuid.uuid4()),
                "artifact_id": str(uuid.uuid4()),
                "input_data": {"row": 1, "col": 2},
                "result_data": {"value": 118.25},
                "created_at": datetime.now(UTC).isoformat(),
            }
        ]
    )
    csv_bytes = render_csv(data)
    assert csv_bytes is not None
    text = csv_bytes.decode("utf-8")
    assert "Measurements" in text
    assert "point_elevation" in text


def test_render_csv_includes_hazard_sections_when_disaster_data_present():
    disaster = {
        "terrain_statistics": {
            "min_elevation": 100.0,
            "max_elevation": 130.0,
            "mean_elevation": 115.0,
        },
        "flood": {
            "water_level": 120.0,
            "potentially_inundated_pixel_count": 2000,
            "valid_pixel_count": 4096,
            "potentially_inundated_percentage": 48.8,
            "potentially_inundated_area": 8000.0,
            "area_unit": "square metre",
        },
        "landslide": {
            "class_pixel_counts": {"low": 100, "moderate": 50},
            "class_areas": {"low": 400.0, "moderate": 200.0},
            "class_percentages": {"low": 66.7, "moderate": 33.3},
        },
    }
    data = _minimal_report_data(disaster_screening=disaster)
    csv_bytes = render_csv(data)
    assert csv_bytes is not None
    text = csv_bytes.decode("utf-8")
    assert "Terrain Statistics" in text
    assert "Flood Screening Summary" in text
    assert "Landslide Susceptibility Classes" in text


def test_build_bundle_zip_contains_real_pdf_json_and_artifact_files(tmp_path):
    data = _minimal_report_data()
    pdf_bytes = render_pdf(data)
    csv_bytes = None

    artifact_path = tmp_path / "slope.tif"
    artifact_path.write_bytes(b"fake-but-real-bytes-on-disk")

    zip_bytes = build_bundle_zip(data, pdf_bytes, csv_bytes, [("slope.tif", artifact_path)])

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        assert "report.pdf" in names
        assert "analysis.json" in names
        assert "artifacts/slope.tif" in names
        assert zf.read("report.pdf") == pdf_bytes
        assert json.loads(zf.read("analysis.json")) == data
        assert zf.read("artifacts/slope.tif") == b"fake-but-real-bytes-on-disk"


def test_build_bundle_zip_notes_missing_artifact_files_rather_than_silently_omitting(tmp_path):
    data = _minimal_report_data()
    pdf_bytes = render_pdf(data)
    missing_path = tmp_path / "does_not_exist.tif"

    zip_bytes = build_bundle_zip(data, pdf_bytes, None, [("missing.tif", missing_path)])

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        assert "artifacts/missing.tif" not in zf.namelist()
        assert "artifacts/MISSING.txt" in zf.namelist()
        assert "missing.tif" in zf.read("artifacts/MISSING.txt").decode()


# --------------------------------------------------------------------------
# Service/API-level tests: real generation pipeline, ownership, persistence
# --------------------------------------------------------------------------


async def test_create_report_rejects_dataset_with_no_completed_analysis(client):
    headers = await _register_and_login(client, "report-noanalysis@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/reports",
        json={},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "No completed analysis exists" in resp.json()["error"]["message"]


async def test_created_report_job_has_explicit_timeout_and_failure_callback(client):
    from rq.job import Job

    from app.core.config import get_settings
    from app.jobs.queue import get_redis_connection
    from app.jobs.tasks import handle_report_generation_failure

    headers = await _register_and_login(client, "report-rq-policy@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    await _create_job_row(project_id, dataset["id"], user_id)

    response = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/reports",
        json={},
        headers=headers,
    )
    assert response.status_code == 201
    report_id = response.json()["id"]

    rq_job = Job.fetch(f"report-{report_id}", connection=get_redis_connection())
    assert rq_job.timeout == get_settings().REPORT_GENERATION_TIMEOUT_SECONDS
    assert rq_job.failure_callback is handle_report_generation_failure


async def test_generate_report_produces_real_pdf_csv_bundle_for_uncalibrated_job(client):
    headers = await _register_and_login(client, "report-basic@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_float_raster(key, np.ones((8, 8), dtype="float32"))
    await _create_artifact_row(job_id, "relative_depth", key, size)

    # Inserted directly (never enqueued) so this test generates the report
    # exactly once — POST /reports would also queue it for the suite's own
    # worker, racing this in-process generate_report() on the same files.
    report_id = str(await _create_report_row(project_id, dataset["id"], user_id))
    status_before = (
        await client.get(f"/api/v1/projects/{project_id}/reports/{report_id}", headers=headers)
    ).json()["status"]
    assert status_before == "pending"

    await generate_report(uuid.UUID(report_id))

    status_resp = await client.get(
        f"/api/v1/projects/{project_id}/reports/{report_id}", headers=headers
    )
    body = status_resp.json()
    assert body["status"] == "completed"
    assert body["pdf_available"] is True
    assert body["bundle_available"] is True
    # No measurements, no disaster screening -> genuinely no tabular data.
    assert body["csv_available"] is False

    pdf_resp = await client.get(
        f"/api/v1/projects/{project_id}/reports/{report_id}/pdf", headers=headers
    )
    assert pdf_resp.status_code == 200
    assert pdf_resp.content.startswith(b"%PDF-")

    csv_resp = await client.get(
        f"/api/v1/projects/{project_id}/reports/{report_id}/csv", headers=headers
    )
    assert csv_resp.status_code == 404

    json_resp = await client.get(
        f"/api/v1/projects/{project_id}/reports/{report_id}/json", headers=headers
    )
    assert json_resp.status_code == 200
    report_json = json_resp.json()
    assert report_json["dataset"]["id"] == dataset["id"]
    assert report_json["depth_analysis"]["calibration"]["status"] == "uncalibrated"
    # Real, non-fabricated per-stage generation timings — never invented.
    timings = report_json["generation_timings_seconds"]
    for key_name in ("build_report_data_seconds", "render_pdf_seconds", "render_csv_seconds"):
        assert isinstance(timings[key_name], float)
        assert timings[key_name] >= 0.0

    bundle_resp = await client.get(
        f"/api/v1/projects/{project_id}/reports/{report_id}/bundle", headers=headers
    )
    assert bundle_resp.status_code == 200
    with zipfile.ZipFile(io.BytesIO(bundle_resp.content)) as zf:
        names = zf.namelist()
        assert "report.pdf" in names
        assert "analysis.json" in names
        assert "artifacts/relative_depth.tif" in names
        # Independently verify the archived artifact matches the real stored file.
        assert (
            zf.read("artifacts/relative_depth.tif") == get_storage().absolute_path(key).read_bytes()
        )
        # The bundle's own analysis.json must always be byte-identical (as
        # parsed JSON) to the standalone JSON export — both are meant to be
        # the exact same real data snapshot, never two independently
        # re-derived copies that could silently drift apart.
        assert json.loads(zf.read("analysis.json")) == report_json


async def test_generate_report_failure_when_dataset_analysis_disappears(client):
    headers = await _register_and_login(client, "report-vanish@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_float_raster(key, np.ones((4, 4), dtype="float32"))
    await _create_artifact_row(job_id, "relative_depth", key, size)

    report_id = await _create_report_row(project_id, dataset["id"], user_id)

    # Simulate the job having become non-completed between report creation
    # and generation (e.g. it was somehow re-queued) — generation should
    # fail honestly rather than fabricate a report from nothing.
    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, job_id)
        job.status = AnalysisJobStatus.RUNNING
        await db.commit()

    await generate_report(report_id)

    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        assert report.status == ReportStatus.FAILED
        assert "No completed analysis" in report.error_message
        assert report.pdf_storage_key is None


async def test_generate_report_rq_timeout_becomes_explicit_failed_state(client, monkeypatch):
    from app.services.report_reconciliation import REPORT_TIMEOUT_ERROR_MESSAGE

    headers = await _register_and_login(client, "report-timeout@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_float_raster(key, np.ones((4, 4), dtype="float32"))
    await _create_artifact_row(job_id, "relative_depth", key, size)
    report_id = await _create_report_row(project_id, dataset["id"], user_id)

    def raise_rq_timeout(_context):
        raise JobTimeoutException("RQ execution limit reached")

    monkeypatch.setattr(report_execution, "build_report_data", raise_rq_timeout)
    await generate_report(report_id)

    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        assert report.status == ReportStatus.FAILED
        assert report.error_message == REPORT_TIMEOUT_ERROR_MESSAGE
        assert report.report_metadata is None
        assert report.pdf_storage_key is None
        assert report.csv_storage_key is None
        assert report.bundle_storage_key is None


async def test_report_ownership_enforced_cross_user_and_cross_project(client):
    owner_headers = await _register_and_login(client, "report-owner@example.com")
    other_headers = await _register_and_login(client, "report-intruder@example.com")
    project_id = await _create_project(client, owner_headers)
    dataset = await _upload_source_image(client, owner_headers, project_id)
    user_id = await _current_user_id(client, owner_headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_float_raster(key, np.ones((4, 4), dtype="float32"))
    await _create_artifact_row(job_id, "relative_depth", key, size)
    report_id = await _create_report_row(
        project_id,
        dataset["id"],
        user_id,
        status=ReportStatus.COMPLETED,
        report_metadata={"dummy": True},
    )

    for suffix in ("", "/json", "/pdf", "/csv", "/bundle"):
        resp = await client.get(
            f"/api/v1/projects/{project_id}/reports/{report_id}{suffix}", headers=other_headers
        )
        assert resp.status_code == 404, suffix

    # Another user's own project can't reach this report via a mismatched
    # project_id either (existence never leaked through a 403).
    other_project_id = await _create_project(client, other_headers, "Intruder Project")
    resp = await client.get(
        f"/api/v1/projects/{other_project_id}/reports/{report_id}", headers=other_headers
    )
    assert resp.status_code == 404


async def test_report_download_endpoints_reject_not_yet_completed_report(client):
    headers = await _register_and_login(client, "report-pending@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    report_id = await _create_report_row(project_id, dataset["id"], user_id)

    for suffix in ("/json", "/pdf", "/csv", "/bundle"):
        resp = await client.get(
            f"/api/v1/projects/{project_id}/reports/{report_id}{suffix}", headers=headers
        )
        assert resp.status_code == 422, suffix


async def test_report_persists_across_fresh_db_session(client):
    """Stands in for a backend/Docker restart — nothing here is cached
    in-process, so a brand-new AsyncSessionLocal must see exactly what a
    prior one committed (same technique used throughout this project's
    other persistence tests)."""
    headers = await _register_and_login(client, "report-persist@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_source_image(client, headers, project_id)
    user_id = await _current_user_id(client, headers)
    job_id = await _create_job_row(project_id, dataset["id"], user_id)
    key = f"projects/{project_id}/analysis/{job_id}/depth.tif"
    size = _write_float_raster(key, np.ones((4, 4), dtype="float32"))
    await _create_artifact_row(job_id, "relative_depth", key, size)

    # Inserted directly (never enqueued) so this test generates the report
    # exactly once — POST /reports would also queue it for the suite's own
    # worker, racing this in-process generate_report() on the same files.
    report_id = await _create_report_row(project_id, dataset["id"], user_id)
    await generate_report(report_id)

    async with AsyncSessionLocal() as db_a:
        report_a = await db_a.get(Report, report_id)
        pdf_key = report_a.pdf_storage_key
        metadata_a = report_a.report_metadata

    async with AsyncSessionLocal() as db_b:
        report_b = await db_b.get(Report, report_id)
        assert report_b.status == ReportStatus.COMPLETED
        assert report_b.pdf_storage_key == pdf_key
        assert report_b.report_metadata == metadata_a
        assert get_storage().absolute_path(pdf_key).is_file()


# --------------------------------------------------------------------------
# Real end-to-end: actual depth+calibration+disaster pipeline outputs
# --------------------------------------------------------------------------


def _fake_depth(width: int, height: int) -> np.ndarray:
    # A real, non-constant gradient (not a flat value) so calibration has
    # real variance to fit against.
    gradient = np.linspace(0.2, 1.0, width, dtype=np.float32)
    return np.tile(gradient, (height, 1))


def _depth_consistent_report_dem(scale: float) -> bytes:
    """DEM = scale * (the fake estimator's own depth) + 100, on the source
    grid. Validates pipeline/report behavior; NOT accuracy evidence."""
    return make_depth_consistent_dem_geotiff_bytes(
        _fake_depth(32, 32),
        scale=scale,
        noise_std=0.0,
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
    )


class _FastFakeDepthEstimator:
    """Stands in for the real ~1-2s Depth Anything V2 forward pass so this
    test runs quickly — the SAME established technique
    `tests/test_semantic_segmentation.py` already uses. Everything
    downstream of this (real calibration fitting, real terrain-derivative
    computation, real report generation) is genuinely computed, never
    mocked — this is the "at least one real E2E" test the Phase 9 spec
    requires."""

    def load(self):
        pass

    def predict(self, rgb_image):
        from ai.depth_estimator import DepthPrediction

        height, width = rgb_image.shape[0], rgb_image.shape[1]
        depth = _fake_depth(width, height)
        return DepthPrediction(
            depth=depth,
            inference_seconds=0.01,
            input_width=width,
            input_height=height,
            model_input_width=width,
            model_input_height=height,
        )

    def info(self):
        from ai.depth_estimator import DepthModelInfo

        return DepthModelInfo(
            name="fake", revision="n/a", source="n/a", license="n/a", device="cpu"
        )


async def test_real_e2e_report_reflects_actual_pipeline_outputs(client, monkeypatch):
    monkeypatch.setattr(
        analysis_execution, "get_depth_estimator", lambda: _FastFakeDepthEstimator()
    )

    headers = await _register_and_login(client, "report-e2e@example.com")
    project_id = await _create_project(client, headers)

    scene = make_structured_scene_geotiff_bytes(
        width=32,
        height=32,
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
    )
    source_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("scene.tif", scene, "image/tiff")},
        headers=headers,
    )
    source_id = source_resp.json()["id"]

    # Deliberately depth-consistent reference (elevations 100.4-102.0) so
    # the calibration quality gate has a real relationship to accept.
    dem = _depth_consistent_report_dem(scale=2.0)
    dem_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("dem.tif", dem, "image/tiff")},
        data={"role": "dem_reference"},
        headers=headers,
    )
    dem_id = dem_resp.json()["id"]

    # Real depth (faked model) + real calibration pipeline.
    job_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{source_id}/analysis",
        json={"parameters": {"version": "v1", "dem_reference_dataset_id": dem_id}},
        headers=headers,
    )
    job_id = job_resp.json()["id"]
    await analysis_execution.execute_analysis_job(uuid.UUID(job_id))

    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, uuid.UUID(job_id))
        assert job.status == AnalysisJobStatus.COMPLETED
        assert job.calibration_status == CalibrationStatus.CALIBRATED
        dsm_artifact_id = job.calibration_metadata["dsm_artifact_id"]

    # Real, standalone disaster-screening job over the real DSM produced above.
    disaster_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{source_id}/analysis",
        json={
            "parameters": {
                "version": "v1",
                "disaster_source_artifact_id": dsm_artifact_id,
                "run_flood_screening": True,
                # Inside the calibrated elevation range, so the flood
                # screen genuinely splits the raster.
                "water_level": 101.2,
                "run_landslide_screening": True,
            }
        },
        headers=headers,
    )
    disaster_job_id = disaster_resp.json()["id"]
    await analysis_execution.execute_analysis_job(uuid.UUID(disaster_job_id))

    async with AsyncSessionLocal() as db:
        disaster_job = await db.get(AnalysisJob, uuid.UUID(disaster_job_id))
        assert disaster_job.disaster_status == DisasterStatus.COMPLETED
        real_flood_pixels = disaster_job.disaster_metadata["flood"][
            "potentially_inundated_pixel_count"
        ]
        real_scale_a = job.calibration_metadata["scale_a"]

    # Real report generation over these real, just-produced outputs.
    # Inserted directly (never enqueued) so this test generates the report
    # exactly once — POST /reports would also queue it for the suite's own
    # worker, racing this in-process generate_report() on the same files.
    user_id = await _current_user_id(client, headers)
    report_id = str(await _create_report_row(project_id, source_id, user_id))
    await generate_report(uuid.UUID(report_id))

    json_resp = await client.get(
        f"/api/v1/projects/{project_id}/reports/{report_id}/json", headers=headers
    )
    assert json_resp.status_code == 200
    data = json_resp.json()

    # Every number in the report must trace to the real pipeline output —
    # not a re-derived, rounded, or coincidentally-similar value.
    assert data["depth_analysis"]["calibration"]["status"] == "calibrated"
    assert data["depth_analysis"]["calibration"]["scale_a"] == pytest.approx(real_scale_a)
    assert (
        data["disaster_screening"]["flood"]["potentially_inundated_pixel_count"]
        == real_flood_pixels
    )
    artifact_types = {a["artifact_type"] for a in data["artifacts"]}
    assert artifact_types == {
        "relative_depth",
        "metric_elevation",
        "dsm",
        "dtm",
        "ndsm",
        "calibration_residuals",
        "slope",
        "aspect",
        "hillshade",
        "flood_screening",
        "landslide_screening",
    }

    pdf_resp = await client.get(
        f"/api/v1/projects/{project_id}/reports/{report_id}/pdf", headers=headers
    )
    text = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf_resp.content)).pages)
    assert "calibrated" in text.lower()
    assert "Flood Screening" in text
    assert "Landslide Susceptibility Screening" in text
    assert "does not model rainfall" in text
    assert "not a calibrated or validated probability" in text

    bundle_resp = await client.get(
        f"/api/v1/projects/{project_id}/reports/{report_id}/bundle", headers=headers
    )
    with zipfile.ZipFile(io.BytesIO(bundle_resp.content)) as zf:
        names = set(zf.namelist())
        for expected in (
            "artifacts/relative_depth.tif",
            "artifacts/metric_elevation.tif",
            "artifacts/dsm.tif",
            "artifacts/dtm.tif",
            "artifacts/ndsm.tif",
            "artifacts/slope.tif",
            "artifacts/aspect.tif",
            "artifacts/flood_screening.tif",
        ):
            assert expected in names
        # Independently re-open one archived raster with rasterio to prove
        # it's a real, valid GeoTIFF, not an empty/corrupt placeholder.
        with rasterio.io.MemoryFile(zf.read("artifacts/dsm.tif")) as memfile:
            with memfile.open() as ds:
                assert ds.crs is not None
                assert ds.width == 32 and ds.height == 32

    # The passed gate is reported with its applied policy.
    cal = data["depth_analysis"]["calibration"]
    assert cal["quality_gate"]["passed"] is True
    assert cal["validation_metrics_scope"] == "in_sample_inliers"
    assert cal["cross_validation"]["feasible"] is True


async def test_quality_gate_failed_calibration_report_shows_diagnostics_and_reason(
    client, monkeypatch
):
    """(P1-2 #16) A real depth + calibration job whose fit is rejected by the
    quality gate (reference deliberately inverted relative to depth) still
    produces a report — the JSON and PDF both carry the failed status, the
    gate reason, every diagnostic kept on failure, the applied policy, and
    the explicit statement that the gate is not an accuracy standard. No
    metric_elevation/dsm is reported, because none was written."""
    monkeypatch.setattr(
        analysis_execution, "get_depth_estimator", lambda: _FastFakeDepthEstimator()
    )
    headers = await _register_and_login(client, "report-gate-failed@example.com")
    project_id = await _create_project(client, headers)
    scene = make_structured_scene_geotiff_bytes(
        width=32,
        height=32,
        crs=_UTM_CRS,
        origin_x=_ORIGIN_X,
        origin_y=_ORIGIN_Y,
        pixel_size=_PIXEL_SIZE,
    )
    source_id = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets",
            files={"file": ("scene.tif", scene, "image/tiff")},
            headers=headers,
        )
    ).json()["id"]
    dem_id = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets",
            files={"file": ("dem.tif", _depth_consistent_report_dem(scale=-2.0), "image/tiff")},
            data={"role": "dem_reference"},
            headers=headers,
        )
    ).json()["id"]
    job_id = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets/{source_id}/analysis",
            json={"parameters": {"version": "v1", "dem_reference_dataset_id": dem_id}},
            headers=headers,
        )
    ).json()["id"]
    await analysis_execution.execute_analysis_job(uuid.UUID(job_id))

    async with AsyncSessionLocal() as db:
        job = await db.get(AnalysisJob, uuid.UUID(job_id))
        assert job.status == AnalysisJobStatus.COMPLETED
        assert job.calibration_status == CalibrationStatus.FAILED

    # Inserted directly (never enqueued) so this test generates the report
    # exactly once — POST /reports would also queue it for the suite's own
    # worker, racing this in-process generate_report() on the same files.
    user_id = await _current_user_id(client, headers)
    report_id = str(await _create_report_row(project_id, source_id, user_id))
    await generate_report(uuid.UUID(report_id))

    data = (
        await client.get(f"/api/v1/projects/{project_id}/reports/{report_id}/json", headers=headers)
    ).json()
    cal = data["depth_analysis"]["calibration"]
    assert cal["status"] == "failed"
    assert "G1_expected_scale_sign" in cal["error"]
    assert cal["quality_gate"]["passed"] is False
    assert [c["criterion"] for c in cal["quality_gate"]["failed_criteria"]] == [
        "G1_expected_scale_sign"
    ]
    assert cal["quality_gate"]["policy"]["min_cv_skill"] == 0.0
    assert cal["scale_a"] == pytest.approx(-2.0, abs=1e-4)
    assert cal["fit_diagnostics"]["pearson_r"] == pytest.approx(-1.0, abs=1e-6)
    assert cal["cross_validation"]["feasible"] is True
    assert cal["validation_metrics_scope"] == "in_sample_inliers"
    assert cal["dsm_artifact_id"] is None and cal["metric_elevation_artifact_id"] is None
    assert {a["artifact_type"] for a in data["artifacts"]} == {"relative_depth"}

    pdf_resp = await client.get(
        f"/api/v1/projects/{project_id}/reports/{report_id}/pdf", headers=headers
    )
    assert pdf_resp.status_code == 200
    raw = "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf_resp.content)).pages)
    text = " ".join(raw.split())
    for expected in (
        "In-sample inlier MAE / RMSE / bias",
        "In-sample diagnostics (all valid samples)",
        "Held-out cross-validation",
        "Held-out skill vs. mean baseline",
        "Calibration quality gate",
        "Failed",
        "G1_expected_scale_sign",
        "not an empirically validated accuracy standard",
    ):
        assert expected in text, expected
