"""Backend test suite entry point.

CRITICAL SAFETY NOTE — read before touching this file: this suite runs
against a REAL Postgres database and REAL local storage (no mocking), and
`cleanup_db` below unconditionally deletes everything in the tables/prefix
it targets, after every single test. A prior version of this file had no
isolation at all: it pointed at the SAME database and SAME storage
directory the live development application uses, and a real pytest run
permanently deleted real uploaded projects/datasets/analysis artifacts.

Everything below exists to make that impossible again: it forces this
process to connect to an explicitly separate TEST_DATABASE_URL/
TEST_STORAGE_ROOT (never the application's own DATABASE_URL/STORAGE_ROOT),
refuses outright — before a single test can run — if that separation can't
be proven, and only then creates/migrates the isolated test database.
"""

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from app.core.config import get_settings

_settings = get_settings()

_dev_database_url = _settings.DATABASE_URL
_dev_database_url_sync = _settings.DATABASE_URL_SYNC
_dev_storage_root = _settings.STORAGE_ROOT
_dev_redis_url = _settings.REDIS_URL
_test_database_url = _settings.TEST_DATABASE_URL
_test_database_url_sync = _settings.TEST_DATABASE_URL_SYNC
_test_storage_root = _settings.TEST_STORAGE_ROOT
_test_redis_url = _settings.TEST_REDIS_URL

if (
    not _test_database_url
    or not _test_database_url_sync
    or not _test_storage_root
    or not _test_redis_url
):
    raise RuntimeError(
        "Refusing to run tests: TEST_DATABASE_URL / TEST_DATABASE_URL_SYNC / "
        "TEST_STORAGE_ROOT / TEST_REDIS_URL are not all configured. This "
        "suite deletes everything it touches every run, so it must never "
        "fall back to the application's own DATABASE_URL/STORAGE_ROOT/"
        "REDIS_URL. Set all four in .env to resources distinct from the "
        "development ones — see .env.example."
    )
if _test_database_url == _dev_database_url or _test_database_url_sync == _dev_database_url_sync:
    raise RuntimeError(
        "Refusing to run tests: TEST_DATABASE_URL resolves to the exact "
        "same database as the development DATABASE_URL. Tests must run "
        "against a separate database — see .env.example."
    )
if _test_storage_root == _dev_storage_root:
    raise RuntimeError(
        "Refusing to run tests: TEST_STORAGE_ROOT resolves to the exact "
        "same path as the development STORAGE_ROOT. Tests must run "
        "against a separate storage root — see .env.example."
    )
if _test_redis_url == _dev_redis_url:
    # Real analysis/report jobs flow through an RQ queue backed by Redis —
    # sharing REDIS_URL with development would let the real dev worker
    # container and this suite's own worker race on the same queue, each
    # able to steal the other's jobs (observed for real: jobs enqueued by
    # tests were silently picked up by the dev worker, which then logged
    # "job no longer exists" since it can't see the isolated test database).
    raise RuntimeError(
        "Refusing to run tests: TEST_REDIS_URL resolves to the exact same "
        "Redis URL as the development REDIS_URL. Tests must use a "
        "different Redis DB index — see .env.example."
    )
if "test" not in urlsplit(_test_database_url_sync).path.lower():
    # A plain-inequality check above already prevents the exact bug that
    # caused the real data loss, but a database name that doesn't even look
    # like a test resource (e.g. a typo'd copy of the real name) is exactly
    # the kind of mistake this file exists to catch — refuse outright.
    raise RuntimeError(
        "Refusing to run tests: TEST_DATABASE_URL's database name does not "
        "contain 'test' (expected something like '.../terrainx_test'). "
        "Refusing on the assumption this is a misconfiguration, not a "
        "real isolated test database — see .env.example."
    )

# Override the process environment BEFORE importing anything under `app.`
# that might construct a DB engine / storage-backend singleton from
# settings — app.db.session's `engine` and app.core.storage.get_storage()
# are both built once, at import time, from whatever get_settings()
# resolves to at that moment. Every part of the app these tests exercise
# must be wired to the isolated test resources, never the real ones.
os.environ["DATABASE_URL"] = _test_database_url
os.environ["DATABASE_URL_SYNC"] = _test_database_url_sync
os.environ["STORAGE_ROOT"] = _test_storage_root
os.environ["REDIS_URL"] = _test_redis_url
get_settings.cache_clear()

