# TERRAIN-X Deployment Guide

## Deployed / Current State

TERRAIN-X is deployed with the frontend and backend workloads separated by their runtime requirements.

| Component | Platform | Current endpoint or role |
|---|---|---|
| React/Vite frontend | Vercel | <https://terrain-x-lilac.vercel.app> |
| FastAPI backend | Railway | <https://backend-production-0b0f5.up.railway.app> |
| RQ worker | Railway | Runs queued analysis and report jobs as a separate service |
| PostgreSQL/PostGIS | Railway | Primary relational and geospatial database |
| Redis | Railway | RQ queue and job state backend |
| Persistent volume | Railway | Stores uploaded data, generated artifacts, and model cache files |

The deployed request and job flow is:

```text
Vercel frontend
    |
    | HTTPS /api/v1
    v
Railway FastAPI backend ---- PostgreSQL/PostGIS
    |
    +---- Redis queue ---- Railway RQ worker
                              |
                              v
                     Railway persistent volume
```

The database schema must be at Alembic migration head `c3a9d5e7f1b2`.

## Required Configuration

### Vercel Frontend

Configure the Vercel project with these settings:

| Setting | Value |
|---|---|
| Root Directory | `frontend` |
| Framework | Vite |
| Build Command | `npm run build` |
| Output Directory | `dist` |
| `VITE_API_BASE_URL` | `https://backend-production-0b0f5.up.railway.app/api/v1` |

The repository-level `vercel.json` provides the SPA rewrite to `index.html` so client-side routes load correctly.

### Railway Services

The Railway project requires the following services:

- A FastAPI backend service built from `backend/Dockerfile`. It must listen on Railway's `$PORT`.
- A separate RQ worker service connected to the same application configuration, Redis instance, database, and persistent storage.
- PostgreSQL with the PostGIS extension enabled.
- Redis for RQ queues and job state.
- A persistent volume mounted where the backend and worker can access uploaded datasets, generated outputs, and model caches.

Run database migrations against the production database as part of a controlled release:

```bash
python -m alembic upgrade head
```

Confirm that the resulting Alembic revision is `c3a9d5e7f1b2`.

### Production Environment Variables

Set production values in the Vercel and Railway dashboards. Do not commit real credentials or secret values.

```dotenv
# Backend runtime
ENVIRONMENT=production
LOG_LEVEL=INFO

# Database placeholders
DATABASE_URL=<railway-postgresql-async-url>
DATABASE_URL_SYNC=<railway-postgresql-sync-url>

# Redis placeholder
REDIS_URL=<railway-redis-url>

# Authentication placeholder
JWT_SECRET_KEY=<long-random-production-secret>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Browser access
CORS_ORIGINS=https://terrain-x-lilac.vercel.app

# Persistent storage
STORAGE_ROOT=<persistent-volume-data-path>
HF_HOME=<persistent-volume-model-cache-path>
MAX_UPLOAD_SIZE_MB=500

# Job lifecycle
ANALYSIS_JOB_TIMEOUT_SECONDS=900
REPORT_GENERATION_TIMEOUT_SECONDS=900
STALE_JOB_AFTER_SECONDS=1200
STALE_REPORT_AFTER_SECONDS=1200
STALE_JOB_SWEEP_INTERVAL_SECONDS=60
```

The backend and worker must use matching database, Redis, storage, and application settings. Credentials remain platform-managed secrets.

## Local Development

Create a local `.env` from `.env.example` and replace placeholders with local-only values. Never reuse production credentials locally or commit the resulting `.env` file.

Start the local stack from the repository root:

```bash
docker compose -f docker/docker-compose.yml up --build
```

Typical local endpoints are:

- Frontend: `http://localhost:5173`
- Backend: `http://localhost:8000`
- API documentation: `http://localhost:8000/docs`
- Health endpoint: `http://localhost:8000/api/v1/health`

Run the frontend build and tests from `frontend`:

```bash
npm install
npm run build
npm test
```

Run backend tests through the local Docker stack:

```bash
docker compose -f docker/docker-compose.yml exec backend python -m pytest -v
```

## Verification / Smoke Tests

1. Open <https://terrain-x-lilac.vercel.app> and confirm the SPA loads without a routing error.
2. Check the production backend health endpoint: <https://backend-production-0b0f5.up.railway.app/api/v1/health>.
3. Confirm the backend reports a healthy database and Redis connection.
4. Confirm the production database is at migration head `c3a9d5e7f1b2`.
5. Sign in through the frontend and verify authenticated API requests reach the Railway backend.
6. Create a project and upload a small test dataset.
7. Start an analysis job and verify the separate RQ worker consumes it successfully.
8. Confirm generated artifacts remain available from persistent storage after the job completes.
9. Verify terrain visualization and report generation complete successfully.

Do not include passwords, JWT secrets, database credentials, Redis credentials, or other production secrets in smoke-test notes or logs.
