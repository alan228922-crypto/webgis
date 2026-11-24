import json
import math
import os
import re
import sqlite3
from datetime import datetime, timedelta
from functools import lru_cache
from itertools import islice
from pathlib import Path, PureWindowsPath

from django.conf import settings
from django.db import DatabaseError, transaction
from django.db.models import Prefetch
from django.http import FileResponse, HttpResponse, JsonResponse
from django.utils import timezone
from shapely.geometry import shape
from shapely import wkt

from .data_pipeline.spatial_index import query_bbox, query_bbox_by_source_category, query_point
from .integrations.base import SnapshotCache
from .integrations.cwa import CwaAlertAdapter, CwaRadarAdapter, CwaWeatherAdapter
from .integrations.tdx import TdxClient
from .services import dynamic_flood
from .services.geology_screening import GeologyCloudService, quality_metadata, worksite_geometry_hash, worksite_headers
from .services.geology_geometry import fixed_screening_context, parse_buffer
from .services.worksite_publication import geology_source
from .services.geology_summary import GeologySummaryService
from .models import Alert, ApiServiceConfig, ApiSnapshot, CctvCamera, LayerCatalog, Plan, PlanVersion, Project, StationSeed
from .permissions import (
    canonical_plan_status,
    can_create_plan,
    can_delete_plan,
    can_delete_project,
    plan_delete_block_reason,
    project_delete_block_reason,
    can_edit_plan,
    can_edit_project,
    can_view_plan,
    can_view_project,
    is_authenticated_user,
    is_plan_published,
    is_plan_submitted,
    is_manager_or_developer,
)
from .planning import validate_feature_collection
from . import cctv_service
from . import alert_sync
from . import train_positioning
from . import weather_service
from . import traffic_service
from .views_common import (
    _api_authentication_required,
    _authenticated_required,
    _bounded_int,
    _catalog_path,
    _error,
    _layer_health_path,
    _layer_is_spatial_indexed,
    _staff_required,
)
from .views_maintainer import (
    maintainer_api_services,
    maintainer_data_table_detail,
    maintainer_data_tables,
    maintainer_gis_layers,
)
from .views_projects import (
    _project_payload,
    project_archive,
    project_detail,
    projects,
)
from .views_plans import (
    _plan_payload,
    project_plan_archive,
    project_plan_detail,
    project_plan_export,
    project_plan_review,
    project_plan_select_geology_source,
    project_plan_submit,
    project_plan_versions,
    project_plans,
    project_plans_ui_metadata,
)


@lru_cache(maxsize=1)
def _tra_station_coordinates():
    path = settings.BASE_DIR / "data" / "processed" / "gis" / "transport" / "tra_stations.geojson"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    result = {}
    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if geometry.get("type") != "Point" or len(coordinates) < 2:
            continue
        properties = feature.get("properties") or {}
        for key in ("MARKNAME1", "MARKNAME2"):
            name = properties.get(key)
            if name:
                result[str(name).replace("臺鐵", "").replace("車站", "").strip()] = (coordinates[1], coordinates[0])
    return result


@lru_cache(maxsize=1)
def _rail_geometries():
    path = settings.BASE_DIR / "data" / "processed" / "gis" / "transport" / "rail_lines.geojson"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ()
    geometries = []
    for feature in payload.get("features", []):
        try:
            geometry = shape(feature.get("geometry"))
        except (TypeError, ValueError):
            continue
        if geometry.geom_type == "LineString" and not geometry.is_empty:
            geometries.append(geometry)
        elif geometry.geom_type == "MultiLineString":
            geometries.extend(line for line in geometry.geoms if not line.is_empty)
    return tuple(geometries)


@lru_cache(maxsize=1)
def _tdx_tra_metadata(client_id, client_secret):
    if not client_id or not client_secret:
        return {}, [], [], []
    adapter = TdxClient(client_id, client_secret, timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS)
    def safe(fetcher):
        try:
            return fetcher()
        except Exception:
            return []
    stations = safe(adapter.fetch_tra_stations)
    networks = safe(adapter.fetch_tra_network)
    station_of_line = safe(adapter.fetch_tra_station_of_line)
    line_network = safe(adapter.fetch_tra_line_network)
    return {str(item.get("station_id")): item for item in stations if item.get("station_id")}, networks, station_of_line, line_network


def _project_point_to_rail(point, rail_geometries):
    return train_positioning._project_point_to_rail(point, rail_geometries)


def _align_train_position(record, rail_geometries, max_distance=0.0025):
    return train_positioning._align_train_position(record, rail_geometries, max_distance=max_distance)


def _sanitize_train_coordinate(record, rail_geometries):
    return train_positioning._sanitize_train_coordinate(record, rail_geometries)


def _apply_tdx_station_metadata(record, stations):
    return train_positioning._apply_tdx_station_metadata(record, stations)


def _apply_estimated_position(record, estimated, rail_geometries, fallback=None):
    return train_positioning._apply_estimated_position(record, estimated, rail_geometries, fallback=fallback)


def _clock_seconds(value):
    return train_positioning._clock_seconds(value)


def _train_segment_duration_seconds(record):
    return train_positioning._train_segment_duration_seconds(record)


def _estimate_train_position(record, station_coordinates):
    return train_positioning._estimate_train_position(record, station_coordinates, now=timezone.localtime())


