"""Accelerator selection. Torch is imported lazily inside the function body
so that merely importing this module (e.g. from the backend API process,
which never actually runs inference) does not pull in torch at all.
"""


def select_device() -> str:
    """Returns 'cuda' only if the runtime genuinely confirms a usable CUDA
    device is present; 'cpu' otherwise. Never assumes GPU availability, and
    never requires CUDA — this codebase's reference environment has no
    NVIDIA GPU (AMD integrated graphics), so CPU is expected to be the
    normal path.
    """
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"
