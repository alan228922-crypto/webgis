from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from django.conf import settings
from worksite_gis.services.geology_geometry import DataValidationError, POLYGON_TYPES, validate_wgs84, wgs84_shape


GEOLOGY_API_BASE = "https://www.geologycloud.tw/api/v1/zh-tw"
GEOLOGY_CONTRACT_VERSION = "v3"
GEOLOGY_CACHE_KEY_DIGEST_HEX_LENGTH = 32
GEOLOGY_CACHE_TTL_SECONDS = 300
GEOLOGY_STALE_TTL_SECONDS = 86400
GEOLOGY_MAX_RESPONSE_BYTES = 1_500_000
GEOLOGY_UPSTREAM_TIMEOUT_SECONDS = 5

GEOLOGY_LAYERS = {
    "drill": {"endpoint": "Drill", "label": "工程地質鑽孔", "mode": "bbox"},
    "liquefaction": {"endpoint": "liquefaction", "label": "液化潛勢空間提示", "mode": "area"},
    "dip_slope": {"endpoint": "DipSlope", "label": "順向坡", "mode": "bbox"},
    "rock_fall": {"endpoint": "RockFall", "label": "落石", "mode": "bbox"},
    "debris_flow_track": {"endpoint": "DebrisFlowTrack", "label": "土石流流動區", "mode": "bbox"},
}


def canonical_cache_key_payload(
    endpoint: str,
    query: dict[str, Any],
    *,
    version: str = GEOLOGY_CONTRACT_VERSION,
    project_slug: str = "none",
    layer: str = "none",
    layer_set: str = "none",
    bbox: str = "none",
    area: str = "none",
    limit: int | None = None,
    geometry_hash: str = "none",
) -> dict[str, Any]:
    bbox = bbox if bbox != "none" else str(query.get("bbox", "none"))
    area = area if area != "none" else str(query.get("area", "none"))
    if limit is None and query.get("limit") is not None:
        limit = query["limit"]
    if isinstance(layer_set, str):
        normalized_layer_set = sorted(part.strip() for part in layer_set.split(",") if part.strip())
    else:
        normalized_layer_set = sorted(str(part) for part in (layer_set or []))
    return {
        "contract_version": version,
        "project_slug": project_slug,
        "layer": layer,
        "layer_set": normalized_layer_set,
        "endpoint": endpoint.rstrip("/").split("/")[-1],
        "bbox": bbox,
        "area": area,
        "limit": limit,
        "geometry_hash": geometry_hash,
        "query": query,
    }


def build_cache_key(
    endpoint: str,
    query: dict[str, Any],
    *,
    version: str = GEOLOGY_CONTRACT_VERSION,
    project_slug: str = "none",
    layer: str = "none",
    layer_set: str | list[str] | tuple[str, ...] = "none",
    bbox: str = "none",
    area: str = "none",
    limit: int | None = None,
    geometry_hash: str = "none",
) -> str:
    context = canonical_cache_key_payload(
        endpoint,
        query,
        version=version,
        project_slug=project_slug,
        layer=layer,
        layer_set=layer_set,
        bbox=bbox,
        area=area,
        limit=limit,
        geometry_hash=geometry_hash,
    )
    canonical = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:GEOLOGY_CACHE_KEY_DIGEST_HEX_LENGTH]
    return f"geology-layer:{version}:{digest}"


def _safe_error(exc: BaseException) -> str:
    message = str(exc)
    message = re.sub(r"https?://[^\s]+", "upstream", message)
    message = re.sub(r"([?&](?:key|token|api_key|authorization)=[^&\s]+)", r"\1=REDACTED", message, flags=re.I)
    message = re.sub(r"[\r\n\t]+", " ", message)
    return f"{type(exc).__name__}: {message[:240]}"


def normalize_geojson(payload: dict[str, Any], *, layer: str | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise ValueError("GeoJSON must be a FeatureCollection")
    validate_wgs84(payload)
    features = payload.get("features")
    if not isinstance(features, list):
        raise ValueError("GeoJSON features must be a list")
    normalized = {"type": "FeatureCollection", "features": []}
    for index, item in enumerate(features):
        if not isinstance(item, dict):
            raise ValueError(f"feature {index} must be an object")
        geometry = item.get("geometry")
        if not isinstance(geometry, dict) or not geometry.get("type"):
            raise ValueError(f"feature {index} geometry is required")
        properties = item.get("properties")
        if not isinstance(properties, dict):
            raise ValueError(f"feature {index} properties must be an object")
        validate_wgs84(item)
        try:
            if layer == "liquefaction":
                wgs84_shape(geometry, types=POLYGON_TYPES)
            else:
                wgs84_shape(geometry)
        except DataValidationError as exc:
            raise DataValidationError(exc.reason, f"feature {index} geometry is invalid: {exc}") from exc
        feature = {"type": "Feature", "geometry": deepcopy(geometry), "properties": deepcopy(properties)}
        if isinstance(item.get("id"), (str, int)):
            feature["id"] = item["id"]
        if layer == "drill":
            project_no = properties.get("Project_No")
            hole_no = properties.get("Hole_Point_No")
            if project_no is not None and hole_no is not None:
                feature["properties"]["geology_id"] = f"{project_no}+{hole_no}"
        normalized["features"].append(feature)
    return normalized


@dataclass(frozen=True)
class GeologyDatasetEvidence:
    """Server-side evidence from a verified provider or an offline fixture adapter."""

    source_id: str
    dataset_version: str | None = None
    source_published_at: str | None = None
    complete: bool = False


@dataclass
class GeologyCloudAdapter:
    layer: str
    client: Any = None
    timeout: float = 10
    max_response_bytes: int = GEOLOGY_MAX_RESPONSE_BYTES

    def __post_init__(self):
        if self.layer not in GEOLOGY_LAYERS:
            raise ValueError("unsupported geology layer")
        if self.client is None and not getattr(settings, "WORKSITE_DEMO", False):
            self.client = httpx.Client(timeout=self.timeout, trust_env=False)
        self.definition = GEOLOGY_LAYERS[self.layer]
        self.endpoint = f"{GEOLOGY_API_BASE}/{self.definition['endpoint']}"

    def fetch(self, query: dict[str, Any]) -> tuple[dict[str, Any], datetime]:
        if getattr(settings, "WORKSITE_DEMO", False):
            raise RuntimeError("Direct GeologyCloud access is disabled in Demo mode")
        response = self.client.get(self.endpoint, params={**query, "t": ".json"})
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type.lower():
            raise ValueError("upstream response is not JSON")
        if len(response.content) > self.max_response_bytes:
            raise ValueError("upstream response exceeds configured size limit")
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise ValueError("upstream JSON is invalid") from exc
        return normalize_geojson(payload, layer=self.layer), datetime.now(timezone.utc)


def safe_error(exc: BaseException) -> str:
    return _safe_error(exc)