def _estimate_train_position_on_route(record, station_coordinates, rail_geometries, ratio=0.5):
    return train_positioning._estimate_train_position_on_route(record, station_coordinates, rail_geometries, ratio=ratio)


def _route_track_path(record, station_coordinates, rail_geometries, samples=32):
    return train_positioning._route_track_path(record, station_coordinates, rail_geometries, samples=samples)


def _attach_track_animation(record, station_coordinates, rail_geometries, samples=32):
    return train_positioning._attach_track_animation(
        record, station_coordinates, rail_geometries, samples=samples, now=timezone.localtime()
    )


def _train_progress_ratio(record):
    return train_positioning._train_progress_ratio(record, now=timezone.localtime())




def health(request):
    return JsonResponse({"status": "ok", "service": "worksite_gis"})


def _alert_payload(alert):
    occurred_at = alert.occurred_at
    if alert.source.upper() == "CWA":
        occurred_at = alert_sync.source_datetime((alert.payload or {}).get("source_issued_at"))
    return {"id": alert.id, "source": alert.source, "severity": alert.severity, "title": alert.title, "payload": alert.payload, "occurred_at": occurred_at.isoformat() if occurred_at else None, "cleared_at": alert.cleared_at.isoformat() if alert.cleared_at else None}


def _sync_cwa_alerts():
    return alert_sync.sync_cwa_alerts(
        api_key=settings.CWA_API_KEY,
        timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS,
        cache_ttl=60,
        adapter_class=CwaAlertAdapter,
        snapshot_cache_class=SnapshotCache,
        alert_model=Alert,
        now=timezone.now,
    )


def current_alerts(request):
    now = timezone.now()
    status = "disabled"
    error = "CWA credential missing"
    snapshot = None
    if settings.CWA_API_KEY:
        snapshot = ApiSnapshot.objects.filter(service__iexact="CWA", kind="alerts", status="ok").first()
        if snapshot is None:
            status, error = "unknown", "no_alert_snapshot"
        else:
            status, error = ("ok", "") if snapshot.expires_at > now else ("stale", "alert_snapshot_expired")
    alerts = [
        _alert_payload(alert) for alert in Alert.objects.filter(cleared_at__isnull=True)
        if alert.source.upper() != "CWA" or (snapshot and alert_sync.confirmed_cwa_alert(alert, now))
    ]
    return JsonResponse({"status": status, "error": error, "alerts": alerts,
                         "fetched_at": snapshot.fetched_at.isoformat() if snapshot else None})


def alert_history(request):
    try:
        limit = min(max(int(request.GET.get("limit", "50")), 1), 200)
    except ValueError:
        return _error("limit must be numeric")
    try:
        hours = min(max(float(request.GET.get("hours", str(settings.WORKSITE_ALERT_HISTORY_HOURS))), 1), 168)
    except ValueError:
        return _error("hours must be numeric")
    cutoff = timezone.now() - timedelta(hours=hours)
    alerts = Alert.objects.filter(occurred_at__gte=cutoff).order_by("-occurred_at", "-id")[:limit]
    return JsonResponse({"hours": hours, "alerts": [_alert_payload(alert) for alert in alerts]})


def _optional_runtime_status(missing, has_tiles):
    demo = getattr(settings, "WORKSITE_DEMO", False)
    return {
        "tdx": "disabled" if demo else "unknown" if settings.TDX_CLIENT_ID and settings.TDX_CLIENT_SECRET else "not_configured",
        "cwa": "disabled" if demo else "unknown" if settings.CWA_API_KEY else "not_configured",
        "wra": "unknown" if not demo and settings.WRA_ENABLED else "disabled",
        "geology_cloud": "synthetic" if demo else "unknown",
        "gis": "unavailable" if missing else "present_unverified",
        "basemap": "present_unverified" if has_tiles else "unavailable",
    }


