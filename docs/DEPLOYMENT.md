# TERRAIN-X Deployment Guide

## Deployment Verdict

**B. FRONTEND ON VERCEL + BACKEND/WORKERS ELSEWHERE**

The React/Vite frontend deploys cleanly to Vercel. The FastAPI backend **cannot** run on Vercel due to fundamental architectural incompatibilities (RQ workers, Redis, PostgreSQL/PostGIS, local filesystem storage, PyTorch/rasterio/GDAL dependencies, long-running AI jobs, and background reconciliation loops).

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         PRODUCTION                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐         ┌──────────────────────────────────┐  │
│  │   Vercel     │         │   Container Platform             │  │
│  │              │  HTTPS  │   (Railway / Render / Fly.io /   │  │
│  │   React/Vite │ ──────► │    AWS ECS / GCP Cloud Run)     │  │
│  │   Frontend   │         │                                  │  │
│  │              │         │  ┌────────────┐  ┌────────────┐ │  │
│  │   - Three.js │         │  │  FastAPI   │  │  RQ Worker │ │  │
│  │   - Leaflet  │         │  │  Backend   │  │  (separate)│ │  │
│  │   - Recharts │         │  │            │  │            │ │  │
│  │   - Auth UI  │         │  │  - API     │  │  - Depth   │ │  │
│  │              │         │  │  - CRUD    │  │  - Calib.  │ │  │
│  │              │         │  │  - Viz     │  │  - DTM     │ │  │
│  │              │         │  │  - Reports │  │  - SAM     │ │  │
│  │              │         │  │  - Auth    │  │  - Hazard  │ │  │
│  └──────────────┘         │  └────────────┘  └────────────┘ │  │
│                           │                                  │  │
│                           │  ┌────────────┐  ┌────────────┐ │  │
│                           │  │ PostgreSQL │  │   Redis    │ │  │
│                           │  │  + PostGIS │  │   (RQ)     │ │  │
│                           │  │  (managed) │  │  (managed) │ │  │
│                           │  └────────────┘  └────────────┘ │  │
│                           │                                  │  │
│                           │  ┌────────────────────────────┐ │  │
│                           │  │  Object Storage (S3)       │ │  │
│                           │  │  - GeoTIFF/DEM/DSM files   │ │  │
│                           │  │  - Reports (PDF/CSV/ZIP)   │ │  │
│                           │  │  - Model cache             │ │  │
│                           │  └────────────────────────────┘ │  │
│                           └──────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Why the Backend Cannot Run on Vercel

| Feature | Vercel Compatibility | Reason |
|---------|---------------------|--------|
| RQ Workers | **INCOMPATIBLE** | Long-running background processes; Vercel functions are request-scoped and ephemeral |
| Redis | **INCOMPATIBLE** | Required for RQ job queue; Vercel has no managed Redis |
| PostgreSQL/PostGIS | **INCOMPATIBLE** | Required database with PostGIS extension; Vercel has no managed Postgres |
| Local Filesystem Storage | **INCOMPATIBLE** | `LocalStorageBackend` assumes persistent local disk; Vercel has ephemeral filesystem |
| PyTorch/torchvision | **INCOMPATIBLE** | ~2GB+ ML model dependencies; Vercel has 250MB function size limit |
| rasterio/GDAL | **INCOMPATIBLE** | Native geospatial libraries; not available in Vercel's Python runtime |
| Long-running AI Jobs | **INCOMPATIBLE** | Analysis jobs take 170+ seconds; Vercel functions timeout at 10-30s |
| Background Reconciliation | **INCOMPATIBLE** | `_stale_job_sweep_loop` runs indefinitely; not possible in serverless |
| Alembic Migrations | **INCOMPATIBLE** | Need to run at startup; Vercel has no startup hook |

---

## Vercel Configuration (Frontend)

### Settings

| Setting | Value |
|---------|-------|
| **Root Directory** | `frontend` |
| **Build Command** | `npm run build` |
| **Output Directory** | `dist` |
| **Install Command** | `npm install` |
| **Framework** | Vite (auto-detected) |

### vercel.json

```json
{
  "buildCommand": "npm run build",
  "outputDirectory": "dist",
  "framework": "vite",
  "rewrites": [
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

### Environment Variables (Vercel Dashboard)

| Variable | Required | Description |
|----------|----------|-------------|
| `VITE_API_BASE_URL` | **YES** | Backend API URL, e.g. `https://api.terrainx.app/api/v1` |
| `VITE_ANALYSIS_POLL_INTERVAL_MS` | No | Polling interval for job status (default: `3000`) |
| `VITE_MAP_TILE_URL` | No | Map tile URL (default: OpenStreetMap) |
| `VITE_MAP_TILE_ATTRIBUTION` | No | Map tile attribution |

### SPA Routing

The `vercel.json` rewrites configuration ensures all routes serve `index.html`, enabling client-side React Router to handle routing after page refresh.

---

## Backend Deployment (Container Platform)

