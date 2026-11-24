from __future__ import annotations

import logging
import ssl
from urllib.parse import urlsplit

import httpx
from django.conf import settings
from django.utils import timezone

from .base import AdapterResult


def _redact_cwa_httpx_log(record):
    # HTTPX's INFO request log receives the URL as a format argument.
    if isinstance(record.args, tuple):
        record.args = tuple(
            value.copy_with(query=None)
            if isinstance(value, httpx.URL) and any(key.lower() == "authorization" for key in value.params)
            else value
            for value in record.args
        )
    return True


logging.getLogger("httpx").addFilter(_redact_cwa_httpx_log)


def _is_safe_https(endpoint, *, allow_query=True):
    try:
        url = urlsplit(endpoint)
    except ValueError:
        return False
    return bool(url.scheme == "https" and url.hostname and not url.username and not url.password
                and not url.fragment and (allow_query or not url.query))


def _safe_upstream_error(exc):
    cause = exc
    while cause is not None:
        if isinstance(cause, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(cause):
            return "tls_verification_failed"
        cause = cause.__cause__
    if isinstance(exc, httpx.TimeoutException):
        return "upstream_timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return {
            401: "upstream_unauthorized",
            403: "upstream_forbidden",
            429: "upstream_rate_limited",
        }.get(status, "upstream_unavailable" if status >= 500 else "upstream_http_error")
    if isinstance(exc, httpx.RequestError):
        return "upstream_unavailable"
    return "invalid_upstream_response"


class _CwaHttpAdapter:
    def __init__(self, api_key, client, timeout):
        self.api_key = api_key
        self.client = client
        self.configuration_error = (
            "disabled" if getattr(settings, "WORKSITE_DEMO", False) else "" if api_key else "not_configured"
        )
        if self.client is None and api_key and not getattr(settings, "WORKSITE_DEMO", False):
            ca_bundle = getattr(settings, "CWA_CA_BUNDLE", "")
            try:
                verification = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else True
            except (OSError, ValueError):
                self.configuration_error = "tls_verification_failed"
            else:
                self.client = httpx.Client(timeout=timeout, trust_env=False, verify=verification)

    def _setup_error(self):
        if not self.api_key:
            return "CWA credential missing"
        return self.configuration_error or ("" if _is_safe_https(self.endpoint) else "invalid_upstream_url")


class CwaWeatherAdapter(_CwaHttpAdapter):
    source = "cwa"
    kind = "weather"

    def __init__(self, api_key: str, client=None, timeout: float = 10):
        super().__init__(api_key, client, timeout)
        self.endpoint = getattr(
            settings,
            "CWA_WEATHER_URL",
            "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0003-001",
        )

    @staticmethod
    def _coordinates(station):
        coordinates = station.get("GeoInfo", {}).get("Coordinates", [])
        coordinate = coordinates[-1] if isinstance(coordinates, list) and coordinates else {}
        return coordinate.get("StationLatitude"), coordinate.get("StationLongitude")

    @staticmethod
    def _normalize(station):
        latitude, longitude = CwaWeatherAdapter._coordinates(station)
        weather = station.get("WeatherElement", {})
        precipitation = weather.get("Now", {}).get("Precipitation")
        return {
            "station_id": station.get("StationId") or station.get("StationID"),
            "name": station.get("StationName"),
            "latitude": latitude,
            "longitude": longitude,
            "observed_at": station.get("ObsTime", {}).get("DateTime"),
            "temperature": weather.get("AirTemperature"),
            "humidity": weather.get("RelativeHumidity"),
            "wind_speed": weather.get("WindSpeed"),
            "precipitation": precipitation,
            "source": "CWA",
            "status": "observed",
        }

    def fetch(self) -> AdapterResult:
        fetched_at = timezone.now()
        if getattr(settings, "WORKSITE_DEMO", False):
            return AdapterResult("disabled", self.source, fetched_at, [], reason="disabled")
        if error := self._setup_error():
            return AdapterResult("error", self.source, fetched_at, [], False, error)
        try:
            response = self.client.get(
                self.endpoint,
                params={"Authorization": self.api_key, "format": "JSON"},
            )
            response.raise_for_status()
            stations = response.json().get("records", {}).get("Station", [])
            records = [self._normalize(station) for station in stations]
            records = [item for item in records if item["latitude"] is not None and item["longitude"] is not None]
            return AdapterResult("ok", self.source, fetched_at, records)
        except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError) as exc:
            return AdapterResult("error", self.source, fetched_at, [], False, _safe_upstream_error(exc))


