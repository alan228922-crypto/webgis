from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import httpx

from django.conf import settings
from django.core.cache import cache
from pyproj import Transformer
from shapely.geometry import box, shape
from shapely.ops import transform, unary_union

from worksite_gis.services.geology_geometry import (
    DISTANCE_EPSILON_M, DataValidationError, project_geometry,
)
from worksite_gis.services.worksite_publication import geology_source

from worksite_gis.integrations.geology_cloud import (
    GEOLOGY_CACHE_TTL_SECONDS,
    GEOLOGY_LAYERS,
    GEOLOGY_STALE_TTL_SECONDS,
    GEOLOGY_UPSTREAM_TIMEOUT_SECONDS,
    GeologyCloudAdapter,
    GeologyDatasetEvidence,
    build_cache_key,
    normalize_geojson,
    safe_error,
)


PROJECTED_CRS = "EPSG:3826"
GEOLOGY_LAYER_SET = ",".join(sorted(GEOLOGY_LAYERS))
_project = Transformer.from_crs("EPSG:4326", PROJECTED_CRS, always_xy=True).transform


def _worksite_union(worksite_geometry):
    if worksite_geometry is None:
        return None
    if isinstance(worksite_geometry, dict):
        if worksite_geometry.get("type") == "FeatureCollection":
            geometries = [shape(item["geometry"]) for item in worksite_geometry.get("features", []) if item.get("geometry")]
            return unary_union(geometries) if geometries else None
        return shape(worksite_geometry)
    return worksite_geometry


def screen_features(payload: dict[str, Any], worksite_geometry, *, layer: str) -> dict[str, Any]:
    payload = normalize_geojson(payload, layer=layer)
    worksite = _worksite_union(worksite_geometry)
    features = payload.get("features", []) if isinstance(payload, dict) else []
    if worksite is None:
        return {
            "status": "no_worksite_geometry",
            "intersects": None,
            "nearest_distance_m": None,
            "nearby_feature_count": len(features),
            "nearest_drill": None,
        }
    projected_worksite = transform(_project, worksite)
    nearest = None
    nearest_distance = None
    intersects = False
    for item in features:
        geometry_payload = item.get("geometry")
        if not geometry_payload:
            continue
        candidate = transform(_project, shape(geometry_payload))
        distance = projected_worksite.distance(candidate)
        intersects = intersects or projected_worksite.intersects(candidate)
        if nearest_distance is None or distance < nearest_distance:
            nearest_distance = distance
            nearest = item
    return {
        "status": "available",
        "intersects": bool(intersects),
        "nearest_distance_m": round(nearest_distance, 2) if nearest_distance is not None else None,
        "nearby_feature_count": len(features),
        "nearest_drill": nearest if layer == "drill" else None,
    }


def derive_liquefaction_area(project, worksite_geometry):
    if _worksite_union(worksite_geometry) is None:
        return None
    if isinstance(worksite_geometry, dict) and worksite_geometry.get("type") == "FeatureCollection":
        for item in worksite_geometry.get("features", []):
            properties = item.get("properties") if isinstance(item, dict) else None
            if isinstance(properties, dict):
                for key in ("administrative_area", "county", "COUNTYNAME", "TOWNNAME"):
                    if properties.get(key):
                        return properties[key]
    metadata = project.metadata if project is not None and isinstance(project.metadata, dict) else {}
    # This value must be populated by a formal worksite-to-administrative-area
    # derivation. Do not fall back to a center point or a Taiwan-wide request.
    if metadata.get("administrative_area_source") == "formal_worksite_geometry":
        return metadata.get("administrative_area") or None
    return None


