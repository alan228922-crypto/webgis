def get_project_weather_stations(
    *,
    project,
    limit,
    adapter_class,
    cache_class,
    distance_func,
    settings_obj,
):
    adapter = adapter_class(
        settings_obj.CWA_API_KEY,
        timeout=settings_obj.EXTERNAL_API_TIMEOUT_SECONDS,
    )
    result = cache_class(settings_obj.EXTERNAL_API_CACHE_SECONDS).fetch(adapter)
    records = []
    for station in result.records:
        try:
            latitude = float(station["latitude"])
            longitude = float(station["longitude"])
        except (KeyError, TypeError, ValueError):
            continue
        distance = distance_func(
            project.search_center_latitude,
            project.search_center_longitude,
            latitude,
            longitude,
        )
        if distance <= project.search_radius_km:
            records.append({
                "external_id": station.get("station_id"),
                "name": station.get("name") or station.get("station_id"),
                "latitude": latitude,
                "longitude": longitude,
                "distance_km": round(distance, 3),
                "observed_at": station.get("observed_at"),
                "temperature": station.get("temperature"),
                "humidity": station.get("humidity"),
                "wind_speed": station.get("wind_speed"),
                "precipitation": station.get("precipitation"),
                "source": station.get("source", result.source),
                "status": station.get("status", "observed"),
            })
    records.sort(key=lambda item: item["distance_km"])
    return {
        "status": result.status,
        "source": result.source,
        "fetched_at": result.fetched_at,
        "stale": result.stale,
        "error": result.error,
        "radius_km": project.search_radius_km,
        "records": records[:limit],
    }