def _read_optional_object(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def status(request):
    try:
        catalog = list(LayerCatalog.objects.all())
        services = list(ApiServiceConfig.objects.values_list("status", flat=True))
    except DatabaseError:
        return JsonResponse({
            "service": "worksite_gis", "status": "unavailable", "error": "database_unavailable",
            "layers": {"count": 0, "missing_outputs": []},
            "basemap": {"available": False, "file_count": 0}, "api_services": {},
            "processed_data": {"available": False, "count": 0, "valid": 0, "invalid": 0},
        }, status=503)
    missing = []
    for layer in catalog:
        path = _layer_health_path(layer)
        if not path.exists():
            missing.append(layer.slug)
    viewport_index = _catalog_path(getattr(
        settings,
        "WORKSITE_GEOLOGIC_SENSITIVE_VIEWPORT_INDEX_PATH",
        settings.DATA_PROCESSED_DIR / "gis" / "hazards" / "geologic_sensitive_areas.sqlite3",
    ))
    if not viewport_index.exists():
        missing.append("geologic_sensitive_areas:viewport_index")
    manifest = _read_optional_object(settings.DATA_PROCESSED_DIR / "basemap" / "offline_tiles_manifest.json")
    validation = _read_optional_object(settings.DATA_CATALOG_DIR / "validation_report.json")
    validation_values = list(validation.values()) if validation is not None else []
    valid_count = sum(1 for item in validation_values if isinstance(item, dict) and item.get("ok") is True)
    processed_data = {"available": validation is not None, "count": len(validation_values),
                      "valid": valid_count, "invalid": len(validation_values) - valid_count}
    service_counts = {key: 0 for key in ("healthy", "degraded", "error", "unknown")}
    for service_status in services:
        service_counts[service_status if service_status in service_counts else "unknown"] += 1
    api_services = {"count": sum(service_counts.values()), **service_counts}
    tile_root = settings.OFFLINE_TILE_ROOT
    has_tiles = False
    if tile_root.is_dir():
        for _, _, filenames in islice(os.walk(tile_root), 128):
            if any(Path(filename).suffix.lower() in {".jpg", ".jpeg", ".png"} for filename in filenames):
                has_tiles = True
                break
    manifest_file_count = (manifest or {}).get("file_count") if has_tiles else None
    if type(manifest_file_count) is not int or manifest_file_count < 0:
        manifest_file_count = 0
    overall_degraded = bool(missing or not has_tiles or service_counts["error"] or service_counts["degraded"])
    return JsonResponse({
        "service": "worksite_gis",
        "status": "degraded" if overall_degraded else "ok",
        "layers": {"count": len(catalog), "missing_outputs": missing},
        "basemap": {"available": has_tiles, "file_count": manifest_file_count},
        "api_services": api_services,
        "processed_data": processed_data,
        "optional_services": _optional_runtime_status(missing, has_tiles),
    })




def layers(request):
    payload = []
    for layer in LayerCatalog.objects.filter(format__in=("geojson", "spatial")):
        metadata = layer.metadata if isinstance(layer.metadata, dict) else {}
        spatial = metadata.get("spatial", {}) if isinstance(metadata.get("spatial", {}), dict) else {}
        payload.append({
            "slug": layer.slug,
            "name": layer.name,
            "category": layer.category,
            "format": layer.format,
            "feature_count": layer.feature_count,
            "crs": layer.crs,
            "delivery": "bbox" if _layer_is_spatial_indexed(layer) else "full",
            "updated_at": layer.updated_at.isoformat() if layer.updated_at else None,
        })
    return JsonResponse({"layers": payload})




def _geology_bbox(request):
    raw = request.GET.get("bbox", "")
    try:
        values = tuple(float(value.strip()) for value in raw.split(","))
        if len(values) != 4 or not all(math.isfinite(value) for value in values):
            raise ValueError
        min_lng, min_lat, max_lng, max_lat = values
        if not (-180 <= min_lng < max_lng <= 180 and -90 <= min_lat < max_lat <= 90):
            raise ValueError
        if max_lng - min_lng > 1.5 or max_lat - min_lat > 1.5:
            raise ValueError
        return ",".join(str(value) for value in values), None
    except (TypeError, ValueError):
        return None, "bbox must be a valid range no larger than 1.5 degrees"


def _geology_error(status, message):
    return {"status": status, "fetched_at": None, "source": "GeologyCloud", "features": [], "screening": {"status": status}, "error": message}


def _invalid_geology_request(request, message, reason="invalid_screening_parameters"):
    payload = _geology_error("error", message)
    if request.GET.get("mode"):
        payload["data_quality"] = quality_metadata("error", reason=reason)
    return JsonResponse(payload, status=400)


def _geology_request_context(request, project):
    mode = request.GET.get("mode")
    if mode not in (None, "exploration", "screening"):
        raise ValueError("mode must be exploration or screening")
    if mode == "screening":
        radius = parse_buffer(request.GET.get("buffer_m", 0))
        publication = geology_source(project)
        source, _ = publication
        analysis = fixed_screening_context(project, source, worksite_geometry_hash(source.geometry) if source else None, radius)
        return publication, analysis.bbox, analysis, mode
    if "buffer_m" in request.GET:
        raise ValueError("buffer_m requires mode=screening")
    bbox, error = _geology_bbox(request)
    if error:
        raise ValueError(error)
    return geology_source(project), bbox, None, mode


def _geology_context_metadata(mode, bbox, analysis):
    return analysis.metadata if analysis else {"mode": mode, "query_bbox": list(map(float, bbox.split(","))), "query_crs": "OGC:CRS84"}


def project_geology_summary(request, slug):
    if request.method != "GET":
        return JsonResponse(_geology_error("error", "GET required"), status=405)
    try:
        project = Project.objects.select_related("geology_source_plan__publication_event__revision").get(slug=slug)
    except Project.DoesNotExist:
        return JsonResponse(_geology_error("error", "project not found"), status=404)
    if not can_view_project(request.user, project):
        return JsonResponse(_geology_error("error", "project not found"), status=404)
    try:
        limit = int(request.GET.get("limit", "200"))
    except (TypeError, ValueError):
        limit = 0
    if not 1 <= limit <= 500:
        return _invalid_geology_request(request, "limit must be between 1 and 500")
    try:
        publication, bbox, analysis, mode = _geology_request_context(request, project)
    except (ValueError, TypeError, OverflowError) as exc:
        return _invalid_geology_request(request, str(exc), getattr(exc, "reason", "invalid_screening_parameters"))
    summary = GeologySummaryService().build(
        project, bbox=bbox, limit=limit, publication=publication, analysis=analysis, explicit_mode=bool(mode),
    )
    if mode:
        summary["screening_context"] = _geology_context_metadata(mode, bbox, analysis)
    return worksite_headers(JsonResponse(summary), project, *publication)


def project_geology_layer(request, slug, layer):
    if request.method != "GET":
        return JsonResponse(_geology_error("error", "GET required"), status=405)
    try:
        project = Project.objects.select_related("geology_source_plan__publication_event__revision").get(slug=slug)
    except Project.DoesNotExist:
        return JsonResponse(_geology_error("error", "project not found"), status=404)
    if not can_view_project(request.user, project):
        return JsonResponse(_geology_error("error", "project not found"), status=404)
    try:
        raw_limit = int(request.GET.get("limit", "200"))
    except (TypeError, ValueError):
        raw_limit = 0
    if not 1 <= raw_limit <= 500:
        return _invalid_geology_request(request, "limit must be between 1 and 500")
    try:
        publication, bbox, analysis, mode = _geology_request_context(request, project)
    except (ValueError, TypeError, OverflowError) as exc:
        return _invalid_geology_request(request, str(exc), getattr(exc, "reason", "invalid_screening_parameters"))
    try:
        response = GeologyCloudService().fetch_layer(
            layer, bbox=bbox, project=project, limit=raw_limit, publication=publication,
            **({"analysis": analysis} if analysis else {}),
        )
    except ValueError as exc:
        return JsonResponse(_geology_error("error", str(exc)), status=400)
    keys = ("status", "fetched_at", "source", "features", "screening", "error")
    if mode:
        response["screening_context"] = _geology_context_metadata(mode, bbox, analysis)
        keys += ("screening_context", "data_quality")
    response = {key: response.get(key) for key in keys}
    response["features"] = response.get("features", [])[:raw_limit]
    if len(json.dumps(response, ensure_ascii=False).encode("utf-8")) > 1_500_000:
        return JsonResponse(_geology_error("error", "geology response exceeds configured size limit"), status=413)
    return worksite_headers(JsonResponse(response), project, *publication)


def project_search_center(request, slug):
    authentication_error = _api_authentication_required(request)
    if authentication_error:
        return authentication_error
    if request.method != "POST":
        return _error("POST required", 405)
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    if not can_view_project(request.user, project):
        return _error("project not found", 404)
    if not can_edit_project(request.user, project):
        return _error("project access forbidden", 403)
    try:
        payload = json.loads(request.body or "{}")
        latitude = float(payload["latitude"])
        longitude = float(payload["longitude"])
        radius = 10.0
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return _error("latitude, longitude and radius_km must be numeric")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return _error("coordinates or radius out of range")
    project.search_center_latitude = latitude
    project.search_center_longitude = longitude
    project.search_radius_km = radius
    project.search_center_label = str(payload.get("label", "")).strip()
    project.search_center_address = str(payload.get("address", "")).strip()
    project.search_center_provider = str(payload.get("provider", "coordinates")).strip()
    project.search_center_raw_id = str(payload.get("raw_id", "")).strip()
    project.search_center_precision = str(payload.get("precision", "point")).strip()
    project.search_center_searched_at = timezone.now()
    project.setup_status = "located"
    project.save(update_fields=[
        "search_center_latitude", "search_center_longitude", "search_radius_km",
        "search_center_label", "search_center_address", "search_center_provider",
        "search_center_raw_id", "search_center_precision", "search_center_searched_at",
        "setup_status", "updated_at",
    ])
    return JsonResponse({"project": project.slug, "setup_status": project.setup_status, "search_center": _project_payload(project)["search_center"]})


def project_location_search(request, slug):
    if request.method not in {"GET", "POST"}:
        return _error("GET or POST required", 405)
    try:
        project = Project.objects.get(slug=slug)
        payload = request.GET if request.method == "GET" else json.loads(request.body or "{}")
        query = str(payload.get("query", "")).strip()
    except (Project.DoesNotExist, json.JSONDecodeError):
        return _error("project not found or request body invalid", 404)
    coordinate = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[,， ]\s*(-?\d+(?:\.\d+)?)\s*", query)
    if coordinate:
        latitude, longitude = map(float, coordinate.groups())
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            return _error("coordinates out of WGS84 range")
        return JsonResponse({"candidates": [{"label": query, "address": "經緯度定位", "latitude": latitude, "longitude": longitude, "provider": "coordinates", "precision": "point", "raw_id": f"coordinates:{latitude},{longitude}"}]})
    if not query:
        return _error("query is required")
    if settings.GEOCODING_PROVIDER == "nominatim":
        from .integrations.geocoding import NominatimGeocodingProvider
        try:
            candidates = NominatimGeocodingProvider(settings.NOMINATIM_GEOCODING_URL).search(query)
        except Exception:
            return _error("搜尋服務暫時無法連線", 503)
        if candidates:
            return JsonResponse({"candidates": candidates[:10], "provider": "nominatim"})
        return _error("搜尋服務暫時無法連線", 503)
    if settings.GEOCODING_PROVIDER == "nlsc":
        from .integrations.geocoding import NlscGeocodingProvider
        try:
            candidates = NlscGeocodingProvider(settings.NLSC_GEOCODING_URL, settings.NLSC_API_TOKEN).search(query)
        except Exception:
            candidates = []
        if candidates:
            return JsonResponse({"candidates": candidates[:10], "provider": "nlsc"})
    if settings.GEOCODING_PROVIDER == "tgos":
        from .integrations.geocoding import TgosGeocodingProvider
        try:
            candidates = TgosGeocodingProvider(settings.TGOS_API_KEY, settings.TGOS_GEOCODING_URL).search(query)
        except Exception:
            candidates = []
        if candidates:
            return JsonResponse({"candidates": candidates[:10], "provider": "tgos"})
    return JsonResponse({"candidates": [{"label": f"{query}（示範候選）", "address": "未設定正式地理編碼服務", "latitude": project.center_latitude, "longitude": project.center_longitude, "provider": "mock", "precision": "project_center", "raw_id": f"mock:{project.slug}"}], "notice": "目前使用明確標示的示範搜尋結果；請選取後再保存。"})


def _distance_km(latitude, longitude, other_latitude, other_longitude):
    dlat = math.radians(other_latitude - latitude)
    dlon = math.radians(other_longitude - longitude)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(latitude)) * math.cos(math.radians(other_latitude)) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(a))


