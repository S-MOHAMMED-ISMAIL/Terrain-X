import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import numpy as np
from sqlalchemy import select, update

from app.core.storage import get_storage
from app.db.session import AsyncSessionLocal
from app.models.analysis_artifact import AnalysisArtifact
from app.models.analysis_job import AnalysisJob, AnalysisJobStatus, AnalysisStage
from app.services import analysis_execution
from tests.fixtures import make_jpeg_bytes


async def _register_and_login(client, email: str, password: str = "supersecret123") -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_project(client, headers: dict, name: str = "Analysis Test Project") -> str:
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    return resp.json()["id"]


async def _upload_dataset(
    client, headers: dict, project_id: str, filename: str = "photo.jpg"
) -> dict:
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": (filename, make_jpeg_bytes(), "image/jpeg")},
        headers=headers,
    )
    return resp.json()


async def _wait_for_terminal(
    client, project_id: str, job_id: str, headers: dict, timeout: float = 10.0
) -> dict:
    """Polls the real API, backed by the real database, waiting for the
    real separate worker container (driven by Redis/RQ, exactly as in
    production) to finish processing this job."""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        resp = await client.get(f"/api/v1/projects/{project_id}/analysis/{job_id}", headers=headers)
        body = resp.json()
        if body["status"] not in ("queued", "running"):
            return body
        await asyncio.sleep(0.2)
    raise AssertionError(f"Job {job_id} did not reach a terminal status within {timeout}s")


async def _insert_queued_job(client, headers, project_id, dataset_id, parameters=None) -> uuid.UUID:
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]
    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset_id),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.QUEUED,
            parameters=parameters or {"version": "v1"},
        )
        db.add(job)
        await db.commit()
        return job.id


class _SlowFakeEstimator:
    """A DepthEstimator stand-in with a controllable predict() delay and
    call tracking, used only for precise cancellation-timing control (racing
    the real model wouldn't give reliable enough timing) -- mirrors the
    identical fixture already used for this purpose in
    test_depth_pipeline.py."""

    def __init__(self, predict_delay: float = 0.0):
        self.predict_delay = predict_delay
        self.predict_called = False

    def load(self) -> None:
        pass

    def predict(self, rgb_image):
        import time

        from ai.depth_estimator import DepthPrediction

        self.predict_called = True
        time.sleep(self.predict_delay)
        height, width = rgb_image.shape[0], rgb_image.shape[1]
        return DepthPrediction(
            depth=np.zeros((height, width), dtype=np.float32),
            inference_seconds=self.predict_delay,
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


async def test_create_analysis_requires_authentication(client):
    resp = await client.post(
        f"/api/v1/projects/{uuid.uuid4()}/datasets/{uuid.uuid4()}/analysis",
        json={},
    )
    assert resp.status_code == 401


async def test_create_analysis_nonexistent_project_rejected(client):
    headers = await _register_and_login(client, "an-owner1@example.com")
    resp = await client.post(
        f"/api/v1/projects/{uuid.uuid4()}/datasets/{uuid.uuid4()}/analysis",
        json={},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_create_analysis_nonexistent_dataset_rejected(client):
    headers = await _register_and_login(client, "an-owner2@example.com")
    project_id = await _create_project(client, headers)
    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{uuid.uuid4()}/analysis",
        json={},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_create_analysis_dataset_from_other_project_rejected(client):
    headers = await _register_and_login(client, "an-owner3@example.com")
    project_a = await _create_project(client, headers, "Project A")
    project_b = await _create_project(client, headers, "Project B")
    dataset = await _upload_dataset(client, headers, project_a)

    # dataset belongs to project_a; requesting it via project_b's URL must 404
    resp = await client.post(
        f"/api/v1/projects/{project_b}/datasets/{dataset['id']}/analysis",
        json={},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_create_analysis_other_users_project_rejected(client):
    headers_owner = await _register_and_login(client, "an-owner4@example.com")
    headers_intruder = await _register_and_login(client, "an-intruder4@example.com")
    project_id = await _create_project(client, headers_owner)
    dataset = await _upload_dataset(client, headers_owner, project_id)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/analysis",
        json={},
        headers=headers_intruder,
    )
    assert resp.status_code == 404


async def test_invalid_dataset_cannot_be_analyzed(client):
    headers = await _register_and_login(client, "an-owner5@example.com")
    project_id = await _create_project(client, headers)

    # Bytes with valid JPEG magic bytes but a corrupt body -> dataset created
    # but marked invalid (mirrors tests/test_datasets.py behavior).
    from tests.fixtures import make_corrupt_jpeg_bytes

    upload_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("corrupt.jpg", make_corrupt_jpeg_bytes(), "image/jpeg")},
        headers=headers,
    )
    dataset = upload_resp.json()
    assert dataset["status"] == "invalid"

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/analysis",
        json={},
        headers=headers,
    )
    assert resp.status_code == 422

    list_resp = await client.get(f"/api/v1/projects/{project_id}/analysis", headers=headers)
    assert list_resp.json() == []


