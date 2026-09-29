from functools import lru_cache

from app.core.config import get_settings
from storage import LocalStorageBackend, StorageBackend


@lru_cache
def get_storage() -> StorageBackend:
    return LocalStorageBackend(get_settings().STORAGE_ROOT)
