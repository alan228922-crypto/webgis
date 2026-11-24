def _camera_record(camera, distance):
    stream_url = camera.stream_url
    return {
        "cctv_id": camera.camera_id,
        "name": camera.location_name or camera.road_name or camera.camera_id,
        "road_name": camera.road_name,
        "location_name": camera.location_name,
        "city": camera.city,
        "latitude": camera.latitude,
        "longitude": camera.longitude,
        "stream_url": stream_url,
        "media_type": camera.media_type,
        "source": camera.provider,
        "status": "snapshot",
        "connection_status": "unverified" if stream_url else "offline",
        "observed_at": None,
        "imported_at": camera.imported_at.isoformat(),
        "distance_km": round(distance, 3),
    }


def _station_record(station, distance):
    props = station.properties if isinstance(station.properties, dict) else {}
    stream_url = props.get("\u5373\u6642\u5f71\u50cf\u7db2\u5740") or props.get("stream_url") or props.get("\u5f71\u50cf\u7db2\u5740") or ""
    stream_path = stream_url.lower().split("?")[0]
    media_type = "image" if stream_url and (
        "cctv-ss" in stream_url.lower()
        or any(stream_path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".mjpg", ".mjpeg"))
    ) else ("hls" if stream_path.endswith(".m3u8") else ("external" if stream_url else ""))
    return {
        "cctv_id": station.external_id,
        "name": station.name or station.external_id,
        "road_name": props.get("\u9053\u8def\u540d\u7a31", ""),
        "location_name": station.name,
        "city": props.get("\u7e23\u5e02") or props.get("CCTV_Counties_and_cities", ""),
        "latitude": station.latitude,
        "longitude": station.longitude,
        "stream_url": stream_url,
        "media_type": media_type,
        "source": props.get("source", "local catalog"),
        "status": "snapshot",
        "connection_status": "unverified" if stream_url else "offline",
        "observed_at": props.get("observed_at"),
        "distance_km": round(distance, 3),
    }


def _tdx_record(item, distance):
    stream_url = item.get("stream_url") or item.get("image_url") or ""
    return {
        **item,
        "stream_url": stream_url,
        "city": item.get("city", ""),
        "connection_status": "unverified" if stream_url else "offline",
        "distance_km": round(distance, 3),
    }


def get_project_cctv(
    *,
    project,
    limit,
    camera_model,
    station_model,
    adapter_class,
    cache_class,
    distance_func,
    settings_obj,
):
    cctv_radius_km = 2
    records = []
    latitude = project.search_center_latitude
    longitude = project.search_center_longitude
    for camera in camera_model.objects.filter(
        latitude__range=(latitude - 0.12, latitude + 0.12),
        longitude__range=(longitude - 0.12, longitude + 0.12),
    ):
        distance = distance_func(latitude, longitude, camera.latitude, camera.longitude)
        if distance <= cctv_radius_km:
            records.append(_camera_record(camera, distance))

    existing_ids = {item["cctv_id"] for item in records}
    for station in station_model.objects.filter(kind="cctv"):
        if station.external_id in existing_ids:
            continue
        distance = distance_func(latitude, longitude, station.latitude, station.longitude)
        if distance <= cctv_radius_km:
            records.append(_station_record(station, distance))

    adapter = adapter_class(
        settings_obj.TDX_CLIENT_ID,
        settings_obj.TDX_CLIENT_SECRET,
        timeout=settings_obj.EXTERNAL_API_TIMEOUT_SECONDS,
    )
    adapter.kind = "cctv"
    result = cache_class(settings_obj.EXTERNAL_API_CACHE_SECONDS).fetch(adapter)
    if getattr(settings_obj, "WORKSITE_DEMO", False):
        records = []
    for item in result.records:
        try:
            item_latitude, item_longitude = float(item["latitude"]), float(item["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        distance = distance_func(latitude, longitude, item_latitude, item_longitude)
        if distance <= cctv_radius_km:
            records.append(_tdx_record(item, distance))

    merged = {}
    for item in records:
        key = (round(item["latitude"], 5), round(item["longitude"], 5))
        previous = merged.get(key)
        if previous is None or (not previous.get("stream_url") and item.get("stream_url")):
            merged[key] = item
    records = list(merged.values())
    records.sort(key=lambda item: item["distance_km"])
    return {
        "status": result.status,
        "source": result.source,
        "fetched_at": result.fetched_at,
        "runtime_state": result.runtime_state,
        "reason": result.reason,
        "retry_after_seconds": result.retry_after_seconds,
        "cache": result.cache,
        "stale": result.stale,
        "error": result.error,
        "radius_km": cctv_radius_km,
        "records": records[:limit],
    }
