from __future__ import annotations

from dataclasses import dataclass

import httpx
from django.conf import settings
from django.utils import timezone

from .base import AdapterResult


@dataclass(frozen=True)
class WraRaster:
    content: bytes
    content_type: str
    metadata: dict


class WraInundationAdapter:
    source = "wra"
    kind = "dynamic_flood"
    metadata_url = "https://iot.wra.gov.tw/rasterMap/inundation/rasetMapMetaData"
    raster_url = "https://iot.wra.gov.tw/rasterMap/inundation"

    def __init__(self, client=None, timeout: float = 15):
        self.client = client if client is not None else httpx.Client(timeout=timeout, trust_env=False)
        self.owns_client = client is None
        self.metadata_endpoint = getattr(settings, "WRA_INUNDATION_METADATA_URL", self.metadata_url)
        self.raster_endpoint = getattr(settings, "WRA_INUNDATION_RASTER_URL", self.raster_url)

    @staticmethod
    def enabled():
        return not getattr(settings, "WORKSITE_DEMO", False) and getattr(settings, "WRA_ENABLED", True)

    def close(self):
        if self.owns_client:
            self.client.close()

    @staticmethod
    def _bounds(payload):
        return [[float(payload["BRLatitude"]), float(payload["ULLongitude"])], [float(payload["ULLatitude"]), float(payload["BRLongitude"])]]

    def fetch_metadata(self, region: str, forecast_hours: int) -> AdapterResult:
        fetched_at = timezone.now()
        if not self.enabled():
            return AdapterResult("disabled", self.source, fetched_at, [])
        try:
            response = self.client.get(self.metadata_endpoint, params={"region": region, "forecastHours": int(forecast_hours)})
            response.raise_for_status()
            payload = response.json()
            if payload.get("IsEmptyRasterMap") is True:
                return AdapterResult("empty", self.source, fetched_at, [])
            if payload.get("message"):
                return AdapterResult("error", self.source, fetched_at, [], False, "upstream_error")
            record = {
                "bounds": self._bounds(payload),
                "timestamp": payload.get("TimeStamp"),
                "is_empty": bool(payload.get("IsEmptyRasterMap", False)),
                "region": region,
                "forecast_hours": int(forecast_hours),
            }
            return AdapterResult("ok", self.source, fetched_at, [record])
        except httpx.TimeoutException:
            return AdapterResult("timeout", self.source, fetched_at, [], False, "timeout")
        except httpx.RequestError:
            return AdapterResult("unavailable", self.source, fetched_at, [], False, "unavailable")
        except (httpx.HTTPError, httpx.InvalidURL, ValueError, TypeError, KeyError, AttributeError):
            return AdapterResult("error", self.source, fetched_at, [], False, "upstream_error")

    def fetch_raster(self, region: str, forecast_hours: int) -> WraRaster:
        if not self.enabled():
            raise httpx.RequestError("WRA disabled")
        response = self.client.get(self.raster_endpoint, params={"region": region, "forecastHours": int(forecast_hours)})
        response.raise_for_status()
        if response.status_code == 204:
            return WraRaster(b"", "", {"IsEmptyRasterMap": True})
        metadata = response.headers.get("RasterMapMetaData", "{}")
        try:
            import json
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
        return WraRaster(response.content, response.headers.get("Content-Type", ""), metadata)