def worksite_geometry_hash(worksite_geometry):
    if worksite_geometry is None:
        return "none"
    canonical = json.dumps(worksite_geometry, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def worksite_headers(response, project, source, reason):
    response["X-WORKSITE-Worksite-Generation"] = str(project.geology_generation)
    response["Cache-Control"] = "private, no-store"
    if source:
        digest = worksite_geometry_hash(source.geometry)
        response["X-WORKSITE-Worksite"] = f"{source.status}:{source.plan_id}:{source.version}:{digest}"
    if reason:
        response["X-WORKSITE-Worksite-Reason"] = reason
    return response


def confirmed_liquefaction_area(analysis):
    metadata = analysis.metadata
    revision = metadata["revision_id"]
    if metadata["publication_status"] != "verified" or revision is None:
        return None, "unverified_administrative_area"
    records = getattr(settings, "WORKSITE_GEOLOGY_LIQUEFACTION_AREAS", {})
    record = records.get(str(revision)) if isinstance(records, dict) else None
    if getattr(settings, "WORKSITE_DEMO", False):
        from worksite_gis.demo import synthetic_area_record
        record = synthetic_area_record(metadata)
    if not isinstance(record, dict):
        return None, "missing_administrative_area"
    if (
        record.get("geometry_hash") != metadata["geometry_hash"]
        or record.get("project_id") != metadata["project_id"]
        or not isinstance(record.get("source"), str) or not record["source"].strip()
        or not isinstance(record.get("area"), str) or not record["area"].strip()
    ):
        return None, "unverified_administrative_area"
    bounds = record.get("coverage_bbox")
    if not isinstance(bounds, (list, tuple)) or len(bounds) != 4:
        return None, "administrative_coverage_unknown"
    try:
        west, south, east, north = bounds
        x0, y0, x1, y1 = metadata["query_bbox"]
        if not (-180 <= west <= x0 <= x1 <= east <= 180 and -90 <= south <= y0 <= y1 <= north <= 90):
            return None, "administrative_coverage_unknown"
    except (TypeError, ValueError):
        return None, "administrative_coverage_unknown"
    return {"area": record["area"], "source": record["source"], "coverage_bbox": list(bounds)}, None


def _failure_reason(exc):
    if isinstance(exc, DataValidationError):
        return exc.reason
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return "timeout"
    if isinstance(exc, (httpx.TransportError, ConnectionError)):
        return "network_failure"
    return "invalid_response"


def quality_metadata(status, *, fetched_at=None, query=None, evidence=None, reason=None,
                     upstream_count=None, candidate_count=None, exact_count=None, returned_count=None, complete=False,
                     administrative_area=None):
    success = status in {"live", "cached", "stale"}
    default_source = "Synthetic Data" if getattr(settings, "WORKSITE_DEMO", False) else "GeologyCloud"
    included = exact_count if exact_count is not None else candidate_count
    return {
        "retrieval": ("empty" if included == 0 else "ok") if success else status,
        "failure_reason": reason,
        "cache": {"live": "live", "cached": "fresh_cache", "stale": "stale_cache"}.get(status),
        "completeness": {
            "display_truncated": included is not None and returned_count is not None and included > returned_count,
            "local_scan_complete": success,
            "scan_incomplete": False,
            "upstream_limit_unknown": not (evidence and evidence.get("complete")),
            "upstream_coverage_unknown": not (evidence and evidence.get("complete")),
            "analysis_input_complete": bool(complete),
        },
        "counts": {"upstream_candidates": upstream_count, "exact_matches": exact_count,
                   "analysis_features": exact_count, "scope_candidates": candidate_count, "display_features": returned_count},
        "source": (evidence or {}).get("source_id", default_source),
        "dataset_version": (evidence or {}).get("dataset_version"),
        "source_published_at": (evidence or {}).get("source_published_at"),
        "fetched_at": fetched_at,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "query": {**query, "t": ".json"} if query is not None else None,
        "administrative_area": administrative_area,
    }


def polygon_overlap(worksite, polygons):
    if worksite is None or worksite.is_empty or worksite.area <= 0:
        return {"intersection_area_m2": None, "intersection_ratio": None}
    area = unary_union([worksite.intersection(polygon) for polygon in polygons]).area
    return {"intersection_area_m2": round(area, 2), "intersection_ratio": round(area / worksite.area, 6)}


def _fixed_candidates(payload, analysis, layer):
    records = []
    worksite = analysis.worksite
    bound = box(*analysis.metadata["query_bbox"])
    for feature in payload["features"]:
        native = shape(feature["geometry"])
        if not bound.intersects(native):
            continue
        geometry = project_geometry(native)
        distance = worksite.distance(geometry)
        if distance <= analysis.metadata["buffer_m"] + DISTANCE_EPSILON_M:
            identity = feature.get("id", feature["properties"].get("geology_id"))
            stable_id = str(identity) if identity is not None else json.dumps(feature, sort_keys=True, ensure_ascii=False)
            records.append((distance, stable_id, feature, geometry))
    records.sort(key=lambda item: (item[0], item[1]))
    nearest = records[0] if records else None
    screening = {
        "status": "available", "intersects": any(worksite.intersects(item[3]) for item in records),
        "nearest_distance_m": round(nearest[0], 2) if nearest else None,
        "nearby_feature_count": len(records),
        "nearest_drill": nearest[2] if nearest and layer == "drill" else None,
        "nearby_feature_count_scope": "exact_matches",
    }
    return records, screening


class GeologyCloudService:
    def __init__(self, *, client=None, timeout=GEOLOGY_UPSTREAM_TIMEOUT_SECONDS):
        self.client = client
        self.timeout = timeout

    def _query(self, layer, *, bbox, project, worksite):
        definition = GEOLOGY_LAYERS[layer]
        if definition["mode"] == "area":
            area = derive_liquefaction_area(project, worksite)
            if not area:
                return None, None, "unavailable"
            return {"area": area}, area, None
        if not bbox:
            raise ValueError("bbox is required")
        return {"bbox": bbox}, bbox, None

    def _cache_context(self, layer, *, bbox, project, limit, publication=None, analysis=None):
        source, reason = publication if publication is not None else geology_source(project)
        worksite = source.geometry if source else None
        if project is not None and not source:
            return {"unavailable": reason}
        area_evidence = None
        if analysis and layer == "liquefaction":
            area_evidence, reason = confirmed_liquefaction_area(analysis)
            if reason:
                return {"unavailable": reason}
            area_or_bbox = area_evidence["area"]
            query = {"area": area_or_bbox}
        else:
            query, area_or_bbox, unavailable = self._query(layer, bbox=bbox, project=project, worksite=worksite)
            if unavailable:
                return {"unavailable": "missing_administrative_area"}
        definition = GEOLOGY_LAYERS[layer]
        cache_query = {**query, "bbox": bbox, "area": area_or_bbox if definition["mode"] == "area" else None, "limit": limit}
        if analysis:
            cache_query.update(
                mode="screening", buffer_m=analysis.metadata["buffer_m"],
                revision_id=analysis.metadata["revision_id"], method_version=analysis.metadata["method_version"],
                administrative_area=area_evidence,
            )
        demo_scenario = getattr(settings, "WORKSITE_DEMO_SCENARIO", "normal") if getattr(settings, "WORKSITE_DEMO", False) else None
        if demo_scenario:
            cache_query["synthetic_scenario"] = demo_scenario
        if project is None:
            key = build_cache_key(definition["endpoint"], cache_query)
        else:
            key = build_cache_key(
                definition["endpoint"],
                {**cache_query, "worksite_publication": source.identity},
                version="v4" if analysis else "v3",
                project_slug=project.slug,
                layer=layer,
                layer_set=GEOLOGY_LAYER_SET,
                bbox=bbox or "none",
                area=area_or_bbox if definition["mode"] == "area" else "none",
                limit=limit,
                geometry_hash=worksite_geometry_hash(worksite),
            )
        stale_key = f"{key}:stale"
        return {
            "query": query,
            "area_or_bbox": area_or_bbox,
            "worksite": worksite,
            "analysis": analysis,
            "administrative_area": area_evidence,
            "cache_key": key,
            "stale_key": stale_key,
            "fresh": cache.get(key),
            "stale": cache.get(stale_key),
            "unavailable": None,
            "demo_scenario": demo_scenario,
        }

    def prepare_cache_context(self, layer, *, bbox=None, project=None, limit=None, publication=None, analysis=None):
        """Snapshot cache state in the request thread before parallel work starts."""
        if layer not in GEOLOGY_LAYERS:
            raise ValueError("unsupported geology layer")
        return self._cache_context(layer, bbox=bbox, project=project, limit=limit, publication=publication, analysis=analysis)

    @staticmethod
    def persist_cache_write(cache_write):
        if not cache_write:
            return
        cache_payload = cache_write["payload"]
        cache.set(cache_write["cache_key"], cache_payload, GEOLOGY_CACHE_TTL_SECONDS)
        cache.set(cache_write["stale_key"], cache_payload, GEOLOGY_STALE_TTL_SECONDS)

    def fetch_layer(self, layer, *, bbox=None, project=None, limit=None, cache_context=None, publication=None, analysis=None):
        if layer not in GEOLOGY_LAYERS:
            raise ValueError("unsupported geology layer")
        is_parallel_context = cache_context is not None
        context = cache_context if cache_context is not None else self._cache_context(layer, bbox=bbox, project=project, limit=limit, publication=publication, analysis=analysis)
        query = context.get("query")
        worksite = context.get("worksite")
        analysis = context.get("analysis")
        area_evidence = context.get("administrative_area")
        unavailable = context.get("unavailable")

        def no_data(status, reason, message):
            screening = {"status": status, "intersects": None, "nearest_distance_m": None}
            if layer == "liquefaction":
                screening.update(intersection_area_m2=None, intersection_ratio=None, overlap_reason=reason)
            source = "Synthetic Data" if getattr(settings, "WORKSITE_DEMO", False) else "GeologyCloud"
            return {"status": status, "fetched_at": None, "source": source,
                    "features": [], "screening": screening, "error": message,
                    "data_quality": quality_metadata(status, reason=reason, query=query, administrative_area=area_evidence)}

        if unavailable:
            return no_data("unavailable", unavailable, unavailable)

        def response(status, cached, error=None, reason=None):
            return self._response(status, cached, worksite, layer, bbox, limit, error,
                                  analysis=analysis, query=query, reason=reason, area_evidence=area_evidence)

        try:
            if context.get("fresh"):
                return response("cached", context["fresh"])
            scenario = context.get("demo_scenario")
            if scenario:
                from worksite_gis.demo import synthetic_geology
                if scenario == "timeout":
                    raise TimeoutError("Synthetic Data timeout")
                cache_payload = synthetic_geology(layer, scenario, bbox)
                if bbox:
                    cache_payload["payload"] = self._clip_bbox(cache_payload["payload"], bbox)
                if scenario == "stale":
                    return response("stale", cache_payload, "Synthetic Data timeout", "timeout")
            else:
                fetched = GeologyCloudAdapter(layer, client=self.client, timeout=self.timeout).fetch(query)
                payload, fetched_at = fetched[:2]
                evidence = fetched[2] if len(fetched) == 3 else None
                if evidence is not None and not isinstance(evidence, GeologyDatasetEvidence):
                    raise ValueError("Invalid dataset evidence")
                cache_payload = {"fetched_at": fetched_at.isoformat(), "payload": normalize_geojson(payload, layer=layer)}
                if evidence is not None:
                    cache_payload["evidence"] = {
                        "source_id": evidence.source_id, "dataset_version": evidence.dataset_version,
                        "source_published_at": evidence.source_published_at, "complete": evidence.complete is True,
                    }
            result = response("live", cache_payload)
            cache_write = {"cache_key": context["cache_key"], "stale_key": context["stale_key"], "payload": cache_payload}
            if is_parallel_context:
                result["_cache_write"] = cache_write
            else:
                self.persist_cache_write(cache_write)
            return result
        except Exception as exc:
            if context.get("stale"):
                try:
                    return response("stale", context["stale"], safe_error(exc), _failure_reason(exc))
                except (ValueError, TypeError, KeyError):
                    pass
            return no_data("error", _failure_reason(exc), safe_error(exc))

    @staticmethod
    def _clip_bbox(payload, bbox):
        min_x, min_y, max_x, max_y = [float(value) for value in bbox.split(",")]
        clip = shape({"type": "Polygon", "coordinates": [[[min_x, min_y], [max_x, min_y], [max_x, max_y], [min_x, max_y], [min_x, min_y]]]})
        features = []
        for item in payload.get("features", []):
            geometry = shape(item["geometry"])
            if geometry.intersects(clip):
                features.append(item)
        return {"type": "FeatureCollection", "features": features}

    @staticmethod
    def _response(status, cached, worksite, layer, bbox, limit=None, error=None, *,
                  analysis=None, query=None, reason=None, area_evidence=None):
        payload = cached["payload"]
        upstream_count = len(payload.get("features", []))
        evidence = cached.get("evidence")
        complete = bool(analysis and evidence and evidence.get("complete"))
        if analysis:
            records, screening = _fixed_candidates(payload, analysis, layer)
            candidate_features = [item[2] for item in records]
        else:
            if layer == "liquefaction" and bbox:
                payload = GeologyCloudService._clip_bbox(payload, bbox)
            candidate_features = payload.get("features", [])
            screening = screen_features(payload, worksite, layer=layer)
            screening["nearby_feature_count_scope"] = "candidate_features"

        if layer == "liquefaction":
            overlap_area = overlap_ratio = None
            overlap_reason = "analysis_input_incomplete" if analysis else "viewport_exploration"
            if complete and area_evidence:
                overlap = polygon_overlap(analysis.worksite, (item[3] for item in records))
                overlap_area = overlap["intersection_area_m2"]
                overlap_ratio = overlap["intersection_ratio"]
                overlap_reason = None if overlap_ratio is not None else "invalid_geometry"
            screening.update(intersection_area_m2=overlap_area, intersection_ratio=overlap_ratio,
                             overlap_reason=overlap_reason)

        returned = candidate_features if limit is None else candidate_features[:limit]
        return {
            "status": status, "fetched_at": cached.get("fetched_at"),
            "source": (evidence or {}).get("source_id", "GeologyCloud") if getattr(settings, "WORKSITE_DEMO", False) else "GeologyCloud",
            "features": returned, "candidate_feature_count": len(candidate_features),
            "returned_feature_count": len(returned), "screening": screening, "error": error,
            "data_quality": quality_metadata(status, fetched_at=cached.get("fetched_at"),
                query=query, evidence=evidence, reason=reason, upstream_count=upstream_count,
                candidate_count=len(candidate_features), exact_count=len(candidate_features) if analysis else None,
                returned_count=len(returned), complete=complete,
                administrative_area=area_evidence),
        }
