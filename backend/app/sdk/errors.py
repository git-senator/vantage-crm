"""SDK exceptions — the error vocabulary a plugin author sees.

Deliberately independent of the CRM's `AppError` hierarchy: a plugin catches SDK
errors, not internal ones, so the contract does not leak the core's exception
types. The backend bridge maps these onto its own HTTP errors at the boundary.
"""

from __future__ import annotations


class SdkError(Exception):
    """Base class for every SDK error."""


class SdkValidationError(SdkError):
    """A manifest or event payload did not satisfy the SDK contract."""


class SdkCompatibilityError(SdkError):
    """A plugin declared an SDK version this platform cannot run."""


class SdkPermissionError(SdkError):
    """A plugin used a capability it was not granted."""


__all__ = [
    "SdkCompatibilityError",
    "SdkError",
    "SdkPermissionError",
    "SdkValidationError",
]