async def test_valid_dataset_creates_job_persists_parameters_and_reaches_worker(client):
    headers = await _register_and_login(client, "an-owner6@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)

    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/analysis",
        json={"parameters": {"version": "v1"}},
        headers=headers,
    )
    assert create_resp.status_code == 201
    job = create_resp.json()
    assert job["status"] in ("queued", "running")
    assert job["dataset_id"] == dataset["id"]
    assert job["project_id"] == project_id
    # Phase 4/6 added optional calibration/semantic fields to
    # AnalysisParametersV1; a request that never mentions them still stores
    # them explicitly with their defaults (Pydantic's default), not just
    # "version".
    assert job["parameters"] == {
        "version": "v1",
        "dem_reference_dataset_id": None,
        "gcp_reference_dataset_id": None,
        "enable_semantic_segmentation": False,
        "disaster_source_artifact_id": None,
        "run_flood_screening": False,
        "water_level": None,
        "run_landslide_screening": False,
        "landslide_thresholds": None,
    }

    # Confirm the row is real and durable in Postgres, independent of the API.
    async with AsyncSessionLocal() as db:
        db_job = await db.get(AnalysisJob, uuid.UUID(job["id"]))
        assert db_job is not None
        assert db_job.parameters == {
            "version": "v1",
            "dem_reference_dataset_id": None,
            "gcp_reference_dataset_id": None,
            "enable_semantic_segmentation": False,
            "disaster_source_artifact_id": None,
            "run_flood_screening": False,
            "water_level": None,
            "run_landslide_screening": False,
            "landslide_thresholds": None,
        }

    # The real, separate worker container (driven by real Redis/RQ) must pick
    # this up, actually load the real depth model, run real inference, and
    # complete it — no mocking of the job lifecycle or the model itself.
    # Generous timeout: this exercises the real model end-to-end, including
    # a cold model load on a fresh worker process.
    final = await _wait_for_terminal(client, project_id, job["id"], headers, timeout=120.0)
    assert final["status"] == "completed"
    assert final["current_stage"] == "completed"
    assert final["progress"] == 100
    assert final["started_at"] is not None
    assert final["completed_at"] is not None
    assert final["execution_summary"]["reopened_file"] is True
    assert final["execution_summary"]["width"] == dataset["width"]
    assert "band_statistics" in final["execution_summary"]
    depth_summary = final["execution_summary"]["depth_estimation"]
    assert depth_summary["model_name"] == "depth-anything/Depth-Anything-V2-Small-hf"
    assert depth_summary["device"] == "cpu"
    assert depth_summary["inference_seconds"] > 0
    assert depth_summary["output_width"] == dataset["width"]
    assert depth_summary["output_height"] == dataset["height"]

    # Phase 3 legitimately creates exactly one real artifact per completed
    # job — the relative-depth raster — never fabricated, never more than
    # what this run actually produced.
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(AnalysisArtifact).where(AnalysisArtifact.analysis_job_id == uuid.UUID(job["id"]))
        )
        artifacts = result.scalars().all()
    assert len(artifacts) == 1
    artifact = artifacts[0]
    assert artifact.artifact_type == "relative_depth"
    assert artifact.mime_type == "image/tiff"
    assert artifact.file_size_bytes > 0
    assert artifact.artifact_metadata["model_name"] == "depth-anything/Depth-Anything-V2-Small-hf"
    assert artifact.artifact_metadata["is_georeferenced"] is False
    assert artifact.artifact_metadata["crs"] is None
    assert "NOT metric elevation" in artifact.artifact_metadata["value_semantics"]

    storage = get_storage()
    assert storage.exists(artifact.storage_key)