# Re-resolve and assert the override actually took — belt-and-braces
# against a future refactor silently reordering these lines or something
# upstream re-populating os.environ from the original .env afterward.
_settings = get_settings()
if (
    _settings.DATABASE_URL != _test_database_url
    or _settings.DATABASE_URL_SYNC != _test_database_url_sync
    or _settings.STORAGE_ROOT != _test_storage_root
    or _settings.REDIS_URL != _test_redis_url
):
    raise RuntimeError(
        "Test database/storage/redis override did not take effect — "
        "refusing to run tests rather than risk touching development "
        "resources."
    )


def _server_admin_url(database_url_sync: str) -> tuple[str, str]:
    """Splits a SQLAlchemy sync URL into (admin connection URL to the
    server's default `postgres` database, target database name) — psycopg
    needs a plain `postgresql://` URL and a real database to connect to
    before it can check for/create the target database itself."""
    plain = database_url_sync.replace("postgresql+psycopg://", "postgresql://", 1)
    parts = urlsplit(plain)
    db_name = parts.path.lstrip("/")
    admin_url = urlunsplit((parts.scheme, parts.netloc, "/postgres", "", ""))
    return admin_url, db_name


def _ensure_test_database_exists(database_url_sync: str) -> None:
    """Creates the isolated test database on the same Postgres server if it
    doesn't exist yet (a fresh clone/CI run starts with only the real
    development database) — idempotent, safe to call every test session."""
    admin_url, db_name = _server_admin_url(database_url_sync)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
    # Mirrors database/init/001_extensions.sql — a freshly-created database
    # doesn't automatically inherit PostGIS from the postgis/postgis image's
    # own default-database setup, and the app's models depend on it.
    with psycopg.connect(
        database_url_sync.replace("postgresql+psycopg://", "postgresql://", 1)
    ) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        conn.commit()


def _migrate_test_database(database_url_sync: str) -> None:
    """Runs real Alembic migrations against the isolated test database
    in-process, reusing the app's own alembic/env.py (which reads
    DATABASE_URL_SYNC via get_settings(), already overridden above)."""
    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url_sync)
    command.upgrade(cfg, "head")


_ensure_test_database_exists(_test_database_url_sync)
_migrate_test_database(_test_database_url_sync)

# Only safe to import now that DATABASE_URL/STORAGE_ROOT are guaranteed to
# point at the isolated test resources above.
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.core.storage import get_storage  # noqa: E402
from app.db.session import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _test_worker():
    """Several tests exercise the REAL async job pipeline end-to-end: they
    hit the API to enqueue a real RQ job, then poll for it to complete —
    exactly like the real app, which relies on the separate `worker`
    container to actually process it. That real worker container always
    runs with the DEVELOPMENT DATABASE_URL/STORAGE_ROOT (it's a distinct
    OS process/container with its own environment; this test process
    overriding its own os.environ above has no effect on it), so it cannot
    see jobs created against the isolated test database — it would just log
    "job no longer exists" and never advance them, hanging every test that
    polls for completion. Spawning our own worker subprocess HERE, which
    inherits this process's (already test-pointed) environment, keeps the
    same real-async-job-processing behavior these tests rely on while
    staying fully isolated from the real development worker/database.
    """
    backend_root = Path(__file__).resolve().parent.parent
    proc = subprocess.Popen(
        [sys.executable, "worker.py"],
        cwd=str(backend_root),
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def cleanup_db():
    yield
    async with AsyncSessionLocal() as session:
        await session.execute(text("DELETE FROM analysis_artifacts"))
        await session.execute(text("DELETE FROM analysis_jobs"))
        await session.execute(text("DELETE FROM datasets"))
        await session.execute(text("DELETE FROM projects"))
        await session.execute(text("DELETE FROM users"))
        await session.commit()
    # Tests exercise the real storage backend (no mocking); reset it so test
    # runs stay isolated from each other. Safe unconditionally: get_storage()
    # is wired to the isolated TEST_STORAGE_ROOT above, never the
    # application's real STORAGE_ROOT.
    get_storage().delete_prefix("projects")
