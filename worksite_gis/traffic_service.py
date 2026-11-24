from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .integrations.tdx_transport import TdxError, classify_tdx_error
from shapely import wkt
from shapely.errors import GEOSException
from shapely.geometry import Point, mapping


def _traffic_snapshot(adapter, kind, fetcher, ttl_seconds, *, snapshot_model):
    now = timezone.now()
    configuration_error = getattr(adapter, "configuration_error", None)
    if configuration_error:
        return [], now, False, configuration_error
    current = snapshot_model.objects.filter(
        service="tdx", kind=kind, expires_at__gt=now, status="ok"
    ).first()
    if current:
        return current.payload, current.fetched_at, False, ""
    try:
        fetched = fetcher()
        if hasattr(fetched, "records"):
            if fetched.status != "ok":
                raise TdxError(getattr(fetched, "reason", None) or "upstream_unavailable")
            records = fetched.records
        else:
            records = fetched
        snapshot_model.objects.create(
            service="tdx",
            kind=kind,
            fetched_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            payload=records,
            status="ok",
        )
        return records, now, False, ""
    except Exception as exc:
        fallback = snapshot_model.objects.filter(
            service="tdx", kind=kind, status="ok", fetched_at__lte=now,
            fetched_at__gte=now - timedelta(seconds=(
                getattr(settings, "TDX_STALE_MAX_SECONDS", 600) if kind.endswith("_live") else 172800
            )),
        ).first()
        reason = "upstream_unavailable" if type(exc) is RuntimeError else str(classify_tdx_error(exc))
        if fallback:
            return fallback.payload, fallback.fetched_at, True, reason
        return [], now, False, reason


def _snapshot(fetcher, adapter, kind, ttl_seconds, *, snapshot_model, snapshot_func):
    if snapshot_func:
        return snapshot_func(adapter, kind, fetcher, ttl_seconds)
    return _traffic_snapshot(
        adapter,
        kind,
        fetcher,
        ttl_seconds,
        snapshot_model=snapshot_model,
    )


def get_highway_traffic(
    *,
    project,
    limit,
    adapter,
    snapshot_model,
    live_ttl_seconds,
    snapshot_func=None,
):
    center = Point(float(project.search_center_longitude), float(project.search_center_latitude))
    search_area = center.buffer(float(project.search_radius_km or 10) / 111.0)
    sources = [
        ("highway", "highway_shapes", adapter.fetch_highway_section_shapes, "highway_live", adapter.fetch_highway_live, "highway_sections", adapter.fetch_highway_sections),
        ("freeway", "freeway_shapes", adapter.fetch_freeway_section_shapes, "freeway_live", adapter.fetch_freeway_live, "freeway_sections", adapter.fetch_freeway_sections),
    ]
    features = []
    errors, fetched_times, stale_flags = [], [], []
    for road_type, shape_kind, shape_fetcher, live_kind, live_fetcher, section_kind, section_fetcher in sources:
        shapes, shape_time, shape_stale, shape_error = _snapshot(
            shape_fetcher, adapter, shape_kind, 86400,
            snapshot_model=snapshot_model, snapshot_func=snapshot_func,
        )
        live, live_time, live_stale, live_error = _snapshot(
            live_fetcher, adapter, live_kind, live_ttl_seconds,
            snapshot_model=snapshot_model, snapshot_func=snapshot_func,
        )
        sections, section_time, section_stale, section_error = _snapshot(
            section_fetcher, adapter, section_kind, 86400,
            snapshot_model=snapshot_model, snapshot_func=snapshot_func,
        )
        if not live_error or live_stale:
            fetched_times.append(live_time)
        stale_flags.extend((shape_stale, live_stale, section_stale))
        errors.extend(error for error in (shape_error, live_error, section_error) if error)
        section_by_id = {str(item.get("section_id")): item for item in sections if item.get("section_id")}
        live_by_section = {str(item.get("section_id")): item for item in live if item.get("section_id")}
        for item in shapes:
            section_id = str(item.get("section_id") or "")
            status = live_by_section.get(section_id)
            if not status:
                continue
            try:
                geometry = wkt.loads(item.get("geometry", ""))
            except (TypeError, ValueError, GEOSException):
                continue
            if not geometry.intersects(search_area):
                continue
            metadata = section_by_id.get(section_id, {})
            properties = {key: value for key, value in status.items() if key != "section_id"}
            properties.update({
                "section_id": section_id,
                "road_type": road_type,
                "road_name": metadata.get("road_name"),
                "direction": metadata.get("direction"),
                "section_name": metadata.get("section_name"),
            })
            features.append({
                "type": "Feature",
                "geometry": mapping(geometry),
                "properties": properties,
            })
    return {
        "status": "ok" if not errors else "degraded",
        "stale": any(stale_flags),
        "fetched_at": max(fetched_times, default=None),
        "runtime_state": ("stale" if any(stale_flags) else "available" if features else
                          (errors[0] if errors[0] in {"disabled", "not_configured"} else "unavailable")
                          if errors else "empty"),
        "radius_km": project.search_radius_km or 10,
        "error": errors[0] if errors else "",
        "features": features[:limit],
    }


def get_facility_tracks(
    *,
    road_class,
    query,
    city,
    limit,
    adapter,
    snapshot_model,
    snapshot_func=None,
):
    records, fetched_at, stale, fetch_error = _snapshot(
        lambda: adapter.fetch_highway_facility_tracks(road_class),
        adapter,
        f"highway_facility_{road_class}",
        86400,
        snapshot_model=snapshot_model,
        snapshot_func=snapshot_func,
    )
    filtered = []
    for item in records:
        cities = item.get("cities") or []
        if query and query not in str(item.get("road_name", "")).casefold():
            continue
        if city and not any(
            city in str(c.get("name", "")) or city == str(c.get("code", ""))
            for c in cities
        ):
            continue
        filtered.append(item)
    return {
        "status": "ok" if not fetch_error else "degraded",
        "runtime_state": ("stale" if stale else "available" if filtered else
                          (fetch_error if fetch_error in {"disabled", "not_configured"} else "unavailable")
                          if fetch_error else "empty"),
        "road_class": road_class,
        "stale": stale,
        "fetched_at": fetched_at,
        "error": fetch_error,
        "records": filtered[:limit],
    }
