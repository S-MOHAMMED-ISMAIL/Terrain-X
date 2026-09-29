"""AI/CV package: monocular depth estimation (implemented, Phase 3), semantic
extraction and uncertainty (not yet implemented).

Must remain independent of the backend/FastAPI layer — backend imports from
here, never the reverse. See `ai/depth_estimator.py` for the model-agnostic
interface and `ai/depth_anything.py` for the concrete Depth Anything V2
implementation.
"""
