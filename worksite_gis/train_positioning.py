from django.utils import timezone
from shapely.geometry import Point


def _project_point_to_rail(point, rail_geometries):
    """Snap a (lat, lon) estimate to the closest local rail LineString."""
    if not point or not rail_geometries:
        return None
    candidate = Point(float(point[1]), float(point[0]))
    nearest = min(rail_geometries, key=lambda line: line.distance(candidate), default=None)
    if nearest is None:
        return None
    snapped = nearest.interpolate(nearest.project(candidate))
    return float(snapped.y), float(snapped.x)


def _align_train_position(record, rail_geometries, max_distance=0.0025):
    """Correct a live or estimated train coordinate to the nearest rail."""
    try:
        latitude = float(record.get("latitude"))
        longitude = float(record.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not rail_geometries:
        return None
    point = Point(longitude, latitude)
    nearest = min(rail_geometries, key=lambda line: line.distance(point), default=None)
    if nearest is None or nearest.distance(point) > max_distance:
        return None
    snapped = nearest.interpolate(nearest.project(point))
    return float(snapped.y), float(snapped.x)


def _sanitize_train_coordinate(record, rail_geometries):
    """Keep a live coordinate only when it is plausibly on a TRA track."""
    try:
        raw_latitude = float(record.get("latitude"))
        raw_longitude = float(record.get("longitude"))
    except (TypeError, ValueError):
        return False
    record["raw_latitude"] = raw_latitude
    record["raw_longitude"] = raw_longitude
    aligned = _align_train_position({"latitude": raw_latitude, "longitude": raw_longitude}, rail_geometries)
    if not aligned:
        record["latitude"] = None
        record["longitude"] = None
        record["position_source"] = "invalid_live_coordinate"
        record["position_quality"] = "low"
        return False
    record["latitude"], record["longitude"] = aligned
    record["position_source"] = "track_live"
    record["position_quality"] = "high"
    return True


def _apply_tdx_station_metadata(record, stations):
    current = stations.get(str(record.get("station_id") or ""))
    following = stations.get(str(record.get("next_station_id") or ""))
    if current and current.get("station_name"):
        record["station_name"] = current["station_name"]
    if following and following.get("station_name"):
        record["next_station_name"] = following["station_name"]
    return record


def _apply_estimated_position(record, estimated, rail_geometries, fallback=None):
    snapped = _align_train_position({"latitude": estimated[0], "longitude": estimated[1]}, rail_geometries)
    if snapped:
        record["latitude"], record["longitude"] = snapped
        record["position_source"] = "track_estimate"
        record["position_quality"] = "medium"
        return True
    if fallback:
        record["latitude"], record["longitude"] = fallback
        record["position_source"] = "station_route_unverified"
        record["position_quality"] = "low"
    else:
        record["latitude"] = None
        record["longitude"] = None
        record["position_source"] = "route_unverified"
        record["position_quality"] = "low"
    return False


def _clock_seconds(value):
    try:
        parts = [int(item) for item in str(value).split(":")]
    except (TypeError, ValueError):
        return None
    if len(parts) not in (2, 3):
        return None
    hour, minute = parts[:2]
    second = parts[2] if len(parts) == 3 else 0
    return hour * 3600 + minute * 60 + second


def _train_segment_duration_seconds(record):
    departure = _clock_seconds(record.get("scheduled_departure"))
    arrival = _clock_seconds(record.get("scheduled_arrival_next"))
    if departure is None or arrival is None:
        return 60
    duration = arrival - departure
    if duration <= 0:
        duration += 24 * 60 * 60
    return max(1, duration)


def _estimate_train_position(record, station_coordinates, now=None):
    current_name = str(record.get("station_name") or "").replace("臺鐵", "").replace("車站", "").strip()
    next_name = str(record.get("next_station_name") or "").replace("臺鐵", "").replace("車站", "").strip()
    current = station_coordinates.get(current_name)
    following = station_coordinates.get(next_name)
    if not current or not following:
        return None
    departure = _clock_seconds(record.get("scheduled_departure"))
    arrival = _clock_seconds(record.get("scheduled_arrival_next"))
    if departure is None or arrival is None or arrival <= departure:
        ratio = 0.5
    else:
        now = now or timezone.localtime()
        current_seconds = now.hour * 3600 + now.minute * 60 + now.second
        ratio = max(0.0, min(1.0, (current_seconds - departure - float(record.get("delay_minutes") or 0) * 60) / (arrival - departure)))
    latitude = current[0] + (following[0] - current[0]) * ratio
    longitude = current[1] + (following[1] - current[1]) * ratio
    return latitude, longitude


def _estimate_train_position_on_route(record, station_coordinates, rail_geometries, ratio=0.5):
    """Interpolate along the rail LineString nearest to both station endpoints."""
    current_name = str(record.get("station_name") or "").replace("臺鐵", "").replace("車站", "").strip()
    next_name = str(record.get("next_station_name") or "").replace("臺鐵", "").replace("車站", "").strip()
    current = station_coordinates.get(current_name)
    following = station_coordinates.get(next_name)
    if not current or not following or not rail_geometries:
        return None
    start = Point(float(current[1]), float(current[0]))
    end = Point(float(following[1]), float(following[0]))
    line = min(rail_geometries, key=lambda candidate: candidate.distance(start) + candidate.distance(end), default=None)
    if line is None:
        return None
    start_distance = line.project(start)
    end_distance = line.project(end)
    distance = start_distance + (end_distance - start_distance) * max(0.0, min(1.0, float(ratio)))
    point = line.interpolate(distance)
    return float(point.y), float(point.x)


def _route_track_path(record, station_coordinates, rail_geometries, samples=32):
    """Return sampled [lat, lon] coordinates along the station-to-station rail segment."""
    current_name = str(record.get("station_name") or "").replace("臺鐵", "").replace("車站", "").strip()
    next_name = str(record.get("next_station_name") or "").replace("臺鐵", "").replace("車站", "").strip()
    current = station_coordinates.get(current_name)
    following = station_coordinates.get(next_name)
    if not current or not following or not rail_geometries or samples < 2:
        return []
    start = Point(float(current[1]), float(current[0]))
    end = Point(float(following[1]), float(following[0]))
    line = min(rail_geometries, key=lambda candidate: candidate.distance(start) + candidate.distance(end), default=None)
    if line is None:
        return []
    start_distance = line.project(start)
    end_distance = line.project(end)
    return [
        [float(point.y), float(point.x)]
        for point in (line.interpolate(start_distance + (end_distance - start_distance) * index / (samples - 1)) for index in range(samples))
    ]


def _attach_track_animation(record, station_coordinates, rail_geometries, samples=32, now=None):
    """Attach a sampled rail path using timetable time progress.

    TDX data in this deployment has no GPS feed, so even coordinate fields
    returned by a live board must not override the timetable estimate.
    """
    path = _route_track_path(record, station_coordinates, rail_geometries, samples=samples)
    if len(path) < 2:
        return False
    progress = _train_progress_ratio(record, now=now)
    point = path[min(len(path) - 1, int(progress * (len(path) - 1)))]
    record["latitude"], record["longitude"] = point
    record["position_source"] = "track_estimate"
    record["position_quality"] = "medium"
    record["track_path"] = path
    record["track_progress"] = progress
    record["segment_duration_seconds"] = _train_segment_duration_seconds(record)
    return True


def _train_progress_ratio(record, now=None):
    departure = _clock_seconds(record.get("scheduled_departure"))
    arrival = _clock_seconds(record.get("scheduled_arrival_next"))
    if departure is None or arrival is None:
        return 0.5
    now = now or timezone.localtime()
    current_seconds = now.hour * 3600 + now.minute * 60 + now.second
    duration = _train_segment_duration_seconds(record)
    if arrival <= departure and current_seconds < departure:
        current_seconds += 24 * 60 * 60
    return max(0.0, min(1.0, (current_seconds - departure - float(record.get("delay_minutes") or 0) * 60) / duration))
