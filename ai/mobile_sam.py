"""MobileSAM class-agnostic region segmentation.

Model: MobileSAM (ViT-T / TinyViT distillation of Meta's Segment Anything)
  - Source: https://github.com/ChaoningZhang/MobileSAM
  - Pinned commit: f706ad9c4eb7f219c00d9050e46328518ffb65d2 (verified via the
    GitHub API — signed, real commit — before adoption; the `mobile_sam`
    Python package is installed from this exact commit in `backend/Dockerfile`,
    never a moving branch/tag reference)
  - License: Apache-2.0 (verified against the LICENSE file at the pinned
    commit — the same file Meta's original Segment Anything repository
    ships, which MobileSAM's own README points to)
  - Checkpoint: `weights/mobile_sam.pt` at the pinned commit, 40,728,226
    bytes, git blob SHA `7ef2d090979fd9853adf17b7a99c8b94a1c5a6a7` — verified
    by this module at download time (see `_verify_checkpoint`), never
    trusted blindly.
  - Task: **class-agnostic** automatic mask generation — MobileSAM (like the
    original SAM) is a promptable segmentation *foundation* model, not a
    supervised land-cover/object classifier. It was trained to find visually
    coherent regions in natural images; it has no notion of "building",
    "road", "vegetation", or any other semantic class, and this
    implementation never invents one. Every detected region is reported
    only as an opaque `region_id` — see `ai/semantic_estimator.py`.
  - Known limitations: trained predominantly on natural/ground-level
    photographic scenes (SA-1B), not nadir remote-sensing imagery — the same
    domain-mismatch caveat already documented for Depth Anything V2 in
    `ai/depth_anything.py` applies here, arguably more so, since this model
    makes no attempt at semantic understanding at all, only visual region
    boundaries. Region boundaries are not validated against any real-world
    ground truth (property lines, building footprints, ...) in this project.

Weights are downloaded on first use from the pinned commit's raw GitHub URL
(not baked into the Docker image) and cached under a persistent directory
alongside the existing Hugging Face model cache — see `_cache_dir()` — so
subsequent loads, including after a container restart, are local. This
requires outbound network access the first time only; if that access is
unavailable (or the downloaded bytes don't match the pinned checkpoint's
known-good hash), `load()` raises `ModelLoadError` with a clear message
rather than silently proceeding with a wrong or partial checkpoint.
"""

import hashlib
import logging
import os
import tempfile
import time
import urllib.request
from pathlib import Path

import numpy as np

from ai.device import select_device
from ai.exceptions import InferenceError, ModelLoadError, UnsupportedInputError
from ai.semantic_estimator import (
    RegionInfo,
    SemanticEstimator,
    SemanticModelInfo,
    SemanticPrediction,
)

logger = logging.getLogger("terrainx.ai.mobile_sam")

MODEL_NAME = "MobileSAM (ViT-T)"
MODEL_TYPE = "vit_t"  # the sam_model_registry key for MobileSAM's TinyViT encoder
MODEL_REPOSITORY = "https://github.com/ChaoningZhang/MobileSAM"
MODEL_REVISION = "f706ad9c4eb7f219c00d9050e46328518ffb65d2"  # pinned commit — never "master"
MODEL_LICENSE = "Apache-2.0"
MODEL_TASK = "class-agnostic region segmentation (not a semantic land-cover/object classifier)"

CHECKPOINT_FILENAME = "mobile_sam.pt"
CHECKPOINT_URL = (
    f"https://raw.githubusercontent.com/ChaoningZhang/MobileSAM/{MODEL_REVISION}/weights/"
    f"{CHECKPOINT_FILENAME}"
)
# The pinned commit's real git blob SHA for weights/mobile_sam.pt (confirmed
# via `GET /repos/ChaoningZhang/MobileSAM/contents/weights?ref=<MODEL_REVISION>`
# on the GitHub API before this implementation was written) — a git blob SHA
# is `sha1("blob " + <byte length> + "\0" + <content>)`, not a plain sha1 of
# the file, which is why `_git_blob_sha1` below computes it that way.
CHECKPOINT_BLOB_SHA1 = "7ef2d090979fd9853adf17b7a99c8b94a1c5a6a7"
CHECKPOINT_EXPECTED_SIZE_BYTES = 40_728_226

# Automatic mask generation runs the encoder once, then decodes a grid of
# `points_per_side^2` point prompts. The library's own default is 32 (1024
# prompts) — tuned for GPU throughput. This project is CPU-first with no
# NVIDIA GPU available (see docs/ARCHITECTURE.md), so this is deliberately
# reduced to keep per-image inference time reasonable; documented explicitly
# as a real speed/region-recall tradeoff, not a hidden default.
POINTS_PER_SIDE = 16


