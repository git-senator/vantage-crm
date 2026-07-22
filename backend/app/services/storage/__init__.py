"""Object storage — the only storage API business logic should touch.

Callers depend on `ObjectStorage`; nothing outside this package imports boto3 or
knows a bucket exists. Adding a provider is a new adapter plus one arm of
`build_object_storage`, with no change at any call site — the same arrangement
`app/services/notifications` uses for email.

The resolved adapter is process-wide and cached. An S3 client owns a connection
pool and rebuilding one per request would negate it, so `get_object_storage()`
is the accessor and `AttachmentService` takes an optional override for tests.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.services.storage.base import (
    ObjectNotFoundError,
    ObjectStorage,
    PresignedUrl,
    StorageError,
    StoredObject,
)
from app.services.storage.memory import InMemoryObjectStorage
from app.services.storage.s3 import S3ObjectStorage

__all__ = [
    "InMemoryObjectStorage",
    "ObjectNotFoundError",
    "ObjectStorage",
    "PresignedUrl",
    "S3ObjectStorage",
    "StorageError",
    "StoredObject",
    "build_object_storage",
    "get_object_storage",
    "reset_object_storage",
]

_storage: ObjectStorage | None = None


def build_object_storage(settings: Settings) -> ObjectStorage:
    match settings.STORAGE_PROVIDER:
        case "s3":
            return S3ObjectStorage(settings)
        case "memory":
            return InMemoryObjectStorage()
        case unknown:  # pragma: no cover — the Literal makes this unreachable
            raise ValueError(f"Unsupported STORAGE_PROVIDER: {unknown}")


def get_object_storage() -> ObjectStorage:
    global _storage
    if _storage is None:
        _storage = build_object_storage(get_settings())
    return _storage


def reset_object_storage() -> None:
    """Drop the cached adapter. Tests use this; nothing else should."""
    global _storage
    _storage = None