def project_nearby(request, slug):
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    if project.search_center_latitude is None:
        return _error("search center not set", 409)
    limit, limit_error = _bounded_int(request, "limit", 100, 200)
    if limit_error:
        return limit_error
    kind = request.GET.get("kind", "").strip()
    results = []
    for station in StationSeed.objects.all():
        if kind and station.kind != kind:
            continue
        distance = _distance_km(project.search_center_latitude, project.search_center_longitude, station.latitude, station.longitude)
        if distance <= project.search_radius_km:
            results.append({"kind": station.kind, "external_id": station.external_id, "name": station.name, "latitude": station.latitude, "longitude": station.longitude, "distance_km": round(distance, 3), "source": station.properties.get("source", "platform") if isinstance(station.properties, dict) else "platform", "status": station.properties.get("status", "snapshot") if isinstance(station.properties, dict) else "snapshot", "observed_at": station.properties.get("observed_at") if isinstance(station.properties, dict) else None, "properties": station.properties})
    results.sort(key=lambda item: item["distance_km"])
    return JsonResponse({"project": project.slug, "radius_km": project.search_radius_km, "results": results[:limit]})


def layer_detail(request, slug):
    try:
        layer = LayerCatalog.objects.get(slug=slug, format__in=("geojson", "spatial"))
    except LayerCatalog.DoesNotExist:
        return _error("layer not found", 404)
    metadata = layer.metadata if isinstance(layer.metadata, dict) else {}
    spatial = metadata.get("spatial", {}) if isinstance(metadata.get("spatial", {}), dict) else {}
    if _layer_is_spatial_indexed(layer):
        raw_bbox = request.GET.get("bbox", "")
        try:
            bbox = tuple(float(value) for value in raw_bbox.split(","))
            if len(bbox) != 4:
                raise ValueError
            min_x, min_y, max_x, max_y = bbox
            if not (-180 <= min_x < max_x <= 180 and -90 <= min_y < max_y <= 90):
                raise ValueError
        except (TypeError, ValueError):
            return _error("bbox must be min_lng,min_lat,max_lng,max_lat")
        limit, limit_error = _bounded_int(request, "limit", 500, 1000)
        if limit_error:
            return limit_error
        index_path = _layer_health_path(layer)
        if not index_path.exists():
            return _error("spatial index unavailable", 503)
        try:
            features, quality = query_bbox(index_path, layer.slug, bbox, limit=limit,
                                           scan_budget=getattr(settings, "WORKSITE_SPATIAL_SCAN_BUDGET", 5000),
                                           with_quality=True)
        except (OSError, sqlite3.DatabaseError, ValueError, json.JSONDecodeError):
            return _error("spatial index unavailable", 503)
        return JsonResponse({"type": "FeatureCollection", "features": features, "data_quality": quality})
    output_candidates = [layer.path, metadata.get("output")]
    catalog_path = settings.BASE_DIR / "data" / "catalog" / "layers.json"
    if catalog_path.exists():
        try:
            catalog = json.loads(catalog_path.read_text(encoding="utf-8-sig"))
            catalog_output = catalog.get(slug, {}).get("output")
            output_candidates.append(catalog_output)
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    path = None
    for raw_path in output_candidates:
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        candidate = _catalog_path(raw_path)
        if candidate.exists():
            path = candidate
            break
    if path is None or not path.exists():
        return _error("layer output missing", 503)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _error("layer output unreadable", 503)
    return JsonResponse(payload)


