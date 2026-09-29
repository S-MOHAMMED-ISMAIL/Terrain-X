"""Phase 11: stale/crashed analysis-job reconciliation
(app/services/job_reconciliation.py).

These tests manipulate `updated_at` directly (backdating it past/within the
configured staleness threshold) rather than actually waiting in real time,
for the same reason other timing-sensitive tests in this project use
controllable fakes instead of real sleeps: deterministic, fast, and not
flaky. The reconciliation function itself is exercised directly and for
real against the real database -- no mocking of the SQL/atomicity under
test.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.db.session import AsyncSessionLocal
from app.models.analysis_job import AnalysisJob, AnalysisJobStatus
from app.services.job_reconciliation import (
    STALE_JOB_ERROR_MESSAGE,
    reconcile_stale_running_jobs,
)


async def _register_and_login(client, email: str, password: str = "supersecret123") -> dict:
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    resp = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _create_project(client, headers: dict, name: str = "Reconciliation Test Project") -> str:
    resp = await client.post("/api/v1/projects", json={"name": name}, headers=headers)
    return resp.json()["id"]


async def _upload_dataset(client, headers: dict, project_id: str) -> dict:
    from tests.fixtures import make_jpeg_bytes

    resp = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("photo.jpg", make_jpeg_bytes(), "image/jpeg")},
        headers=headers,
    )
    return resp.json()


async def _make_job(
    client, headers, project_id, dataset_id, *, status: AnalysisJobStatus, updated_at: datetime
) -> uuid.UUID:
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]
    async with AsyncSessionLocal() as db:
        job = AnalysisJob(
            project_id=uuid.UUID(project_id),
            dataset_id=uuid.UUID(dataset_id),
            user_id=uuid.UUID(user_id),
            status=status,
            started_at=datetime.now(UTC) if status != AnalysisJobStatus.QUEUED else None,
            completed_at=(
                datetime.now(UTC)
                if status in (AnalysisJobStatus.COMPLETED, AnalysisJobStatus.FAILED)
                else None
            ),
            error_message="a real prior failure" if status == AnalysisJobStatus.FAILED else None,
            parameters={"version": "v1"},
            updated_at=updated_at,
        )
        db.add(job)
        await db.commit()
        return job.id


def _stale_timestamp() -> datetime:
    settings = get_settings()
    return datetime.now(UTC) - timedelta(seconds=settings.STALE_JOB_AFTER_SECONDS + 60)


def _fresh_timestamp() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=5)


async def test_stale_running_job_is_reconciled_to_failed_with_honest_message(client):
    headers = await _register_and_login(client, "reconcile-owner1@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    job_id = await _make_job(
        client,
        headers,
        project_id,
        dataset["id"],
        status=AnalysisJobStatus.RUNNING,
        updated_at=_stale_timestamp(),
    )

    async with AsyncSessionLocal() as db:
        reconciled = await reconcile_stale_running_jobs(db)

    assert str(job_id) in reconciled

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.FAILED
        assert final.error_message == STALE_JOB_ERROR_MESSAGE
        assert final.completed_at is not None


async def test_recently_updated_running_job_is_never_falsely_reaped(client):
    """A job that is genuinely still active (its updated_at is recent --
    e.g. it just committed a real stage transition, or is mid a long but
    legitimate inference call shorter than the configured threshold) must
    never be reconciled."""
    headers = await _register_and_login(client, "reconcile-owner2@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    job_id = await _make_job(
        client,
        headers,
        project_id,
        dataset["id"],
        status=AnalysisJobStatus.RUNNING,
        updated_at=_fresh_timestamp(),
    )

    async with AsyncSessionLocal() as db:
        reconciled = await reconcile_stale_running_jobs(db)

    assert str(job_id) not in reconciled

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.RUNNING
        assert final.error_message is None


async def test_completed_job_is_never_reaped_even_if_old(client):
    """A genuinely completed job must never be incorrectly reconciled as
    stale, regardless of how old its updated_at is -- the sweep only ever
    matches status='running'."""
    headers = await _register_and_login(client, "reconcile-owner3@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    job_id = await _make_job(
        client,
        headers,
        project_id,
        dataset["id"],
        status=AnalysisJobStatus.COMPLETED,
        updated_at=_stale_timestamp(),
    )

    async with AsyncSessionLocal() as db:
        reconciled = await reconcile_stale_running_jobs(db)

    assert str(job_id) not in reconciled

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.COMPLETED
        assert final.error_message is None


async def test_failed_job_is_never_reaped(client):
    headers = await _register_and_login(client, "reconcile-owner4@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    job_id = await _make_job(
        client,
        headers,
        project_id,
        dataset["id"],
        status=AnalysisJobStatus.FAILED,
        updated_at=_stale_timestamp(),
    )

    async with AsyncSessionLocal() as db:
        reconciled = await reconcile_stale_running_jobs(db)

    assert str(job_id) not in reconciled

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.FAILED
        assert final.error_message == "a real prior failure"  # untouched, not overwritten


async def test_cancelled_job_is_never_reaped(client):
    headers = await _register_and_login(client, "reconcile-owner5@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    job_id = await _make_job(
        client,
        headers,
        project_id,
        dataset["id"],
        status=AnalysisJobStatus.CANCELLED,
        updated_at=_stale_timestamp(),
    )

    async with AsyncSessionLocal() as db:
        reconciled = await reconcile_stale_running_jobs(db)

    assert str(job_id) not in reconciled

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.CANCELLED


async def test_queued_job_is_never_reaped(client):
    headers = await _register_and_login(client, "reconcile-owner6@example.com")
    project_id = await _create_project(client, headers)
    dataset = await _upload_dataset(client, headers, project_id)
    job_id = await _make_job(
        client,
        headers,
        project_id,
        dataset["id"],
        status=AnalysisJobStatus.QUEUED,
        updated_at=_stale_timestamp(),
    )

    async with AsyncSessionLocal() as db:
        reconciled = await reconcile_stale_running_jobs(db)

    assert str(job_id) not in reconciled

    async with AsyncSessionLocal() as db:
        final = await db.get(AnalysisJob, job_id)
        assert final.status == AnalysisJobStatus.QUEUED


async def test_reconciliation_sweep_handles_a_mix_of_jobs_correctly_in_one_pass(client):
    """A single realistic sweep pass over several jobs in different states
    at once must reconcile only the one that is genuinely stale-running."""
    headers = await _register_and_login(client, "reconcile-owner7@example.com")
    project_id = await _create_project(client, headers)

    stale_dataset = await _upload_dataset(client, headers, project_id)
    active_dataset = await _upload_dataset(client, headers, project_id)
    done_dataset = await _upload_dataset(client, headers, project_id)

    stale_job_id = await _make_job(
        client,
        headers,
        project_id,
        stale_dataset["id"],
        status=AnalysisJobStatus.RUNNING,
        updated_at=_stale_timestamp(),
    )
    active_job_id = await _make_job(
        client,
        headers,
        project_id,
        active_dataset["id"],
        status=AnalysisJobStatus.RUNNING,
        updated_at=_fresh_timestamp(),
    )
    done_job_id = await _make_job(
        client,
        headers,
        project_id,
        done_dataset["id"],
        status=AnalysisJobStatus.COMPLETED,
        updated_at=_stale_timestamp(),
    )

    async with AsyncSessionLocal() as db:
        reconciled = await reconcile_stale_running_jobs(db)

    assert reconciled == [str(stale_job_id)]

    async with AsyncSessionLocal() as db:
        assert (await db.get(AnalysisJob, active_job_id)).status == AnalysisJobStatus.RUNNING
        assert (await db.get(AnalysisJob, done_job_id)).status == AnalysisJobStatus.COMPLETED
        assert (await db.get(AnalysisJob, stale_job_id)).status == AnalysisJobStatus.FAILED