### Recommended Platforms

| Platform | Best For | Notes |
|----------|----------|-------|
| **Railway** | Easiest setup | Native Docker support, managed Postgres/Redis |
| **Render** | Good balance | Docker support, managed Postgres, Redis add-on |
| **Fly.io** | Performance | Docker support, good for CPU-intensive workloads |
| **AWS ECS** | Production scale | Full control, higher complexity |
| **GCP Cloud Run** | Serverless containers | Good for API, but worker needs separate service |

### Required External Services

1. **PostgreSQL/PostGIS** (managed)
   - Railway: Built-in Postgres with PostGIS extension
   - Render: Managed Postgres with PostGIS
   - AWS RDS: Postgres with PostGIS extension
   - Supabase: Postgres with PostGIS (free tier available)

2. **Redis** (managed)
   - Railway: Built-in Redis
   - Render: Redis add-on
   - Upstash: Serverless Redis (free tier available)
   - Redis Cloud: Managed Redis

3. **Object Storage** (S3-compatible)
   - AWS S3
   - Cloudflare R2 (free egress)
   - Backblaze B2
   - DigitalOcean Spaces

### Backend Environment Variables

```bash
# --- Environment ---
ENVIRONMENT=production
LOG_LEVEL=INFO

# --- Database ---
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/terrainx
DATABASE_URL_SYNC=postgresql+psycopg://user:password@host:5432/terrainx

# --- Redis ---
REDIS_URL=redis://default:password@host:6379/0

# --- Security ---
JWT_SECRET_KEY=<generate-a-long-random-string>
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# --- CORS ---
CORS_ORIGINS=https://terrainx.vercel.app

# --- Storage ---
STORAGE_ROOT=/app/storage/data
MAX_UPLOAD_SIZE_MB=500

# --- Model Cache ---
HF_HOME=/app/storage/model_cache

# --- Timeouts ---
ANALYSIS_JOB_TIMEOUT_SECONDS=900
REPORT_GENERATION_TIMEOUT_SECONDS=900
STALE_JOB_AFTER_SECONDS=1200
STALE_REPORT_AFTER_SECONDS=1200
STALE_JOB_SWEEP_INTERVAL_SECONDS=60

# --- Processing Limits ---
MAX_DEPTH_INPUT_DIMENSION_PX=4096
MAX_SEMANTIC_INPUT_DIMENSION_PX=2048
PREVIEW_MAX_DIMENSION=1024
MAX_TERRAIN_DIMENSION=256
```

### Docker Deployment

The existing `docker/docker-compose.yml` can be used with any Docker-compatible platform:

```bash
# Build and push images
docker build -t terrainx-backend ./backend
docker build -t terrainx-worker ./backend

# Or use docker compose with a container platform
docker compose -f docker/docker-compose.yml up -d
```

### Worker Deployment

The RQ worker runs as a separate container/process:

```bash
# Using Docker
docker run -e REDIS_URL=... -e DATABASE_URL=... terrainx-worker python worker.py

# Or via docker compose (already configured)
docker compose -f docker/docker-compose.yml up worker
```

### Migration Strategy

The backend uses Alembic migrations. The `docker-entrypoint.sh` script runs migrations when `RUN_MIGRATIONS=true`:

```bash
# Run migrations manually (recommended for production)
docker run --rm -e DATABASE_URL_SYNC=... terrainx-backend alembic upgrade head

# Or set RUN_MIGRATIONS=true in the backend service environment
```

---

## Storage Architecture

### Current Implementation

The backend uses `LocalStorageBackend` which stores files on a local filesystem:

```
storage/data/projects/<project_uuid>/datasets/<dataset_uuid>/original.<ext>
storage/data/projects/<project_uuid>/analysis/<job_id>/depth.tif
storage/data/projects/<project_uuid>/analysis/<job_id>/dsm.tif
storage/data/projects/<project_uuid>/analysis/<job_id>/metric_elevation.tif
storage/data/projects/<project_uuid>/analysis/<job_id>/dtm.tif
storage/data/projects/<project_uuid>/analysis/<job_id>/ndsm.tif
storage/data/projects/<project_uuid>/analysis/<job_id>/semantic.tif
storage/data/projects/<project_uuid>/analysis/<job_id>/slope.tif
storage/data/projects/<project_uuid>/analysis/<job_id>/aspect.tif
storage/data/projects/<project_uuid>/analysis/<job_id>/flood_screening.tif
storage/data/projects/<project_uuid>/analysis/<job_id>/landslide_screening.tif
storage/data/projects/<project_uuid>/reports/<report_id>/report.pdf
storage/data/projects/<project_uuid>/reports/<report_id>/report.csv
storage/data/projects/<project_uuid>/reports/<report_id>/bundle.zip
storage/model_cache/  # Hugging Face model weights
```

### Production Storage Options