async def test_missing_storage_file_causes_real_failure(client):
    from app.core.storage import get_storage

    headers = await _register_and_login(client, "an-owner7@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)

    # Simulate external interference: the file is gone, but the DB row isn't.
    storage = get_storage()
    storage_key = f"projects/{project_id}/datasets/{dataset['id']}/original.jpg"
    storage.delete(storage_key)
    assert not storage.exists(storage_key)

    create_resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/analysis",
        json={},
        headers=headers,
    )
    job = create_resp.json()

    final = await _wait_for_terminal(client, project_id, job["id"], headers)
    assert final["status"] == "failed"
    assert "missing from storage" in final["error_message"]
    assert final["execution_summary"] is None
    assert final["completed_at"] is not None


async def test_worker_unexpected_exception_produces_failed_job_with_sanitized_message(client):
    """Exercises the catch-all failure branch directly (in-process,
    monkeypatched) since a bare exception deep inside rasterio isn't
    something we can reliably trigger through the real, separate worker
    process from a test. The happy-path and missing-file tests above already
    cover the real, unmocked worker/Redis/RQ path end-to-end.

    The job row here is inserted directly and never enqueued to Redis, so
    the real worker container has no way to see or touch it — there is no
    race with it, unlike calling the create-job API (which would enqueue it
    for the real worker to pick up concurrently with this test's own direct
    call to execute_analysis_job)."""
    from app.services import analysis_execution

    headers = await _register_and_login(client, "an-owner8@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]

    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.QUEUED,
            parameters={"version": "v1"},
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    with patch.object(
        analysis_execution, "extract_raster_metadata", side_effect=RuntimeError("boom")
    ):
        await analysis_execution.execute_analysis_job(job_id)

    resp = await client.get(f"/api/v1/projects/{project_id}/analysis/{job_id}", headers=headers)
    body = resp.json()
    assert body["status"] == "failed"
    assert body["error_message"] == "An internal error occurred while processing this job."
    assert "boom" not in body["error_message"]
    assert "RuntimeError" not in body["error_message"]


async def test_job_history_and_detail_apis(client):
    headers = await _register_and_login(client, "an-owner9@example.com")
    project_id = await _create_project(client, headers)
    dataset_a = await _upload_dataset(client, headers, project_id, "a.jpg")
    dataset_b = await _upload_dataset(client, headers, project_id, "b.jpg")

    job_a = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets/{dataset_a['id']}/analysis",
            json={},
            headers=headers,
        )
    ).json()
    job_b = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets/{dataset_b['id']}/analysis",
            json={},
            headers=headers,
        )
    ).json()

    list_resp = await client.get(f"/api/v1/projects/{project_id}/analysis", headers=headers)
    assert list_resp.status_code == 200
    job_ids = {j["id"] for j in list_resp.json()}
    assert {job_a["id"], job_b["id"]} <= job_ids

    detail_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job_a['id']}", headers=headers
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["id"] == job_a["id"]