def _cache_dir() -> Path:
    """Reuses the same persistent bind-mounted directory Depth Anything V2's
    weights are cached under (`HF_HOME`, set in docker-compose.yml) — a
    sibling subdirectory, not inside `transformers`'/`huggingface_hub`'s own
    cache layout, since this checkpoint isn't fetched through that library.
    Falls back to a temp directory only if `HF_HOME` isn't set (e.g. running
    this module directly outside the configured containers).
    """
    root = Path(os.environ.get("HF_HOME", tempfile.gettempdir()))
    return root / "mobile_sam"


def _git_blob_sha1(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode()
    return hashlib.sha1(header + content).hexdigest()


def _verify_checkpoint(path: Path) -> bool:
    if not path.is_file():
        return False
    content = path.read_bytes()
    if len(content) != CHECKPOINT_EXPECTED_SIZE_BYTES:
        return False
    return _git_blob_sha1(content) == CHECKPOINT_BLOB_SHA1


def _ensure_checkpoint() -> Path:
    """Returns a local path to a verified `mobile_sam.pt`, downloading it
    from the pinned commit's raw GitHub URL if not already cached. Never
    downloads a "latest" reference, and never trusts a downloaded (or
    pre-existing) file without checking it against the pinned checkpoint's
    real, known-good git blob hash — a corrupted or unexpectedly-different
    file is a real, reported failure, not silently accepted.
    """
    cache_dir = _cache_dir()
    checkpoint_path = cache_dir / CHECKPOINT_FILENAME

    if _verify_checkpoint(checkpoint_path):
        return checkpoint_path

    cache_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Downloading MobileSAM checkpoint from pinned commit %s (%s)",
        MODEL_REVISION,
        CHECKPOINT_URL,
    )
    try:
        with urllib.request.urlopen(CHECKPOINT_URL, timeout=120) as response:
            downloaded = response.read()
    except Exception as exc:
        raise ModelLoadError(
            f"Failed to download MobileSAM checkpoint from the pinned commit "
            f"({MODEL_REVISION}): {exc}. This requires outbound network access on "
            f"first use; no cached checkpoint was found at {checkpoint_path}."
        ) from exc

    if len(downloaded) != CHECKPOINT_EXPECTED_SIZE_BYTES or (
        _git_blob_sha1(downloaded) != CHECKPOINT_BLOB_SHA1
    ):
        raise ModelLoadError(
            f"Downloaded MobileSAM checkpoint does not match the pinned commit's known-good "
            f"checksum (expected {CHECKPOINT_EXPECTED_SIZE_BYTES} bytes, blob sha1 "
            f"{CHECKPOINT_BLOB_SHA1}; got {len(downloaded)} bytes). Refusing to use a "
            f"checkpoint that doesn't match the pinned, verified revision."
        )

    # Write to a temp file in the same directory, then atomically rename —
    # a failed/partial download must never leave a corrupt file that a later
    # `_verify_checkpoint` call would (correctly) reject anyway, but this
    # also avoids a half-written file being briefly visible to a concurrent
    # reader.
    fd, tmp_name = tempfile.mkstemp(dir=cache_dir)
    try:
        with os.fdopen(fd, "wb") as tmp_file:
            tmp_file.write(downloaded)
        os.replace(tmp_name, checkpoint_path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)

    return checkpoint_path


