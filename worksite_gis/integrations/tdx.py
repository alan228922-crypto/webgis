from __future__ import annotations

import httpx
from django.conf import settings
from django.utils import timezone

from .base import AdapterResult
from .tdx_normalizers import _items, _localized, _train_type_name
from .tdx_transport import TdxTransport, classify_tdx_error


class TdxClient:
    source = "tdx"
    token_url = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
    highway_section_shape_url = "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/SectionShape/Highway"
    freeway_section_shape_url = "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/SectionShape/Freeway"

    def __init__(self, client_id: str, client_secret: str, client=None, timeout: float = 10):
        self.client_id = client_id
        self.client_secret = client_secret
        self.client = client or httpx.Client(timeout=timeout, trust_env=False)
        self._transport = TdxTransport(client_id, client_secret, client=self.client, timeout=timeout)
        self._token = ""
        self._token_expires_at = None

    @property
    def _token(self):
        return self._transport._token

    @_token.setter
    def _token(self, value):
        self._transport._token = value

    @property
    def _token_expires_at(self):
        return self._transport._token_expires_at

    @_token_expires_at.setter
    def _token_expires_at(self, value):
        self._transport._token_expires_at = value

    @property
    def configuration_error(self):
        return self._transport.configuration_error

    def _failure(self, fetched_at, exc):
        failure = classify_tdx_error(exc)
        return AdapterResult("error", self.source, fetched_at, [], False, str(failure),
                             reason=str(failure), retry_after_seconds=failure.retry_after_seconds)

    def get_token(self) -> str:
        return self._transport.get_token()

    def _get(self, url, params=None):
        return self._transport.get(url, params=params)

    def fetch_highway_section_shapes(self):
        payload = self._get(getattr(settings, "TDX_HIGHWAY_SECTION_SHAPE_URL", self.highway_section_shape_url))
        items = self._items(payload, "SectionShapes", "SectionShape")
        return [
            {"section_id": item.get("SectionID"), "geometry": item.get("Geometry")}
            for item in items
            if item.get("SectionID") and item.get("Geometry")
        ]

    def fetch_freeway_section_shapes(self):
        payload = self._get(getattr(settings, "TDX_FREEWAY_SECTION_SHAPE_URL", self.freeway_section_shape_url))
        items = self._items(payload, "SectionShapes", "SectionShape")
        return [{"section_id": item.get("SectionID"), "geometry": item.get("Geometry")} for item in items if item.get("SectionID") and item.get("Geometry")]

    def fetch_highway_facility_tracks(self, road_class=3):
        url = getattr(settings, "TDX_HIGHWAY_FACILITY_TRACK_URL", "https://tdx.transportdata.tw/api/basic/V3/Map/Basic/Road/Facility/Track/RoadClass/{road_class}").format(road_class=int(road_class))
        payload = self._get(url)
        items = self._items(payload, "Roads", "RoadFacilities", "RoadFacilityTracks")
        return [{
            "road_name": item.get("RoadName"),
            "road_class": int(road_class),
            "cities": [{"name": city.get("CityName"), "code": city.get("City")} for city in (item.get("CityList") or []) if city.get("CityName")],
        } for item in items if item.get("RoadName")]

    def fetch_highway_sections(self):
        payload = self._get(getattr(settings, "TDX_HIGHWAY_SECTION_URL", "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/Section/Highway"))
        items = self._items(payload, "Sections", "Section", "HighwaySections")
        return [{
            "section_id": item.get("SectionID"),
            "road_name": item.get("RoadName"),
            "direction": item.get("RoadDirection"),
            "section_name": item.get("SectionName"),
        } for item in items if item.get("SectionID")]

    def fetch_freeway_sections(self):
        payload = self._get(getattr(settings, "TDX_FREEWAY_SECTION_URL", "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/Section/Freeway"))
        items = self._items(payload, "Sections", "Section", "FreewaySections")
        return [{
            "section_id": item.get("SectionID"),
            "road_name": item.get("RoadName"),
            "direction": item.get("RoadDirection"),
            "section_name": item.get("SectionName"),
        } for item in items if item.get("SectionID")]

    def fetch_highway_live(self) -> AdapterResult:
        self.kind = "highway_live"
        fetched_at = timezone.now()
        try:
            payload = self._get("https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/Live/Highway")
            items = self._items(payload, "LiveTraffics", "Highways", "Highway", "RoadTraffic")
            records = [{
                "section_id": item.get("SectionID"),
                "travel_time": item.get("TravelTime"),
                "travel_speed": item.get("TravelSpeed"),
                "congestion_level_id": item.get("CongestionLevelID"),
                "congestion_level": item.get("CongestionLevel"),
                "data_collect_time": item.get("DataCollectTime"),
            } for item in items if item.get("SectionID")]
            return AdapterResult("ok", self.source, fetched_at, records)
        except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as exc:
            return self._failure(fetched_at, exc)

    def fetch_freeway_live(self) -> AdapterResult:
        self.kind = "freeway_live"
        fetched_at = timezone.now()
        try:
            payload = self._get(getattr(settings, "TDX_FREEWAY_LIVE_URL", "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/Live/Freeway"))
            items = self._items(payload, "LiveTraffics", "Freeways", "Freeway", "RoadTraffic")
            records = [{
                "section_id": item.get("SectionID"),
                "travel_time": item.get("TravelTime"),
                "travel_speed": item.get("TravelSpeed"),
                "congestion_level_id": item.get("CongestionLevelID"),
                "congestion_level": item.get("CongestionLevel"),
                "data_collect_time": item.get("DataCollectTime"),
            } for item in items if item.get("SectionID")]
            return AdapterResult("ok", self.source, fetched_at, records)
        except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as exc:
            return self._failure(fetched_at, exc)

    @staticmethod
    def _items(payload, *keys):
        items = _items(payload, *keys)
        if not isinstance(payload, list) and not (
            isinstance(payload, dict) and any(isinstance(payload.get(key), list) for key in keys)
        ):
            raise ValueError("unsupported TDX response")
        return items

    def fetch_tra_stations(self):
        payload = self._get(getattr(settings, "TDX_TRA_STATION_URL", ""))
        items = self._items(payload, "Stations", "Station")
        result = []
        for item in items:
            position = item.get("StationPosition") or item.get("Position") or {}
            result.append({
                "station_id": item.get("StationID") or item.get("StationCode"),
                "station_name": self._localized(item.get("StationName")),
                "latitude": position.get("PositionLat") or position.get("Latitude"),
                "longitude": position.get("PositionLon") or position.get("Longitude"),
                "city_code": item.get("CityCode") or item.get("CountyCode"),
                "town_code": item.get("TownCode"),
            })
        return result

    def fetch_tra_network(self):
        payload = self._get(getattr(settings, "TDX_TRA_NETWORK_URL", ""))
        items = self._items(payload, "Networks", "Network")
        result = []
        for item in items:
            result.append({
                "network_id": item.get("NetworkID") or item.get("NetworkCode"),
                "network_name": self._localized(item.get("NetworkName")),
                "lines": item.get("Lines") or item.get("Line") or [],
            })
        return result

    def fetch_tra_station_of_line(self):
        payload = self._get(getattr(settings, "TDX_TRA_STATION_OF_LINE_URL", ""))
        items = self._items(payload, "StationOfLines", "StationOfLine", "Stations")
        result = []
        for item in items:
            result.append({
                "line_id": item.get("LineID") or item.get("RouteID"),
                "line_code": item.get("LineCode") or item.get("RouteCode"),
                "station_id": item.get("StationID") or item.get("StationCode"),
                "station_name": self._localized(item.get("StationName")),
                "sequence": item.get("Sequence") or item.get("StationSequence") or item.get("StopSequence"),
                "cumulative_distance": item.get("CumulativeDistance") or item.get("CumulativeMileage") or item.get("Distance"),
            })
        return result

    def fetch_tra_line_network(self):
        payload = self._get(getattr(settings, "TDX_TRA_LINE_NETWORK_URL", ""))
        items = self._items(payload, "LineNetworks", "LineNetwork", "Networks")
        result = []
        for item in items:
            result.append({
                "segment_id": item.get("LineNetworkID") or item.get("SegmentID") or item.get("LineSegmentID"),
                "from_station_id": item.get("FromStationID") or item.get("StartStationID") or item.get("StationIDFrom"),
                "to_station_id": item.get("ToStationID") or item.get("EndStationID") or item.get("StationIDTo"),
                "line_id": item.get("LineID") or item.get("RouteID"),
                "distance": item.get("Distance") or item.get("StationDistance") or item.get("LineDistance"),
                "line_name": self._localized(item.get("LineName")),
            })
        return result

    def enrich_train_positions(self, records, include_timetable=False):
        """Merge StationLiveBoard fields into TrainLiveBoard records.

        Coordinates remain supplied by the local station GIS fallback; this
        adapter only adds the richer live-board metadata when available.
        """
        if not records or not self.client_id or not self.client_secret:
            return records
        try:
            live = self._items(self._get(getattr(settings, "TDX_TRAIN_STATION_LIVE_URL", "")), "StationLiveBoards", "StationLiveBoard")
        except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError):
            # StationLiveBoard is useful metadata, but timetable enrichment
            # must still run when this independent endpoint is unavailable.
            live = []
        by_train = {str(item.get("TrainNo")): item for item in live if item.get("TrainNo") is not None}
        for record in records:
            item = by_train.get(str(record.get("train_no")))
            if not item:
                continue
            record["destination"] = self._localized(item.get("EndingStationName") or item.get("DestinationStationName")) or record.get("destination")
            record["direction"] = item.get("Direction", record.get("direction"))
            record["train_type_name"] = self._train_type_name(item.get("TrainTypeCode"), item.get("TrainTypeName"))
            record["station_name"] = self._localized(item.get("StationName")) or record.get("station_name")
            record["delay_minutes"] = item.get("DelayTime", record.get("delay_minutes", 0))
            record["live_status"] = item.get("TrainStatus") or item.get("TrainState")
        if include_timetable:
            try:
                timetable = self._items(self._get(getattr(settings, "TDX_TRAIN_DAILY_URL", "")), "DailyTrainTimetables", "DailyTrainTimetable", "TrainTimetables")
            except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError):
                timetable = []
            by_train_timetable = {str((item.get("TrainInfo") or item).get("TrainNo")): item for item in timetable if (item.get("TrainInfo") or item).get("TrainNo") is not None}
            for record in records:
                item = by_train_timetable.get(str(record.get("train_no")))
                if not item:
                    continue
                train_info = item.get("TrainInfo") or item
                stops = item.get("StopTimes") or item.get("StopTime") or item.get("Stops") or []
                if isinstance(stops, dict):
                    stops = stops.get("StopTime") or stops.get("StopTimes") or []
                current_index = next((index for index, stop in enumerate(stops) if str(stop.get("StationID")) == str(record.get("station_id"))), None)
                if current_index is not None and current_index + 1 < len(stops):
                    next_stop = stops[current_index + 1]
                    record["next_station_id"] = next_stop.get("StationID")
                    record["next_station_name"] = self._localized(next_stop.get("StationName"))
                    record["scheduled_departure"] = stops[current_index].get("DepartureTime")
                    record["scheduled_arrival_next"] = next_stop.get("ArrivalTime")
                record["destination"] = record.get("destination") or self._localized(train_info.get("EndingStationName"))
                record["direction"] = train_info.get("Direction", record.get("direction"))
        return records

    @staticmethod
    def _localized(value):
        return _localized(value)

    @staticmethod
    def _train_type_name(code, value=None):
        return _train_type_name(code, value)

    def fetch_train_live(self) -> AdapterResult:
        self.kind = "train"
        fetched_at = timezone.now()
        try:
            payload = self._get(getattr(
                settings, "TDX_TRAIN_LIVE_URL",
                "https://tdx.transportdata.tw/api/basic/v3/Rail/TRA/TrainLiveBoard",
            ))
            payload = self._items(payload, "TrainLiveBoards")
            records = [{
                "train_no": item.get("TrainNo"),
                "train_type_code": item.get("TrainTypeCode"),
                "train_type_name": self._train_type_name(item.get("TrainTypeCode"), item.get("TrainTypeName")),
                "station_id": item.get("StationID"),
                "station_name": self._localized(item.get("StationName")),
                "destination": self._localized(item.get("DestinationStationName")) or self._localized(item.get("終點站")) or item.get("Destination"),
                "direction": item.get("Direction"),
                "latitude": item.get("PositionLat") or (item.get("TrainPosition") or item.get("Position") or {}).get("PositionLat"),
                "longitude": item.get("PositionLon") or (item.get("TrainPosition") or item.get("Position") or {}).get("PositionLon"),
                "delay_minutes": item.get("DelayTime", 0),
                "updated_at": item.get("UpdateTime"),
                "source": "TDX",
            } for item in payload]
            return AdapterResult("ok", self.source, fetched_at, records)
        except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as exc:
            return self._failure(fetched_at, exc)

    def fetch_cctv(self) -> AdapterResult:
        self.kind = "cctv"
        fetched_at = timezone.now()
        try:
            payload = self._get(getattr(
                settings, "TDX_CCTV_URL",
                "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/CCTV/City/Taipei",
            ))
            payload = self._items(payload, "CCTVs")
            records = [{
                "cctv_id": item.get("CCTVID"), "name": item.get("RoadName") or item.get("CCTVID"),
                "city": item.get("CountyName") or item.get("CityName") or item.get("City"),
                "latitude": item.get("PositionLat"), "longitude": item.get("PositionLon"),
                "stream_url": item.get("VideoStreamURL") or item.get("ImageURL") or "", "source": "TDX",
            } for item in payload]
            return AdapterResult("ok", self.source, fetched_at, records)
        except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as exc:
            return self._failure(fetched_at, exc)

    def fetch(self) -> AdapterResult:
        return self.fetch_cctv() if getattr(self, "kind", "train") == "cctv" else self.fetch_train_live()