async def test_cross_user_job_access_rejected(client):
    headers_owner = await _register_and_login(client, "an-owner10@example.com")
    headers_intruder = await _register_and_login(client, "an-intruder10@example.com")
    project_id = await _create_project(client, headers_owner)
    dataset = await _upload_dataset(client, headers_owner, project_id)
    job = (
        await client.post(
            f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/analysis",
            json={},
            headers=headers_owner,
        )
    ).json()

    resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}", headers=headers_intruder
    )
    assert resp.status_code == 404

    list_resp = await client.get(
        f"/api/v1/projects/{project_id}/analysis", headers=headers_intruder
    )
    assert list_resp.status_code == 404

    cancel_resp = await client.post(
        f"/api/v1/projects/{project_id}/analysis/{job['id']}/cancel", headers=headers_intruder
    )
    assert cancel_resp.status_code == 404


async def test_duplicate_active_job_rejected_and_allowed_after_terminal(client):
    """Tests the duplicate-active-job invariant directly against the
    database rather than racing the real worker (which may finish a Phase 2
    job in well under a second, making a timing-based test flaky)."""
    headers = await _register_and_login(client, "an-owner11@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)

    async with AsyncSessionLocal() as db:
        active_job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["id"]),
            status=AnalysisJobStatus.RUNNING,
            parameters={"version": "v1"},
        )
        db.add(active_job)
        await db.commit()

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/analysis",
        json={},
        headers=headers,
    )
    assert resp.status_code == 409

    # Once the active job reaches a terminal status, a new one is allowed.
    async with AsyncSessionLocal() as db:
        db_job = await db.get(AnalysisJob, active_job.id)
        db_job.status = AnalysisJobStatus.COMPLETED
        db_job.completed_at = datetime.now(UTC)
        await db.commit()

    resp2 = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/analysis",
        json={},
        headers=headers,
    )
    assert resp2.status_code == 201


async def test_cancel_queued_job(client):
    headers = await _register_and_login(client, "an-owner12@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)

    # Force the job to stay queued (not picked up by the real worker) so the
    # cancel path is deterministic, by directly inserting it as queued and
    # never enqueueing it to Redis.
    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["id"]),
            status=AnalysisJobStatus.QUEUED,
            parameters={"version": "v1"},
        )
        db.add(job)
        await db.commit()
        job_id = str(job.id)

    cancel_resp = await client.post(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/cancel", headers=headers
    )
    assert cancel_resp.status_code == 200
    body = cancel_resp.json()
    assert body["status"] == "cancelled"
    assert body["completed_at"] is not None

    second_cancel = await client.post(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/cancel", headers=headers
    )
    assert second_cancel.status_code == 409


async def test_cancelled_before_worker_claim_never_executes(client):
    """A job cancelled after being inserted but before execute_analysis_job
    claims it must stay cancelled — the worker's compare-and-swap claim
    (WHERE status='queued') must fail and it must not touch the row further
    (no started_at, no stage advance, no execution_summary)."""
    headers = await _register_and_login(client, "an-owner14@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]

    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.QUEUED,
            parameters={"version": "v1"},
        )
        db.add(job)
        await db.commit()
        job_id = job.id

        # Cancellation lands before the worker ever looks at this job.
        await db.execute(
            update(AnalysisJob)
            .where(AnalysisJob.id == job_id)
            .values(status=AnalysisJobStatus.CANCELLED, completed_at=datetime.now(UTC))
        )
        await db.commit()

    await analysis_execution.execute_analysis_job(job_id)

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.CANCELLED
        assert final.current_stage == AnalysisStage.QUEUED
        assert final.started_at is None
        assert final.execution_summary is None


