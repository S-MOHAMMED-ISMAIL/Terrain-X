"""R1 report lifecycle recovery tests against the real test database/storage."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from rq.timeouts import JobTimeoutException

from app.core.config import get_settings
from app.core.storage import get_storage
from app.db.session import AsyncSessionLocal
from app.jobs import tasks
from app.models.report import Report, ReportStatus
from app.services.report_execution import generate_report, report_storage_prefix
from app.services.report_reconciliation import (
    REPORT_TIMEOUT_ERROR_MESSAGE,
    REPORT_WORKER_FAILURE_ERROR_MESSAGE,
    STALE_REPORT_ERROR_MESSAGE,
    fail_report_job,
    reconcile_stale_reports,
)
from tests.fixtures import make_jpeg_bytes


async def _register_and_login(client, email: str) -> tuple[dict, uuid.UUID]:
    password = "supersecret123"
    await client.post("/api/v1/auth/register", json={"email": email, "password": password})
    login = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    user_id = uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["id"])
    return headers, user_id


async def _create_report(
    client,
    email: str,
    *,
    status: ReportStatus,
    updated_at: datetime,
    error_message: str | None = None,
) -> tuple[dict, uuid.UUID, uuid.UUID]:
    headers, user_id = await _register_and_login(client, email)
    project = await client.post("/api/v1/projects", json={"name": "R1"}, headers=headers)
    project_id = uuid.UUID(project.json()["id"])
    dataset = await client.post(
        f"/api/v1/projects/{project_id}/datasets",
        files={"file": ("scene.jpg", make_jpeg_bytes(), "image/jpeg")},
        headers=headers,
    )
    async with AsyncSessionLocal() as db:
        report = Report(
            project_id=project_id,
            dataset_id=uuid.UUID(dataset.json()["id"]),
            user_id=user_id,
            status=status,
            error_message=error_message,
            completed_at=(
                datetime.now(UTC)
                if status in (ReportStatus.COMPLETED, ReportStatus.FAILED)
                else None
            ),
            updated_at=updated_at,
        )
        db.add(report)
        await db.commit()
        return headers, project_id, report.id


def _stale(now: datetime) -> datetime:
    return now - timedelta(seconds=get_settings().STALE_REPORT_AFTER_SECONDS + 1)


def _fresh(now: datetime) -> datetime:
    return now - timedelta(seconds=get_settings().STALE_REPORT_AFTER_SECONDS - 1)


def test_stale_threshold_is_safely_above_worker_timeout():
    settings = get_settings()
    assert settings.STALE_REPORT_AFTER_SECONDS > settings.REPORT_GENERATION_TIMEOUT_SECONDS


@pytest.mark.parametrize("status", [ReportStatus.PENDING, ReportStatus.GENERATING])
async def test_stale_active_report_is_reconciled_to_failed(client, status):
    now = datetime.now(UTC)
    headers, project_id, report_id = await _create_report(
        client,
        f"r1-stale-{status.value}@example.com",
        status=status,
        updated_at=_stale(now),
    )
    partial_key = f"{report_storage_prefix(project_id, report_id)}/report.pdf"
    with get_storage().open_writer(partial_key) as output:
        output.write(b"partial")

    response = await client.get(
        f"/api/v1/projects/{project_id}/reports/{report_id}", headers=headers
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["error_message"] == STALE_REPORT_ERROR_MESSAGE
    assert not get_storage().absolute_path(partial_key).exists()


async def test_fresh_generating_report_remains_generating_at_timeout_boundary(client):
    now = datetime.now(UTC)
    _, _, report_id = await _create_report(
        client,
        "r1-fresh@example.com",
        status=ReportStatus.GENERATING,
        updated_at=_fresh(now),
    )
    async with AsyncSessionLocal() as db:
        assert await reconcile_stale_reports(db, now=now) == []
        report = await db.get(Report, report_id)
        assert report.status == ReportStatus.GENERATING


@pytest.mark.parametrize("status", [ReportStatus.COMPLETED, ReportStatus.FAILED])
async def test_terminal_report_is_never_reconciled(client, status):
    now = datetime.now(UTC)
    prior_error = "original failure" if status == ReportStatus.FAILED else None
    _, _, report_id = await _create_report(
        client,
        f"r1-terminal-{status.value}@example.com",
        status=status,
        updated_at=_stale(now),
        error_message=prior_error,
    )
    async with AsyncSessionLocal() as db:
        assert await reconcile_stale_reports(db, now=now) == []
        report = await db.get(Report, report_id)
        assert report.status == status
        assert report.error_message == prior_error


async def test_repeated_reconciliation_is_idempotent(client):
    now = datetime.now(UTC)
    _, _, report_id = await _create_report(
        client,
        "r1-idempotent@example.com",
        status=ReportStatus.GENERATING,
        updated_at=_stale(now),
    )
    async with AsyncSessionLocal() as db:
        assert await reconcile_stale_reports(db, now=now) == [str(report_id)]
        assert await reconcile_stale_reports(db, now=now) == []


async def test_concurrent_reconciliation_updates_report_once(client):
    now = datetime.now(UTC)
    _, _, report_id = await _create_report(
        client,
        "r1-concurrent@example.com",
        status=ReportStatus.GENERATING,
        updated_at=_stale(now),
    )
    async with AsyncSessionLocal() as first, AsyncSessionLocal() as second:
        results = await asyncio.gather(
            reconcile_stale_reports(first, now=now),
            reconcile_stale_reports(second, now=now),
        )
    assert sum(result == [str(report_id)] for result in results) == 1
    assert sum(result == [] for result in results) == 1


async def test_failure_transition_cleans_partial_files_and_is_terminal(client):
    now = datetime.now(UTC)
    _, project_id, report_id = await _create_report(
        client,
        "r1-worker-failure@example.com",
        status=ReportStatus.GENERATING,
        updated_at=now,
    )
    partial_key = f"{report_storage_prefix(project_id, report_id)}/bundle.zip"
    with get_storage().open_writer(partial_key) as output:
        output.write(b"partial")

    assert await fail_report_job(report_id, REPORT_WORKER_FAILURE_ERROR_MESSAGE) is True
    assert await fail_report_job(report_id, "later callback") is False
    assert not get_storage().absolute_path(partial_key).exists()

    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        assert report.status == ReportStatus.FAILED
        assert report.error_message == REPORT_WORKER_FAILURE_ERROR_MESSAGE
        assert report.pdf_storage_key is None
        assert report.bundle_storage_key is None


@pytest.mark.parametrize(
    ("exc_type", "expected"),
    [
        (JobTimeoutException, REPORT_TIMEOUT_ERROR_MESSAGE),
        (RuntimeError, REPORT_WORKER_FAILURE_ERROR_MESSAGE),
    ],
)
async def test_rq_failure_callback_classifies_timeout_and_worker_failure(
    monkeypatch, exc_type, expected
):
    captured = {}

    async def fake_fail(report_id, message):
        captured["report_id"] = report_id
        captured["message"] = message

    monkeypatch.setattr(tasks, "fail_report_job", fake_fail)
    report_id = uuid.uuid4()
    job = SimpleNamespace(args=(str(report_id),))
    await asyncio.to_thread(
        tasks.handle_report_generation_failure,
        job,
        None,
        exc_type,
        exc_type("failure"),
        None,
    )
    assert captured == {"report_id": report_id, "message": expected}


async def test_late_worker_delivery_cannot_revive_failed_report(client):
    now = datetime.now(UTC)
    _, _, report_id = await _create_report(
        client,
        "r1-late-delivery@example.com",
        status=ReportStatus.FAILED,
        updated_at=now,
        error_message="already terminal",
    )
    await generate_report(report_id)
    async with AsyncSessionLocal() as db:
        report = await db.get(Report, report_id)
        assert report.status == ReportStatus.FAILED
        assert report.error_message == "already terminal"
