from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.utils import timezone

from worksite_gis.models import ApiSnapshot


@dataclass(frozen=True)
class AdapterResult:
    status: str
    source: str
    fetched_at: Any
    records: list[dict]
    stale: bool = False
    error: str = ""
    reason: str | None = None
    retry_after_seconds: int | None = None
    cache: str | None = None

    @property
    def runtime_state(self):
        if self.reason in {"disabled", "not_configured"}:
            return self.reason
        if self.stale:
            return "stale"
        if self.status == "ok":
            return "available" if self.records else "empty"
        if self.reason in {"timeout", "network_failure", "rate_limited", "upstream_unavailable"}:
            return "unavailable"
        return "error"


class SnapshotCache:
    """Persist normalized API results and provide an explicit stale fallback."""

    def __init__(self, ttl_seconds: int = 60):
        self.ttl_seconds = max(int(ttl_seconds), 1)

    def fetch(self, adapter) -> AdapterResult:
        now = timezone.now()
        configuration_error = getattr(adapter, "configuration_error", None)
        if configuration_error:
            return AdapterResult("error", adapter.source, now, [], error="CWA credential missing" if configuration_error == "not_configured" and adapter.source == "cwa" else configuration_error,
                                 reason=configuration_error)
        current = ApiSnapshot.objects.filter(
            service=adapter.source, kind=adapter.kind, expires_at__gt=now, status="ok"
        ).first()
        if current:
            return AdapterResult("ok", adapter.source, current.fetched_at, current.payload, cache="fresh_cache")

        result = adapter.fetch()
        if result.status == "ok":
            ApiSnapshot.objects.create(
                service=adapter.source,
                kind=adapter.kind,
                fetched_at=result.fetched_at,
                expires_at=result.fetched_at + timedelta(seconds=self.ttl_seconds),
                payload=result.records,
                status="ok",
            )
            return result

        max_age = getattr(settings, "TDX_STALE_MAX_SECONDS", 600) if adapter.source == "tdx" else 1800
        fallback = ApiSnapshot.objects.filter(
            service=adapter.source, kind=adapter.kind, status="ok",
            fetched_at__gte=now - timedelta(seconds=max_age), fetched_at__lte=now,
        ).first()
        if fallback:
            return AdapterResult(
                "stale", adapter.source, fallback.fetched_at, fallback.payload, True, result.error,
                result.reason, result.retry_after_seconds, "stale_cache"
            )
        return result