async def test_concurrent_cancel_and_worker_claim_are_mutually_exclusive(client):
    """Regression test for a lost-update race: the cancel endpoint and the
    worker's own job-claim step both perform a conditional
    UPDATE ... WHERE status='queued'. If they land concurrently (cancel
    request arrives in the narrow window between the worker reading the job
    and the worker committing its own 'running' transition), exactly one of
    the two compare-and-swaps must win — the loser must be a no-op, never a
    silent overwrite of the winner's result.

    This races the *actual* execute_analysis_job function (not a stand-in)
    against a real cancel UPDATE, each against the same freshly-queued job
    row from its own independent session/connection — so a future regression
    back to an unconditional "job.status = RUNNING; commit()" in the real
    code would make this test flaky/fail, not silently pass.
    """
    headers = await _register_and_login(client, "an-owner15@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]

    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.QUEUED,
            parameters={"version": "v1"},
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    async def try_cancel() -> int:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(AnalysisJob)
                .where(AnalysisJob.id == job_id, AnalysisJob.status == AnalysisJobStatus.QUEUED)
                .values(status=AnalysisJobStatus.CANCELLED, completed_at=datetime.now(UTC))
            )
            await db.commit()
            return result.rowcount

    cancel_rowcount, _ = await asyncio.gather(
        try_cancel(), analysis_execution.execute_analysis_job(job_id)
    )

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)

    if cancel_rowcount == 1:
        # Cancel won the race: the worker's own claim must have found the
        # row already changed and backed off entirely — never overwritten
        # back to running, and never allowed to proceed into execution.
        assert final.status == AnalysisJobStatus.CANCELLED
        assert final.current_stage == AnalysisStage.QUEUED
        assert final.started_at is None
        assert final.execution_summary is None
    else:
        # The worker's claim won: cancel found no queued row left to cancel
        # and was correctly a no-op; the job ran through to completion.
        assert cancel_rowcount == 0
        assert final.status == AnalysisJobStatus.COMPLETED


