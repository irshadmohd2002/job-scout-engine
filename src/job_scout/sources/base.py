"""SourceAdapter Protocol and typed adapter exceptions (architecture.md
section 3). RawJobRecord itself lives in models.py alongside the other
domain models."""

from __future__ import annotations

from typing import Protocol

from job_scout.models import AccessMode, RawJobRecord, SourceSearchParams


class SourceAdapterError(Exception):
    """Base class for typed adapter exceptions. Adapters must never leak raw
    httpx exceptions — they catch them and raise one of these instead."""


class SourceAuthError(SourceAdapterError):
    """Credentials missing or rejected by the source."""


class SourceRateLimitError(SourceAdapterError):
    """Source's published rate limit was hit and retries were exhausted."""


class SourceUnavailableError(SourceAdapterError):
    """Transient failure (timeout, 5xx, connection error) after retries were
    exhausted, or an unexpected non-2xx response."""


class SourceAdapter(Protocol):
    source_id: str
    access_mode: AccessMode

    def is_configured(self) -> bool: ...
    def fetch(self, params: SourceSearchParams) -> list[RawJobRecord]: ...
