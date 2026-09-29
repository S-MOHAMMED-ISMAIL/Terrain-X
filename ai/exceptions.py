class ModelLoadError(Exception):
    """Raised when the depth model/processor fails to load (missing network
    access on first download, corrupt cache, incompatible weights, ...)."""


class UnsupportedInputError(Exception):
    """Raised when an input array is not suitable for inference (wrong
    shape/dtype) — a programming-level contract violation by the caller,
    distinct from InferenceError's runtime model failures."""


class InferenceError(Exception):
    """Raised when the model itself fails during a forward pass."""