def geologic_sensitive_viewport(request):
    authentication_error = _api_authentication_required(request)
    if authentication_error:
        return authentication_error

    raw_bbox = request.GET.get("bbox", "")
    try:
        values = tuple(float(value.strip()) for value in raw_bbox.split(","))
        if len(values) != 4 or not all(math.isfinite(value) for value in values):
            raise ValueError
        min_lng, min_lat, max_lng, max_lat = values
        if not (-180 <= min_lng <= 180 and -180 <= max_lng <= 180):
            raise ValueError
        if not (-90 <= min_lat <= 90 and -90 <= max_lat <= 90):
            raise ValueError
        if min_lng >= max_lng or min_lat >= max_lat:
            raise ValueError
    except (TypeError, ValueError):
        return _error("bbox must be min_lng,min_lat,max_lng,max_lat")

    raw_limit = request.GET.get("limit", "500")
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        return _error("limit must be a positive integer")
    if limit <= 0:
        return _error("limit must be a positive integer")
    if limit > 1000:
        return _error("limit must be no greater than 1000")

    category = request.GET.get("category")
    if category is not None and category not in {"L", "F", "G", "H"}:
        return _error("category must be one of L, F, G, H")

    index_path = Path(getattr(
        settings,
        "WORKSITE_GEOLOGIC_SENSITIVE_VIEWPORT_INDEX_PATH",
        settings.DATA_PROCESSED_DIR / "gis" / "hazards" / "geologic_sensitive_areas.sqlite3",
    ))
    if not index_path.exists():
        return _error("geologic sensitive viewport index unavailable", 503)
    try:
        features, quality = query_bbox_by_source_category(
            index_path,
            "geologic_sensitive_areas",
            values,
            category=category,
            limit=limit,
            scan_budget=getattr(settings, "WORKSITE_SPATIAL_SCAN_BUDGET", 5000),
            with_quality=True,
        )
    except (OSError, sqlite3.DatabaseError, ValueError, json.JSONDecodeError):
        return _error("geologic sensitive viewport index unavailable", 503)
    return JsonResponse({"type": "FeatureCollection", "features": features, "data_quality": quality})