class CwaRadarAdapter:
    source = "cwa"
    kind = "radar"
    default_url = "https://cwaopendata.s3.ap-northeast-1.amazonaws.com/Observation/O-A0058-005.png"
    default_bounds = [[17.75, 115.0], [29.25, 126.5]]

    def __init__(self, api_key: str, client=None, timeout: float = 10):
        self.api_key = api_key
        self.endpoint = getattr(settings, "CWA_RADAR_URL", self.default_url)
        self.configuration_error = (
            "disabled" if getattr(settings, "WORKSITE_DEMO", False) else "" if api_key else "not_configured"
        )

    def fetch(self) -> AdapterResult:
        fetched_at = timezone.now()
        if getattr(settings, "WORKSITE_DEMO", False):
            return AdapterResult("disabled", self.source, fetched_at, [], reason="disabled")
        if not self.api_key:
            return AdapterResult("error", self.source, fetched_at, [], False, "CWA credential missing")
        if not _is_safe_https(self.endpoint, allow_query=False):
            return AdapterResult("error", self.source, fetched_at, [], False, "invalid_upstream_url")
        # CWA publishes this as a latest-image URL. Do not download the large
        # raster on the Django request; the browser fetches it only after the
        # user enables the radar layer.
        return AdapterResult("ok", self.source, fetched_at, [{
            "url": self.endpoint,
            "bounds": self.default_bounds,
            "updated_at": None,
            "image_status": "unverified",
            "content_type": "image/png",
        }])


class CwaAlertAdapter(_CwaHttpAdapter):
    source = "CWA"
    kind = "alerts"

    def __init__(self, api_key: str, client=None, timeout: float = 10):
        super().__init__(api_key, client, timeout)
        self.endpoint = getattr(settings, "CWA_ALERT_URL", "https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0033-001")

    @staticmethod
    def _walk(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from CwaAlertAdapter._walk(child)
        elif isinstance(value, list):
            for child in value:
                yield from CwaAlertAdapter._walk(child)

    def fetch(self) -> AdapterResult:
        fetched_at = timezone.now()
        if getattr(settings, "WORKSITE_DEMO", False):
            return AdapterResult("disabled", self.source, fetched_at, [], reason="disabled")
        if error := self._setup_error():
            return AdapterResult("error", self.source, fetched_at, [], False, error)
        try:
            response = self.client.get(self.endpoint, params={"Authorization": self.api_key, "format": "JSON"})
            response.raise_for_status()
            payload = response.json()
            records = []
            for item in self._walk(payload):
                location = item.get("locationName") or item.get("LocationName")
                hazards = item.get("hazardConditions") or item.get("HazardConditions") or []
                if isinstance(hazards, dict):
                    hazards = [hazards]
                for hazard in hazards:
                    if not isinstance(hazard, dict) or not hazard.get("phenomena") and not hazard.get("Phenomena"):
                        continue
                    phenomenon = hazard.get("phenomena") or hazard.get("Phenomena")
                    issue_time = hazard.get("issueTime") or hazard.get("IssueTime")
                    valid = hazard.get("validTime") or hazard.get("ValidTime") or {}
                    valid_from = valid.get("startTime") or valid.get("StartTime")
                    expires_at = valid.get("endTime") or valid.get("EndTime")
                    source_timestamp = issue_time or f"{valid_from or 'unknown'}|{expires_at or 'unknown'}"
                    external_id = f"{location or '全臺'}|{phenomenon}|{source_timestamp}"
                    records.append({
                        "external_id": external_id,
                        "source": self.source,
                        "severity": self._severity(hazard.get("significance") or hazard.get("Significance")),
                        "title": f"{phenomenon}｜{location or '全臺'}",
                        "payload": {"external_id": external_id, "location": location, "phenomena": phenomenon, "summary": hazard.get("contentText") or hazard.get("ContentText") or ""},
                        "occurred_at": issue_time,
                        "valid_from": valid_from,
                        "expires_at": expires_at,
                    })
            return AdapterResult("ok", self.source, fetched_at, records)
        except (httpx.HTTPError, OSError, ValueError, TypeError, AttributeError) as exc:
            return AdapterResult("error", self.source, fetched_at, [], False, _safe_upstream_error(exc))

    @staticmethod
    def _severity(value):
        value = str(value or "").lower()
        if any(token in value for token in ("red", "severe", "重大")):
            return "red"
        if any(token in value for token in ("orange", "warning", "警")):
            return "orange"
        return "yellow"