class MobileSAMEstimator(SemanticEstimator):
    def __init__(self) -> None:
        self._mask_generator = None
        self._device = select_device()

    def load(self) -> None:
        if self._mask_generator is not None:
            return  # already loaded in this worker process — cheap no-op

        checkpoint_path = _ensure_checkpoint()

        try:
            from mobile_sam import SamAutomaticMaskGenerator, sam_model_registry

            logger.info(
                "Loading MobileSAM (%s, commit %s) from %s",
                MODEL_TYPE,
                MODEL_REVISION,
                checkpoint_path,
            )
            sam = sam_model_registry[MODEL_TYPE](checkpoint=str(checkpoint_path))
            sam.to(device=self._device)
            sam.eval()
            mask_generator = SamAutomaticMaskGenerator(sam, points_per_side=POINTS_PER_SIDE)
        except ModelLoadError:
            raise
        except Exception as exc:
            raise ModelLoadError(f"Failed to load MobileSAM: {exc}") from exc

        self._mask_generator = mask_generator

    def predict(self, rgb_image: np.ndarray, *, min_region_area_px: int = 0) -> SemanticPrediction:
        """Deterministic rasterization policy (documented per the Phase 6
        requirement that overlap handling be explicit, not arbitrary):

        1. Run MobileSAM's automatic mask generator once — a deterministic,
           fixed grid of `POINTS_PER_SIDE^2` point prompts (no randomness),
           real forward passes only, under `torch.inference_mode()`.
        2. Drop any mask whose real pixel area is below `min_region_area_px`
           (a real, documented filter — never a fabricated region).
        3. Sort the remaining masks by real pixel area, LARGEST first. Ties
           (equal area) keep the model's own original output order (a
           stable sort), so results are fully deterministic for a fixed
           input and fixed weights.
        4. Assign region IDs 1..N in that sorted order (ID 1 = the largest
           surviving region), then paint each mask's True pixels into the
           label map *in that same order* — meaning a **smaller** region
           painted later overwrites a **larger** one's pixels wherever they
           overlap. This is a deliberate choice: it keeps visually smaller,
           more specific regions visible in the output raster instead of
           letting them disappear underneath a large enclosing mask.
        5. `pixel_area` reported per region reflects the ACTUAL final
           rasterized area after overlap resolution (not each mask's raw,
           pre-overlap area) — an honest count of what ended up in the
           output raster. `bbox_*` still describes the original mask's own
           extent, which may exceed the final visible area where a smaller
           region was painted on top of part of it.
        """
        if self._mask_generator is None:
            raise InferenceError("predict() called before load()")
        if rgb_image.ndim != 3 or rgb_image.shape[2] != 3:
            raise UnsupportedInputError(
                f"Expected an (H, W, 3) RGB array, got shape {rgb_image.shape}"
            )
        if rgb_image.dtype != np.uint8:
            raise UnsupportedInputError(f"Expected a uint8 RGB array, got dtype {rgb_image.dtype}")

        height, width = rgb_image.shape[0], rgb_image.shape[1]

        import torch

        try:
            start = time.monotonic()
            with torch.inference_mode():
                masks = self._mask_generator.generate(rgb_image)
            inference_seconds = time.monotonic() - start
        except (UnsupportedInputError, InferenceError):
            raise
        except Exception as exc:
            raise InferenceError(f"MobileSAM inference failed: {exc}") from exc

        surviving = [m for m in masks if int(m["segmentation"].sum()) >= max(0, min_region_area_px)]
        surviving.sort(key=lambda m: int(m["area"]), reverse=True)

        label_map = np.zeros((height, width), dtype=np.uint32)
        regions: list[RegionInfo] = []
        for index, mask in enumerate(surviving):
            region_id = index + 1
            segmentation = mask["segmentation"]
            label_map[segmentation] = region_id

            x, y, w, h = mask["bbox"]  # SAM's own convention: [x, y, width, height]
            regions.append(
                RegionInfo(
                    region_id=region_id,
                    pixel_area=0,  # filled in below, after all overlaps are resolved
                    bbox_row_min=int(y),
                    bbox_col_min=int(x),
                    bbox_row_max=int(y + h),
                    bbox_col_max=int(x + w),
                    mask_score=(
                        float(mask["stability_score"]) if "stability_score" in mask else None
                    ),
                    predicted_iou=(
                        float(mask["predicted_iou"]) if "predicted_iou" in mask else None
                    ),
                )
            )

        # Real final pixel counts, computed once after every mask has been
        # painted (see step 5 of the policy above) — never the raw
        # pre-overlap mask area.
        final_regions = []
        for region in regions:
            real_area = int((label_map == region.region_id).sum())
            final_regions.append(
                RegionInfo(
                    region_id=region.region_id,
                    pixel_area=real_area,
                    bbox_row_min=region.bbox_row_min,
                    bbox_col_min=region.bbox_col_min,
                    bbox_row_max=region.bbox_row_max,
                    bbox_col_max=region.bbox_col_max,
                    mask_score=region.mask_score,
                    predicted_iou=region.predicted_iou,
                )
            )

        return SemanticPrediction(
            label_map=label_map,
            regions=final_regions,
            inference_seconds=inference_seconds,
            input_width=width,
            input_height=height,
        )

    def info(self) -> SemanticModelInfo:
        return SemanticModelInfo(
            name=MODEL_NAME,
            task=MODEL_TASK,
            repository=MODEL_REPOSITORY,
            revision=MODEL_REVISION,
            checkpoint=CHECKPOINT_FILENAME,
            license=MODEL_LICENSE,
            device=self._device,
        )
