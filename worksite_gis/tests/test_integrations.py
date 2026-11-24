from datetime import timedelta

import httpx
import pytest
from django.utils import timezone

from worksite_gis.integrations.base import AdapterResult, SnapshotCache
from worksite_gis.integrations.cwa import CwaWeatherAdapter
from worksite_gis.integrations.cwa import CwaRadarAdapter
from worksite_gis.integrations.cwa import CwaAlertAdapter
from worksite_gis.integrations.tdx import TdxClient
from worksite_gis.integrations.wra import WraInundationAdapter
from worksite_gis.models import ApiSnapshot


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def response(payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "https://example.test"))


def test_cwa_adapter_normalizes_station_observations():
    payload = {"records": {"Station": [{
        "StationId": "467490", "StationName": "臺中",
        "GeoInfo": {"Coordinates": [{"StationLatitude": 24.1, "StationLongitude": 120.6}]},
        "ObsTime": {"DateTime": "2026-08-12T10:00:00+08:00"},
        "WeatherElement": {"AirTemperature": 31.2, "RelativeHumidity": 65,
                           "WindSpeed": 2.3, "Now": {"Precipitation": 0.5}},
    }]}}
    client = FakeClient([response(payload)])

    result = CwaWeatherAdapter("secret", client=client).fetch()

    assert result.status == "ok"
    assert result.records[0] == {
        "station_id": "467490", "name": "臺中", "latitude": 24.1, "longitude": 120.6,
        "observed_at": "2026-08-12T10:00:00+08:00", "temperature": 31.2,
        "humidity": 65, "wind_speed": 2.3, "precipitation": 0.5,
        "source": "CWA", "status": "observed",
    }
    assert client.calls[0][2]["params"]["Authorization"] == "secret"
    assert client.calls[0][2]["params"]["format"] == "JSON"


def test_cwa_radar_adapter_returns_latest_overlay_metadata():
    client = FakeClient([])
    result = CwaRadarAdapter("secret", client=client).fetch()
    assert result.status == "ok"
    assert result.records[0]["url"].endswith("O-A0058-005.png")
    assert result.records[0]["bounds"] == [[17.75, 115.0], [29.25, 126.5]]


def test_wra_inundation_adapter_normalizes_metadata_and_raster():
    client = FakeClient([
        response({"TimeStamp": "2026-08-17T05:00:00Z", "ULLatitude": 25.1, "ULLongitude": 120.9, "BRLatitude": 24.8, "BRLongitude": 121.3, "IsEmptyRasterMap": False}),
        httpx.Response(200, content=b"png", headers={"Content-Type": "image/png", "RasterMapMetaData": '{"TimeStamp":"2026-08-17T05:00:00Z","ULLatitude":25.1,"ULLongitude":120.9,"BRLatitude":24.8,"BRLongitude":121.3}'}, request=httpx.Request("GET", "https://example.test")),
    ])
    adapter = WraInundationAdapter(client=client)
    metadata = adapter.fetch_metadata("taoyuan", 2)
    assert metadata.status == "ok"
    assert metadata.records[0]["bounds"] == [[24.8, 120.9], [25.1, 121.3]]
    image = adapter.fetch_raster("taoyuan", 2)
    assert image.content == b"png"
    assert image.content_type == "image/png"


def test_cwa_alert_adapter_normalizes_current_hazards():
    payload = {"records": {"locations": [{"locationName": "桃園市", "hazardConditions": [{"phenomena": "大雨", "significance": "warning", "issueTime": "2026-08-17T05:00:00+08:00", "validTime": {"startTime": "2026-08-17T05:00:00+08:00", "endTime": "2026-08-17T17:00:00+08:00"}, "contentText": "請注意強降雨"}]}]}}
    result = CwaAlertAdapter("secret", client=FakeClient([response(payload)])).fetch()
    assert result.status == "ok"
    assert result.records[0]["title"] == "大雨｜桃園市"
    assert result.records[0]["payload"]["location"] == "桃園市"


def test_cwa_adapter_ignores_unavailable_environment_proxy(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")

    adapter = CwaWeatherAdapter("secret")

    assert adapter.client._trust_env is False


def test_tdx_reuses_token_and_normalizes_train_live_board():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response([{"TrainNo": "123", "TrainTypeCode": "1", "StationID": "1000",
                   "StationName": {"Zh_tw": "臺北"}, "DelayTime": 3,
                   "UpdateTime": "2026-08-12T10:00:00+08:00"}]),
        response([]),
    ])
    tdx = TdxClient("id", "secret", client=client)

    first = tdx.fetch_train_live()
    tdx.fetch_train_live()

    assert first.records[0]["train_no"] == "123"
    assert first.records[0]["delay_minutes"] == 3
    assert [call[0] for call in client.calls].count("post") == 1
    assert client.calls[1][2]["headers"]["Authorization"] == "Bearer token"