async def test_stages_are_actually_persisted_to_postgres_when_reached(client):
    """Proves each AnalysisStage transition is a real, durable commit visible
    to a separate connection — not merely an in-memory enum value that only
    becomes visible once the whole job finishes.

    The job row is inserted directly and never enqueued to Redis, and
    execute_analysis_job is invoked directly in-process (in the same asyncio
    event loop as this test) rather than through the real worker container.
    This is necessary here — and only here — because the 'inference' stage
    is deliberately slowed down (via a lightweight fake DepthEstimator, so
    this test needs no network access and doesn't depend on real model
    behavior) so a concurrent poller (its own independent DB session/
    connection) has a real chance to observe it before the job finishes; a
    patch applied in this test process has no effect on the separate worker
    container's own process, so proving mid-flight visibility requires
    running the real execution function here. Every stage-transition commit
    exercised is identical to what the real worker calls — only the RQ
    dispatch layer and the model itself are bypassed. The real-model
    integration tests below cover actual inference; the happy-path and
    missing-file tests above cover the real worker/Redis/RQ dispatch layer
    end-to-end.
    """
    headers = await _register_and_login(client, "an-owner16@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]

    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.QUEUED,
            parameters={"version": "v1"},
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    observed_stages: list[str] = []

    async def poll_stages_from_independent_session() -> None:
        for _ in range(300):
            async with AsyncSessionLocal() as poll_db:
                row = await poll_db.get(AnalysisJob, job_id)
                if row is not None and (
                    not observed_stages or observed_stages[-1] != row.current_stage.value
                ):
                    observed_stages.append(row.current_stage.value)
                if row is not None and row.status in (
                    AnalysisJobStatus.COMPLETED,
                    AnalysisJobStatus.FAILED,
                ):
                    return
            await asyncio.sleep(0.01)

    class SlowFakeEstimator:
        """Implements the DepthEstimator interface with a real, deliberate
        delay in predict() — just enough for the concurrent poller above to
        reliably observe the 'inference' stage's commit. Not used to test
        depth *correctness* (that's the real-model integration tests below)."""

        def load(self) -> None:
            pass

        def predict(self, rgb_image):
            import time

            import numpy as np

            from ai.depth_estimator import DepthPrediction

            time.sleep(0.3)
            height, width = rgb_image.shape[0], rgb_image.shape[1]
            return DepthPrediction(
                depth=np.zeros((height, width), dtype=np.float32),
                inference_seconds=0.3,
                input_width=width,
                input_height=height,
                model_input_width=width,
                model_input_height=height,
            )

        def info(self):
            from ai.depth_estimator import DepthModelInfo

            return DepthModelInfo(
                name="fake-estimator-for-stage-persistence-test",
                revision="n/a",
                source="n/a",
                license="n/a",
                device="cpu",
            )

    with patch.object(analysis_execution, "get_depth_estimator", return_value=SlowFakeEstimator()):
        await asyncio.gather(
            poll_stages_from_independent_session(),
            analysis_execution.execute_analysis_job(job_id),
        )

    # The independent poller, reading through its own session/connection,
    # must have observed the 'inference' stage committed and visible before
    # the job reached a terminal status — proving that intermediate commit
    # was real, not something only reconstructable after the fact.
    assert "inference" in observed_stages

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.COMPLETED
        assert final.current_stage == AnalysisStage.COMPLETED


# --------------------------------------------------------------------------
# Phase 11: explicit RQ job timeout
# --------------------------------------------------------------------------


async def test_created_job_is_enqueued_with_the_configured_explicit_timeout(client):
    """Real, non-mocked check: a job created through the real API is
    enqueued to the real Redis-backed queue with an explicit job_timeout
    equal to Settings.ANALYSIS_JOB_TIMEOUT_SECONDS -- proving RQ's old
    implicit 180s default is no longer silently in effect."""
    from rq.job import Job

    from app.core.config import get_settings
    from app.jobs.queue import get_redis_connection

    headers = await _register_and_login(client, "an-timeout1@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets/{dataset['id']}/analysis",
        json={},
        headers=headers,
    )
    assert resp.status_code == 201
    job_id = resp.json()["id"]

    rq_job = Job.fetch(job_id, connection=get_redis_connection())
    assert rq_job.timeout == get_settings().ANALYSIS_JOB_TIMEOUT_SECONDS
    assert rq_job.timeout != 180  # not silently left at RQ's old implicit default


# --------------------------------------------------------------------------
# Phase 11: running-job cooperative cancellation
# --------------------------------------------------------------------------


async def test_cancel_running_job_via_api_is_accepted(client):
    """Widened Phase 11 behavior: a job already 'running' (not just
    'queued') can now be cancelled through the public API. The endpoint can
    only ever *request* cancellation of a running job — it flips the DB
    status; the worker's own checkpoints are what actually stop the
    pipeline (see the checkpoint-observation test below)."""
    headers = await _register_and_login(client, "an-owner16@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]

    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.RUNNING,
            started_at=datetime.now(UTC),
            parameters={"version": "v1"},
        )
        db.add(job)
        await db.commit()
        job_id = str(job.id)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/cancel", headers=headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "cancelled"
    assert body["completed_at"] is not None

    # Already terminal now -- a second cancel request is rejected, exactly
    # like the pre-existing queued-job behavior.
    second = await client.post(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/cancel", headers=headers
    )
    assert second.status_code == 409


async def test_cancel_completed_job_is_rejected(client):
    headers = await _register_and_login(client, "an-owner17@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]

    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.COMPLETED,
            completed_at=datetime.now(UTC),
            parameters={"version": "v1"},
        )
        db.add(job)
        await db.commit()
        job_id = str(job.id)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/cancel", headers=headers
    )
    assert resp.status_code == 409

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, uuid.UUID(job_id))
        assert final.status == AnalysisJobStatus.COMPLETED  # untouched