def nearby(request):
    try:
        latitude = float(request.GET["lat"])
        longitude = float(request.GET["lng"])
        radius = float(request.GET.get("radius_km", "10"))
    except (KeyError, ValueError):
        return _error("lat, lng and radius_km must be numeric")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or not 0 < radius <= 100:
        return _error("coordinates or radius out of range")
    limit, limit_error = _bounded_int(request, "limit", 100, 200)
    if limit_error:
        return limit_error
    results = []
    for station in StationSeed.objects.all():
        dlat = math.radians(station.latitude - latitude)
        dlon = math.radians(station.longitude - longitude)
        a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(latitude)) * math.cos(math.radians(station.latitude)) * math.sin(dlon / 2) ** 2
        distance = 6371 * 2 * math.asin(math.sqrt(a))
        if distance <= radius:
            results.append({"kind": station.kind, "external_id": station.external_id, "name": station.name, "latitude": station.latitude, "longitude": station.longitude, "distance_km": round(distance, 3)})
    results.sort(key=lambda item: item["distance_km"])
    return JsonResponse({"results": results[:limit]})


def risk(request):
    try:
        latitude = float(request.GET["lat"])
        longitude = float(request.GET["lng"])
    except (KeyError, ValueError):
        return _error("lat and lng must be numeric")
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return _error("coordinates out of range")
    index_path = Path(settings.SPATIAL_INDEX_PATH)
    if not index_path.exists():
        return _error("spatial index unavailable", 503)
    try:
        indexed_matches = query_point(
            index_path, longitude, latitude, ("hazards", "protection")
        )
    except (OSError, sqlite3.DatabaseError, ValueError, json.JSONDecodeError):
        return _error("spatial index unavailable", 503)
    catalog = {
        layer.slug: layer
        for layer in LayerCatalog.objects.filter(
            slug__in={item["slug"] for item in indexed_matches}
        )
    }
    matches = []
    seen = set()
    for item in indexed_matches:
        if item["slug"] in seen:
            continue
        layer = catalog.get(item["slug"])
        if layer is None:
            continue
        seen.add(item["slug"])
        matches.append({
            "slug": layer.slug,
            "name": layer.name,
            "category": layer.category,
            "properties": item["properties"],
        })
    return JsonResponse({"latitude": latitude, "longitude": longitude, "layers": matches})




def _adapter_response(result, limit):
    return JsonResponse({"status": result.status, "source": result.source, "fetched_at": result.fetched_at.isoformat(), "stale": result.stale, "error": result.error, "records": result.records[:limit],
                         "runtime_state": result.runtime_state, "reason": result.reason,
                         "cache": result.cache, "retry_after_seconds": result.retry_after_seconds})


def weather(request):
    limit, error = _bounded_int(request, "limit", 100, 200)
    if error:
        return error
    adapter = CwaWeatherAdapter(settings.CWA_API_KEY, timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS)
    return _adapter_response(SnapshotCache(settings.EXTERNAL_API_CACHE_SECONDS).fetch(adapter), limit)