def test_tdx_normalizes_highway_section_shapes():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response([{"SectionID": "S1", "Geometry": "LINESTRING(121.1 25.1,121.2 25.2)"}, {"SectionID": "S2", "Geometry": ""}]),
    ])
    result = TdxClient("id", "secret", client=client).fetch_highway_section_shapes()
    assert result == [{"section_id": "S1", "geometry": "LINESTRING(121.1 25.1,121.2 25.2)"}]


def test_tdx_normalizes_freeway_section_shapes_and_live_status():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response([{"SectionID": "F1", "Geometry": "LINESTRING(121.1 25.1,121.2 25.2)"}]),
        response({"LiveTraffics": [{"SectionID": "F1", "TravelSpeed": 88, "CongestionLevel": "1"}]}),
    ])
    tdx = TdxClient("id", "secret", client=client)
    assert tdx.fetch_freeway_section_shapes() == [{"section_id": "F1", "geometry": "LINESTRING(121.1 25.1,121.2 25.2)"}]
    assert tdx.fetch_freeway_live().records[0]["travel_speed"] == 88
    assert "/SectionShape/Freeway" in client.calls[1][1]
    assert "/Live/Freeway" in client.calls[2][1]


def test_tdx_normalizes_freeway_section_metadata():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response([{"SectionID": "F1", "RoadName": "國道1號", "RoadDirection": "N", "SectionName": "交流道A~B"}]),
    ])
    result = TdxClient("id", "secret", client=client).fetch_freeway_sections()
    assert result == [{"section_id": "F1", "road_name": "國道1號", "direction": "N", "section_name": "交流道A~B"}]


def test_tdx_normalizes_highway_live_status():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response({"LiveTraffics": [{"SectionID": "S1", "TravelTime": 36, "TravelSpeed": 22.5, "CongestionLevelID": "D", "CongestionLevel": "3", "DataCollectTime": "2026-08-17T10:18:34+08:00"}]}),
    ])
    result = TdxClient("id", "secret", client=client).fetch_highway_live()
    assert result.records[0]["section_id"] == "S1"
    assert result.records[0]["travel_speed"] == 22.5


def test_tdx_normalizes_highway_facility_track_roads():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response([{"RoadName": "台 13甲", "CityList": [{"CityName": "苗栗縣", "City": "MiaoliCounty"}]}]),
    ])
    result = TdxClient("id", "secret", client=client).fetch_highway_facility_tracks(3)
    assert result[0] == {"road_name": "台 13甲", "road_class": 3, "cities": [{"name": "苗栗縣", "code": "MiaoliCounty"}]}


def test_tdx_normalizes_highway_section_metadata():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response([{"SectionID": "S1", "RoadName": "台13甲", "RoadDirection": "E", "SectionName": "台13甲_0K+0~1K+0"}]),
    ])
    result = TdxClient("id", "secret", client=client).fetch_highway_sections()
    assert result == [{"section_id": "S1", "road_name": "台13甲", "direction": "E", "section_name": "台13甲_0K+0~1K+0"}]


def test_tdx_normalizes_train_display_fields_for_map_markers():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response([{
            "TrainNo": "789", "TrainTypeCode": "3", "StationID": "1001",
            "StationName": {"Zh_tw": "測試站"},
            "DestinationStationName": {"Zh_tw": "高雄"},
            "Direction": 1, "PositionLat": 25.01, "PositionLon": 121.52,
            "DelayTime": 2,
        }]),
    ])
    record = TdxClient("id", "secret", client=client).fetch_train_live().records[0]
    assert record["train_type_name"]
    assert record["destination"] == "高雄"
    assert record["direction"] == 1
    assert record["latitude"] == 25.01
    assert record["longitude"] == 121.52


def test_tdx_enriches_train_records_from_station_live_board():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response({"StationLiveBoards": [{
            "TrainNo": "789", "TrainTypeCode": "1", "TrainTypeName": {"Zh_tw": "自強號"},
            "StationName": {"Zh_tw": "桃園"}, "EndingStationName": {"Zh_tw": "臺北"},
            "Direction": 0, "DelayTime": 4, "TrainStatus": "正常運行",
        }]}),
    ])
    adapter = TdxClient("id", "secret", client=client)
    records = [{"train_no": "789", "destination": None, "direction": None, "delay_minutes": 0}]
    adapter.enrich_train_positions(records)
    assert records[0]["destination"] == "臺北"
    assert records[0]["direction"] == 0
    assert records[0]["delay_minutes"] == 4
    assert records[0]["station_name"] == "桃園"