async def test_cancel_failed_job_is_rejected(client):
    headers = await _register_and_login(client, "an-owner18@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]

    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.FAILED,
            error_message="a real prior failure",
            completed_at=datetime.now(UTC),
            parameters={"version": "v1"},
        )
        db.add(job)
        await db.commit()
        job_id = str(job.id)

    resp = await client.post(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/cancel", headers=headers
    )
    assert resp.status_code == 409

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, uuid.UUID(job_id))
        assert final.status == AnalysisJobStatus.FAILED  # untouched
        assert final.error_message == "a real prior failure"


async def test_running_job_checkpoint_observes_cancellation_requested_via_real_api(
    client, monkeypatch
):
    """End-to-end proof that the public cancel endpoint's write is what a
    real in-flight pipeline observes: a (fake, slow) depth estimator holds
    the job inside a genuinely blocking predict() call while a concurrent,
    real HTTP call POSTs .../cancel; the pipeline must then stop at its next
    checkpoint and the job must end 'cancelled', never 'completed' -- and it
    must not claim to have interrupted the in-flight predict() call itself
    (predict_called is True either way: cancellation is observed only once
    predict() returns).

    execute_analysis_job runs in a genuinely separate OS thread (its own
    event loop, via asyncio.to_thread), not merely a sibling asyncio task on
    this test's own event loop -- predict()'s time.sleep() is a real
    blocking call, exactly like the real model's forward pass, and would
    otherwise starve this same event loop and prevent the concurrent HTTP
    cancel request from ever actually being sent while the job holds
    'inference'. A separate thread is also a closer analogue of production,
    where the worker is a genuinely separate OS process from the backend
    serving this HTTP request.
    """
    headers = await _register_and_login(client, "an-owner19@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    job_id = await _insert_queued_job(client, headers, project_id, dataset["id"])

    fake_estimator = _SlowFakeEstimator(predict_delay=0.5)
    monkeypatch.setattr(analysis_execution, "get_depth_estimator", lambda: fake_estimator)

    def run_job_in_its_own_thread_and_event_loop() -> None:
        asyncio.run(analysis_execution.execute_analysis_job(job_id))

    worker_future = asyncio.create_task(asyncio.to_thread(run_job_in_its_own_thread_and_event_loop))

    # Poll the real API (never a fixed sleep guess) until predict() has
    # genuinely been entered, then cancel via the real endpoint.
    deadline = asyncio.get_running_loop().time() + 5.0
    while not fake_estimator.predict_called:
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("Job never entered predict() in time")
        await asyncio.sleep(0.02)

    cancel_resp = await client.post(
        f"/api/v1/projects/{project_id}/analysis/{job_id}/cancel", headers=headers
    )
    await worker_future

    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"
    assert fake_estimator.predict_called is True  # inference was NOT interrupted mid-call

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.CANCELLED
        assert final.execution_summary is None


async def test_cancellation_race_with_completion_leaves_exactly_one_final_state(
    client, monkeypatch
):
    """Regression test for a lost-update race at the OTHER end of execution
    (the existing test above already covers the race at claim-time): the
    real completion write in execute_analysis_job is a conditional UPDATE
    (WHERE status='running'), not a plain ORM mutate-then-commit, so a
    cancel request landing in the narrow window between _run_stages()
    finishing and that final commit can never be silently overwritten back
    to 'completed' -- and, symmetrically, a cancel request that loses the
    race can never corrupt a job that has already really completed.
    _run_stages is stubbed only for deterministic timing (its own internal
    correctness is covered exhaustively elsewhere); the claim and completion
    code under test is the real, unmodified production code."""
    headers = await _register_and_login(client, "an-owner20@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    job_id = await _insert_queued_job(client, headers, project_id, dataset["id"])

    async def slow_run_stages(db, job):
        await asyncio.sleep(0.3)
        return {"depth_estimation": {"model_name": "fake", "model_revision": "n/a"}}

    monkeypatch.setattr(analysis_execution, "_run_stages", slow_run_stages)

    async def cancel_mid_flight():
        await asyncio.sleep(0.1)  # let execute_analysis_job claim the job first
        return await client.post(
            f"/api/v1/projects/{project_id}/analysis/{job_id}/cancel", headers=headers
        )

    cancel_resp, _ = await asyncio.gather(
        cancel_mid_flight(), analysis_execution.execute_analysis_job(job_id)
    )

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)

    if cancel_resp.status_code == 200:
        # Cancellation won: the completion commit's WHERE status='running'
        # must have matched zero rows and been skipped entirely.
        assert final.status == AnalysisJobStatus.CANCELLED
        assert final.execution_summary is None
    else:
        # Completion won: cancellation must have been correctly rejected as
        # already-terminal, not silently ignored or double-applied.
        assert cancel_resp.status_code == 409
        assert final.status == AnalysisJobStatus.COMPLETED
        assert final.execution_summary is not None


async def test_cancellation_after_earlier_stage_preserves_its_already_written_artifact(
    client, monkeypatch
):
    """Checkpoints only ever sit at safe stage boundaries -- never mid-write
    -- so a real artifact from an earlier, fully-committed stage must never
    be deleted just because a LATER checkpoint observes cancellation. Uses
    the real Depth Anything model for the depth stage (proving a real
    artifact was genuinely written), then a fake, self-cancelling semantic
    estimator to deterministically trigger cancellation during the
    semantic-segmentation stage that follows."""
    from app.services import semantic_pipeline

    headers = await _register_and_login(client, "an-owner21@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]

    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset["id"]),
            user_id=uuid.UUID(user_id),
            status=AnalysisJobStatus.QUEUED,
            parameters={"version": "v1", "enable_semantic_segmentation": True},
        )
        db.add(job)
        await db.commit()
        job_id = job.id

    class _CancelDuringSemanticPredict:
        """Mirrors _CancelDuringPredictEstimator's technique (cancels via a
        genuinely separate DB connection, from inside predict()) applied to
        the semantic stage instead of the depth stage."""

        def load(self) -> None:
            pass

        def predict(self, rgb_image, *, min_region_area_px=0):
            from sqlalchemy import create_engine, text

            from ai.semantic_estimator import SemanticPrediction
            from app.core.config import get_settings

            engine = create_engine(get_settings().DATABASE_URL_SYNC)
            try:
                with engine.connect() as conn:
                    conn.execute(
                        text("UPDATE analysis_jobs SET status = 'cancelled' WHERE id = :id"),
                        {"id": str(job_id)},
                    )
                    conn.commit()
            finally:
                engine.dispose()

            height, width = rgb_image.shape[0], rgb_image.shape[1]
            return SemanticPrediction(
                label_map=np.zeros((height, width), dtype=np.uint32),
                regions=[],
                inference_seconds=0.01,
                input_width=width,
                input_height=height,
            )

        def info(self):
            from ai.semantic_estimator import SemanticModelInfo

            return SemanticModelInfo(
                name="fake",
                task="class-agnostic region segmentation",
                repository="n/a",
                revision="n/a",
                checkpoint="n/a",
                license="n/a",
                device="cpu",
            )

    monkeypatch.setattr(
        semantic_pipeline, "get_semantic_estimator", lambda: _CancelDuringSemanticPredict()
    )

    await analysis_execution.execute_analysis_job(job_id)

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.CANCELLED

        result = await db.execute(
            select(AnalysisArtifact).where(AnalysisArtifact.analysis_job_id == job_id)
        )
        artifacts = result.scalars().all()

    # The real depth artifact, fully written and committed before the
    # semantic stage (and its cancellation) ever began, must still exist --
    # untouched, not deleted as "partial" cleanup.
    assert len(artifacts) == 1
    assert artifacts[0].artifact_type == "relative_depth"
    assert get_storage().exists(artifacts[0].storage_key)
