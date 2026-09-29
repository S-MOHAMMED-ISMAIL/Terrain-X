FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libexpat1 git \
    && rm -rf /var/lib/apt/lists/*

# Build context is the repository root (see docker/docker-compose.yml) so this
# image can include the independent ai/, geospatial/, storage/ packages
# alongside the backend itself — see docs/ARCHITECTURE.md.

# torch + torchvision installed FIRST, on their own, from the CPU-only wheel
# index. This reference environment has no NVIDIA GPU (AMD integrated
# graphics) and must never require CUDA; installing them here — before
# requirements.txt, and from a dedicated index rather than mixed into
# requirements.txt — guarantees pip can't resolve either to PyPI's default
# (CUDA-enabled, much larger) linux wheel. torchvision==0.20.1 is the release
# paired with torch==2.5.1 (see https://github.com/pytorch/vision#installation);
# it is a hard runtime dependency of MobileSAM's automatic mask generator
# (`mobile_sam.automatic_mask_generator` imports `torchvision.ops.boxes`),
# not something this project would otherwise need — see docs/ARCHITECTURE.md
# §3.6 for the Phase 6 semantic-segmentation model card.
RUN pip install torch==2.5.1 torchvision==0.20.1 --index-url https://download.pytorch.org/whl/cpu

# MobileSAM (Apache-2.0, github.com/ChaoningZhang/MobileSAM) has no PyPI
# release, so it's installed directly from a PINNED commit SHA (never a
# moving branch/tag reference) via pip's git-URL support — `git` above is
# required only for this install step. The commit was chosen and its
# LICENSE/weights verified before adoption; see docs/ARCHITECTURE.md §3.6 for
# the full verification record. Model *weights* (mobile_sam.pt) are
# deliberately NOT downloaded at image-build time — they're fetched lazily
# and cached in the persistent storage bind mount on first real use, exactly
# like Depth Anything V2's weights (see ai/mobile_sam.py).
RUN pip install "git+https://github.com/ChaoningZhang/MobileSAM.git@f706ad9c4eb7f219c00d9050e46328518ffb65d2"

COPY backend/requirements.txt backend/requirements-dev.txt ./
RUN pip install -r requirements-dev.txt

COPY ai ./ai
COPY geospatial ./geospatial
COPY storage ./storage
COPY backend ./

RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

# Invoked via `sh` (not exec bit) because bind-mounting this directory from a
# Windows host can drop the executable permission on the script.
ENTRYPOINT ["sh", "/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