def project_train_status(request, slug):
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    if project.search_center_latitude is None or project.search_center_longitude is None:
        return _error("search center not set", 409)
    limit, error = _bounded_int(request, "limit", 100, 200)
    if error:
        return error
    adapter = TdxClient(settings.TDX_CLIENT_ID, settings.TDX_CLIENT_SECRET, timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS)
    adapter.kind = "train"
    result = SnapshotCache(settings.EXTERNAL_API_CACHE_SECONDS).fetch(adapter)
    if not result.records:
        return _adapter_response(result, limit)
    if not result.stale:
        try:
            adapter.enrich_train_positions(result.records, include_timetable=True)
        except Exception:
            # Keep the primary TrainLiveBoard result when enrichment is
            # temporarily unavailable, including a stale snapshot fallback.
            pass
    station_coordinates = _tra_station_coordinates()
    tdx_stations, _networks, _station_of_line, line_network = ({}, [], [], [])
    if not result.stale:
        tdx_stations, _networks, _station_of_line, line_network = _tdx_tra_metadata(
            settings.TDX_CLIENT_ID, settings.TDX_CLIENT_SECRET
        )
    line_network_pairs = {}
    for segment in line_network:
        start = str(segment.get("from_station_id") or "")
        end = str(segment.get("to_station_id") or "")
        if start and end:
            line_network_pairs[(start, end)] = segment
    for station in tdx_stations.values():
        if station.get("latitude") is not None and station.get("longitude") is not None:
            station_coordinates[str(station.get("station_name") or "").strip()] = (float(station["latitude"]), float(station["longitude"]))
    rail_geometries = _rail_geometries()
    route_station_index = {}
    for item in _station_of_line:
        station_id = str(item.get("station_id") or "")
        if station_id:
            route_station_index.setdefault(station_id, []).append(item)
    for record in result.records:
        live_coordinate_aligned = False
        record["upstream_position"] = (
            {"latitude": record["latitude"], "longitude": record["longitude"]}
            if record.get("latitude") is not None and record.get("longitude") is not None else None
        )
        record["position_freshness"] = "stale" if result.stale else "snapshot"
        if record.get("latitude") is not None and record.get("longitude") is not None:
            if _sanitize_train_coordinate(record, rail_geometries):
                live_coordinate_aligned = True
            elif not record.get("position_source") and record.get("latitude") is not None:
                record["position_source"] = "upstream_coordinate"
        station_name = str(record.get("station_name") or "").replace("臺鐵", "").replace("車站", "").strip()
        _apply_tdx_station_metadata(record, tdx_stations)
        route_matches = route_station_index.get(str(record.get("station_id")), [])
        if route_matches:
            route = route_matches[0]
            record["line_id"] = route.get("line_id")
            record["station_sequence"] = route.get("sequence")
            record["cumulative_distance"] = route.get("cumulative_distance")
        pair = (str(record.get("station_id") or ""), str(record.get("next_station_id") or ""))
        segment = line_network_pairs.get(pair)
        if segment:
            record["line_network_segment_id"] = segment.get("segment_id")
            record["line_network_line_id"] = segment.get("line_id")
            record["line_network_distance"] = segment.get("distance")
            record["line_network_verified"] = True
        station_name = str(record.get("station_name") or "").replace("?粹", "").replace("頠?", "").strip()
        coordinates = station_coordinates.get(station_name)
        if coordinates and not live_coordinate_aligned:
            record["latitude"], record["longitude"] = coordinates
            record["position_source"] = "station"
            record["position_quality"] = "medium"
        if line_network and record.get("next_station_id") and not segment:
            if not _attach_track_animation(record, station_coordinates, rail_geometries):
                record["position_source"] = "station_route_unverified" if coordinates else "route_unverified"
                record["position_quality"] = "low"
            continue
        if result.stale:
            continue
        estimated = _estimate_train_position_on_route(record, station_coordinates, rail_geometries, _train_progress_ratio(record))
        if estimated is None:
            estimated = _estimate_train_position(record, station_coordinates)
        if estimated:
            if not _attach_track_animation(record, station_coordinates, rail_geometries):
                _apply_estimated_position(record, estimated, rail_geometries, coordinates)
    records = []
    for record in result.records:
        try:
            latitude = float(record["latitude"])
            longitude = float(record["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        if _distance_km(project.search_center_latitude, project.search_center_longitude, latitude, longitude) <= 10:
            records.append(record)
    result.records[:] = records
    return _adapter_response(result, limit)


def _traffic_snapshot(adapter, kind, fetcher, ttl_seconds):
    return traffic_service._traffic_snapshot(
        adapter, kind, fetcher, ttl_seconds, snapshot_model=ApiSnapshot
    )


def project_highway_traffic(request, slug):
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    if project.search_center_latitude is None or project.search_center_longitude is None:
        return _error("search center not set", 409)
    limit, error = _bounded_int(request, "limit", 300, 300)
    if error:
        return error
    adapter = TdxClient(settings.TDX_CLIENT_ID, settings.TDX_CLIENT_SECRET, timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS)
    payload = traffic_service.get_highway_traffic(
        project=project,
        limit=limit,
        adapter=adapter,
        snapshot_model=ApiSnapshot,
        live_ttl_seconds=settings.EXTERNAL_API_CACHE_SECONDS,
        snapshot_func=_traffic_snapshot,
    )
    return JsonResponse({
        "type": "FeatureCollection",
        **{key: value.isoformat() if key == "fetched_at" and value is not None else value for key, value in payload.items()},
    })


def highway_facility_tracks(request):
    try:
        road_class = min(max(int(request.GET.get("road_class", "3")), 0), 9)
    except (TypeError, ValueError):
        return _error("road_class must be numeric")
    limit, error = _bounded_int(request, "limit", 200, 200)
    if error:
        return error
    query = request.GET.get("q", "").strip().casefold()
    city = request.GET.get("city", "").strip()
    adapter = TdxClient(settings.TDX_CLIENT_ID, settings.TDX_CLIENT_SECRET, timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS)
    payload = traffic_service.get_facility_tracks(
        road_class=road_class,
        query=query,
        city=city,
        limit=limit,
        adapter=adapter,
        snapshot_model=ApiSnapshot,
        snapshot_func=_traffic_snapshot,
    )
    return JsonResponse({
        **{key: value.isoformat() if key == "fetched_at" and value is not None else value for key, value in payload.items()},
    })


def project_cctv(request, slug):
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    if project.search_center_latitude is None:
        return _error("search center not set", 409)
    limit, error = _bounded_int(request, "limit", 100, 200)
    if error:
        return error
    payload = cctv_service.get_project_cctv(
        project=project,
        limit=limit,
        camera_model=CctvCamera,
        station_model=StationSeed,
        adapter_class=TdxClient,
        cache_class=SnapshotCache,
        distance_func=_distance_km,
        settings_obj=settings,
    )
    payload["fetched_at"] = payload["fetched_at"].isoformat()
    return JsonResponse(payload)


def project_weather_stations(request, slug):
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    if project.search_center_latitude is None:
        return _error("search center not set", 409)
    limit, error = _bounded_int(request, "limit", 50, 200)
    if error:
        return error
    payload = weather_service.get_project_weather_stations(
        project=project,
        limit=limit,
        adapter_class=CwaWeatherAdapter,
        cache_class=SnapshotCache,
        distance_func=_distance_km,
        settings_obj=settings,
    )
    payload["fetched_at"] = payload["fetched_at"].isoformat()
    return JsonResponse(payload, status=503 if payload["status"] == "error" else 200)


def project_dynamic_flood(request, slug):
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    try:
        region, hours = dynamic_flood.request_scope(request.GET)
    except ValueError as exc:
        return _error(str(exc))
    result = dynamic_flood.get_forecast(region, hours)
    batch = result.batch
    coverage = dynamic_flood.covers_project(batch, project) if batch else None
    if coverage is False:
        result = dynamic_flood.FloodResult(
            "unverified", result.fetched_at, reason="project_outside_source_extent"
        )
        batch = None
    return JsonResponse({
        "status": result.status,
        "source": "wra",
        "fetched_at": result.fetched_at.isoformat() if result.fetched_at else None,
        "source_time": batch.source_time if batch else None,
        "error": result.reason if not batch else "",
        "reason": result.reason or None,
        "freshness": result.freshness,
        "region": region,
        "forecast_hours": batch.hours if batch else hours,
        "requested_forecast_hours": hours,
        "project_coverage": "inside_extent" if coverage else "outside_extent" if coverage is False else "unknown",
        "overlay": batch.overlay(slug) if batch else None,
    }, status=200 if batch or result.status in ("disabled", "empty") else 503)


def project_dynamic_flood_image(request, slug):
    try:
        project = Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    try:
        region, hours = dynamic_flood.request_scope(request.GET)
    except ValueError as exc:
        return _error(str(exc))
    batch_id = request.GET.get("batch", "")
    batch = None
    if dynamic_flood.enabled() and re.fullmatch(r"[0-9a-f]{64}", batch_id):
        batch = dynamic_flood.get_batch(batch_id, region, hours)
    if batch is None or dynamic_flood.covers_project(batch, project) is False:
        return _error("WRA raster batch unavailable", 404)
    response = HttpResponse(batch.content, content_type="image/png")
    response["X-WORKSITE-WRA-Batch"] = batch.batch_id
    response["Cache-Control"] = "private, no-store"
    return response


def project_radar(request, slug):
    try:
        Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return _error("project not found", 404)
    adapter = CwaRadarAdapter(settings.CWA_API_KEY, timeout=settings.EXTERNAL_API_TIMEOUT_SECONDS)
    result = SnapshotCache(300).fetch(adapter)
    overlay = result.records[0] if result.records else None
    return JsonResponse({
        "status": result.status,
        "source": result.source,
        "fetched_at": result.fetched_at.isoformat(),
        "stale": result.stale,
        "error": result.error,
        "overlay": overlay,
    }, status=503 if result.status == "error" and overlay is None else 200)


def _offline_tile_candidates(z, x, y):
    roots = list(settings.OFFLINE_TILE_ROOT.glob("**/CH_3857_Vect_OpenData/CH_3857_Vect_OpenData"))
    if not roots:
        roots = [
            settings.OFFLINE_TILE_ROOT
            / "CH_3857_Vect_OpenData"
            / "CH_3857_Vect_OpenData"
        ]
    seen = set()
    for root in roots:
        for row in (y, (2**z - 1) - y):
            tile = root / f"L{z:02d}" / f"R{row:08x}" / f"C{x:08x}.jpg"
            if tile not in seen:
                seen.add(tile)
                yield tile


def offline_tile(request, z, x, y):
    """Serve the retained TGOS vector tile pyramid as Leaflet XYZ tiles."""
    if not (0 <= z <= 20 and 0 <= x < 2**z and 0 <= y < 2**z):
        return _error("tile coordinates out of range", 404)
    for tile in _offline_tile_candidates(z, x, y):
        if tile.exists():
            return FileResponse(tile.open("rb"), content_type="image/jpeg")
    return _error("tile not found", 404)