| Option | Pros | Cons |
|--------|------|------|
| **Local filesystem (current)** | Simple, no extra services | Not scalable, single point of failure |
| **S3-compatible object storage** | Scalable, durable, cheap | Requires code changes to `StorageBackend` |
| **NFS/Network volume** | Drop-in replacement | Requires infrastructure management |

### Recommended: S3-Compatible Storage

The `StorageBackend` abstraction already supports adding an S3 implementation:

```python
# storage/backend.py already has the interface
class StorageBackend(ABC):
    @abstractmethod
    def open_writer(self, key: str) -> BinaryIO: ...
    
    @abstractmethod
    def absolute_path(self, key: str) -> Path: ...
    
    @abstractmethod
    def exists(self, key: str) -> bool: ...
    
    @abstractmethod
    def delete(self, key: str) -> None: ...
    
    @abstractmethod
    def delete_prefix(self, prefix: str) -> None: ...
```

An S3 implementation would need to:
1. Implement `open_writer` to return a file-like object that uploads to S3
2. Implement `absolute_path` to return a temporary local path (download from S3)
3. Implement `exists` to check S3
4. Implement `delete` and `delete_prefix` for S3 objects

---

## Security Checklist

- [x] JWT_SECRET_KEY is required and validated (non-development environments)
- [x] CORS origins are configurable
- [x] Database credentials are not hardcoded
- [x] Redis credentials are not hardcoded
- [x] `.env` is gitignored
- [x] `storage/data/` is gitignored
- [x] `storage/model_cache/` is gitignored
- [x] `node_modules/` is gitignored
- [x] No secrets in frontend source code
- [x] API URL is configurable via environment variables
- [x] Password hashing uses argon2
- [x] JWT tokens expire (default: 60 minutes)

---

## Local Verification Commands

### Frontend Build Test

```bash
cd frontend
npm install
npm run build
# Expected: dist/ directory created with index.html and assets/
```

### Frontend Preview

```bash
cd frontend
npm run preview
# Open http://localhost:4173
```

### Backend Local Test (Docker)

```bash
cp .env.example .env
cd docker
docker compose up --build
# Backend: http://localhost:8000
# Frontend: http://localhost:5173
# API Docs: http://localhost:8000/docs
```

### Backend Tests

```bash
docker compose -f docker/docker-compose.yml exec backend python -m pytest -v
```

---

## Deployment Steps (Human Operator)

### Step 1: Deploy Frontend to Vercel

1. Push the repository to GitHub (already done)
2. Go to [vercel.com](https://vercel.com) and import the repository
3. Configure:
   - **Root Directory**: `frontend`
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`
4. Add environment variable: `VITE_API_BASE_URL=https://your-backend-domain.com/api/v1`
5. Deploy

### Step 2: Deploy Backend to Container Platform

1. Choose a platform (Railway recommended for simplicity)
2. Create a new project from the GitHub repository
3. Configure environment variables (see above)
4. Add managed PostgreSQL with PostGIS extension
5. Add managed Redis
6. Deploy the backend service
7. Deploy the worker service (separate service/command)

### Step 3: Configure Object Storage

1. Create an S3 bucket (or use Cloudflare R2)
2. Update `STORAGE_ROOT` to point to the bucket
3. (Optional) Implement S3 storage backend

### Step 4: Verify Deployment

1. Open the Vercel frontend URL
2. Register a new account
3. Create a project
4. Upload a test image
5. Run an analysis job
6. Verify the terrain visualization works
7. Generate a report

---

## Repository Changes Made

| File | Change | Reason |
|------|--------|--------|
| `vercel.json` | Created | SPA routing for Vercel |
| `docs/DEPLOYMENT.md` | Created | This deployment guide |

---

## Files NOT Changed

- `frontend/package.json` - No dependency changes needed
- `frontend/vite.config.ts` - No changes needed
- `frontend/src/api/client.ts` - Already uses `VITE_API_BASE_URL`
- `frontend/Dockerfile` - Not used for Vercel deployment
- `backend/Dockerfile` - No changes needed
- `backend/requirements.txt` - No changes needed
- `backend/app/main.py` - No changes needed
- `backend/app/core/config.py` - No changes needed
- `backend/app/core/storage.py` - No changes needed
- `backend/app/jobs/queue.py` - No changes needed
- `backend/app/jobs/tasks.py` - No changes needed
- `docker/docker-compose.yml` - No changes needed
- `.env.example` - No changes needed
- `.gitignore` - No changes needed
- All scientific/AI algorithms - **NOT TOUCHED**
- All calibration thresholds - **NOT TOUCHED**
- All D1/D2/D3 behavior - **NOT TOUCHED**
- All R1 report lifecycle behavior - **NOT TOUCHED**
- All S1 JWT security behavior - **NOT TOUCHED**

---

## Summary

The TERRAIN-X frontend is ready for Vercel deployment. The backend requires a container platform due to its architectural requirements (RQ workers, Redis, PostgreSQL/PostGIS, local filesystem storage, and AI/ML dependencies). The repository has been prepared with minimal changes: only `vercel.json` and this deployment guide were added.