def test_tdx_timetable_enrichment_survives_station_live_timeout():
    timeout = httpx.ReadTimeout("station live timeout", request=httpx.Request("GET", "https://example.test"))
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        timeout,
        response({"DailyTrainTimetables": [{
            "TrainInfo": {"TrainNo": "789"},
            "StopTimes": [
                {"StationID": "1000", "StationName": {"Zh_tw": "甲"}, "DepartureTime": "09:00"},
                {"StationID": "1010", "StationName": {"Zh_tw": "乙"}, "ArrivalTime": "09:05"},
            ],
        }]}),
    ])
    adapter = TdxClient("id", "secret", client=client)
    records = [{"train_no": "789", "station_id": "1000"}]

    adapter.enrich_train_positions(records, include_timetable=True)

    assert records[0]["next_station_id"] == "1010"
    assert records[0]["scheduled_departure"] == "09:00"
    assert records[0]["scheduled_arrival_next"] == "09:05"


def test_tdx_unwraps_official_v3_response_envelopes():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response({"TrainLiveBoards": [{"TrainNo": "456", "StationName": {"Zh_tw": "板橋"}}]}),
        response({"CCTVs": [{"CCTVID": "cam-1", "RoadName": "南投縣道路 151", "CountyName": "南投縣", "PositionLat": 25.0, "PositionLon": 121.5, "VideoStreamURL": "https://example.test/stream"}]}),
    ])
    tdx = TdxClient("id", "secret", client=client)

    train = tdx.fetch_train_live()
    cctv = tdx.fetch_cctv()

    assert train.status == "ok"
    assert train.records[0]["train_no"] == "456"
    assert cctv.status == "ok"
    assert cctv.records[0]["cctv_id"] == "cam-1"
    assert cctv.records[0]["stream_url"] == "https://example.test/stream"


@pytest.mark.django_db
def test_snapshot_cache_returns_stale_data_when_adapter_fails():
    now = timezone.now()
    ApiSnapshot.objects.create(
        service="cwa", kind="weather", fetched_at=now - timedelta(minutes=5),
        expires_at=now - timedelta(minutes=4), payload=[{"station_id": "cached"}], status="ok",
    )

    class BrokenAdapter:
        source = "cwa"
        kind = "weather"

        def fetch(self):
            return AdapterResult("error", "cwa", timezone.now(), [], False, "timeout")

    result = SnapshotCache(ttl_seconds=60).fetch(BrokenAdapter())

    assert result.status == "stale"
    assert result.stale is True
    assert result.records == [{"station_id": "cached"}]
    assert "timeout" in result.error


def test_tdx_normalizes_tra_station_network_and_station_of_line():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response({"Stations": [{"StationID": "100", "StationName": {"Zh_tw": "甲站"}, "StationPosition": {"PositionLat": 24.1, "PositionLon": 120.6}}]}),
        response({"Networks": [{"NetworkID": "N1", "NetworkName": {"Zh_tw": "臺鐵路網"}, "Lines": [{"LineID": "L1", "LineName": {"Zh_tw": "山線"}}]}]}),
        response({"StationOfLines": [{"LineID": "L1", "StationID": "100", "Sequence": 1, "CumulativeDistance": 0.0}]}),
    ])
    adapter = TdxClient("id", "secret", client=client)

    assert adapter.fetch_tra_stations()[0]["station_id"] == "100"
    assert adapter.fetch_tra_network()[0]["network_id"] == "N1"
    assert adapter.fetch_tra_station_of_line()[0]["line_id"] == "L1"


def test_tdx_normalizes_tra_line_network_segments():
    client = FakeClient([
        response({"access_token": "token", "expires_in": 3600}),
        response({"LineNetworks": [{"LineNetworkID": "S1", "FromStationID": "100", "ToStationID": "101", "LineID": "L1", "Distance": 2.4, "LineName": {"Zh_tw": "山線"}}]}),
    ])
    adapter = TdxClient("id", "secret", client=client)

    segment = adapter.fetch_tra_line_network()[0]
    assert segment == {"segment_id": "S1", "from_station_id": "100", "to_station_id": "101", "line_id": "L1", "distance": 2.4, "line_name": "山線"}
