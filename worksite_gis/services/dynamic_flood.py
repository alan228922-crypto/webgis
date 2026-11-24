"""WRA layers serve bytes and georeferencing from one frozen raster response."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime
import httpx

from worksite_gis.integrations.wra import WraInundationAdapter


FRESH_SECONDS = 5 * 60
MAX_AGE_SECONDS = 15 * 60
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class RasterBatch:
    region: str
    hours: int
    batch_id: str
    source_time: str
    bounds: list[list[float]]
    fetched_at: datetime
    content: bytes

    def overlay(self, slug):
        query = urlencode({"region": self.region, "forecastHours": self.hours, "batch": self.batch_id})
        return {
            "region": self.region,
            "forecast_hours": self.hours,
            "timestamp": self.source_time,
            "bounds": self.bounds,
            "is_empty": False,
            "batch_id": self.batch_id,
            "image_url": f"/api/projects/{slug}/dynamic-flood/image/?{query}",
        }


@dataclass(frozen=True)
class FloodResult:
    status: str
    fetched_at: datetime | None
    batch: RasterBatch | None = None
    freshness: str | None = None
    reason: str = ""


def enabled():
    return WraInundationAdapter.enabled()


def request_scope(params):
    default_region = settings.WRA_DEFAULT_REGION
    configured = getattr(settings, "WRA_ALLOWED_REGIONS", (default_region,))
    if isinstance(configured, str):
        configured = configured.split(",")
    allowed = {item.strip().lower() for item in configured if item.strip()}
    region = params.get("region", default_region).strip().lower()
    if region not in allowed:
        raise ValueError("unsupported WRA region")
    try:
        hours = min(max(int(params.get("forecastHours", "2")), 0), 6)
    except ValueError:
        raise ValueError("forecastHours must be numeric") from None
    return region, hours


def covers_project(batch, project):
    lat, lon = project.search_center_latitude, project.search_center_longitude
    if lat is None or lon is None:
        return None
    (south, west), (north, east) = batch.bounds
    return south <= lat <= north and west <= lon <= east


def _batch_key(batch_id):
    return f"wra:raster:v1:{batch_id}"


def get_batch(batch_id, region, hours):
    batch = cache.get(_batch_key(batch_id)) if batch_id else None
    if (
        batch is None or (batch.region, batch.hours) != (region, hours)
        or timezone.now() - batch.fetched_at >= timedelta(seconds=MAX_AGE_SECONDS)
    ):
        return None
    return batch


def _freeze_raster(raster, region, hours, fetched_at):
    metadata = raster.metadata
    if not isinstance(metadata, dict):
        return None
    # The request binds the scope when it is not echoed. Refuse a contradictory response.
    if "region" in metadata and str(metadata["region"]).strip().lower() != region:
        return None
    if "forecastHours" in metadata and str(metadata["forecastHours"]).strip() != str(hours):
        return None
    try:
        source_time = metadata["TimeStamp"]
        parsed = parse_datetime(source_time)
        (south, west), (north, east) = WraInundationAdapter._bounds(metadata)
        if parsed is None or timezone.is_naive(parsed):
            return None
    except (KeyError, OverflowError, TypeError, ValueError):
        return None
    if (
        not (-90 <= south < north <= 90 and -180 <= west < east <= 180)
        or raster.content_type.split(";", 1)[0].strip().lower() != "image/png"
        or not raster.content.startswith(PNG_SIGNATURE)
    ):
        return None
    bounds = [[south, west], [north, east]]
    # Time and extent are read from the raster's own header, never from a parallel request.
    identity = json.dumps([region, hours, source_time, bounds], separators=(",", ":")).encode()
    digest = sha256(identity)
    digest.update(raster.content)
    return RasterBatch(region, hours, digest.hexdigest(), source_time, bounds, fetched_at, raster.content)


def _fetch(adapter, region, hours):
    scope_key = f"wra:scope:v1:{region}:{hours}"
    cached = get_batch(cache.get(scope_key), region, hours)
    if cached and timezone.now() - cached.fetched_at < timedelta(seconds=FRESH_SECONDS):
        return FloodResult("ok", cached.fetched_at, cached, "fresh")
    try:
        raster = adapter.fetch_raster(region, hours)
    except httpx.TimeoutException:
        reason = "timeout"
    except httpx.RequestError:
        reason = "unavailable"
    except (httpx.HTTPError, httpx.InvalidURL):
        reason = "error"
    else:
        fetched_at = timezone.now()
        if isinstance(raster.metadata, dict) and raster.metadata.get("IsEmptyRasterMap") is True:
            cache.delete(scope_key)
            return FloodResult("empty", fetched_at)
        batch = _freeze_raster(raster, region, hours, fetched_at)
        if batch is None:
            cache.delete(scope_key)
            return FloodResult("unverified", fetched_at, reason="raster_metadata_unverified")
        cache.set(_batch_key(batch.batch_id), batch, MAX_AGE_SECONDS)
        cache.set(scope_key, batch.batch_id, MAX_AGE_SECONDS)
        return FloodResult("ok", fetched_at, batch, "fresh")
    if cached and timezone.now() - cached.fetched_at < timedelta(seconds=MAX_AGE_SECONDS):
        return FloodResult("stale", cached.fetched_at, cached, "stale", reason)
    return FloodResult(reason, timezone.now(), reason=reason)


def get_forecast(region, hours):
    if not enabled():
        return FloodResult("disabled", None)
    adapter = WraInundationAdapter(timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS)
    try:
        result = _fetch(adapter, region, hours)
        if hours and result.status in ("timeout", "unavailable", "error"):
            fallback = _fetch(adapter, region, 0)
            if fallback.batch:
                return FloodResult("fallback", fallback.fetched_at, fallback.batch, fallback.freshness, result.reason)
        return result
    finally:
        adapter.close()
