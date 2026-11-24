import json
from pathlib import Path

import pytest
from django.test import Client, override_settings


def test_tile_path_uses_hexadecimal_tgos_components():
    from worksite_gis.views import _offline_tile_candidates

    candidates = _offline_tile_candidates(14, 13711, 7016)
    assert any("R00001b68" in str(path) and "C0000358f.jpg" in str(path) for path in candidates)


def test_offline_tile_returns_jpeg_for_existing_tgos_tile(client, tmp_path):
    from django.test import override_settings

    root = tmp_path / "archive" / "CH_3857_Vect_OpenData" / "CH_3857_Vect_OpenData"
    tile = root / "L14" / "R00001b68" / "C0000358f.jpg"
    tile.parent.mkdir(parents=True)
    tile.write_bytes(b"tile")

    with override_settings(OFFLINE_TILE_ROOT=tmp_path):
        response = client.get("/api/tiles/14/13711/7016.jpg")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/jpeg"


@pytest.mark.django_db
def test_public_location_search_cannot_save_shared_center(client, monkeypatch):
    from worksite_gis.integrations import geocoding
    from worksite_gis.models import Project

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return [{
                "display_name": "Taoyuan Station, Taoyuan District, Taiwan",
                "lat": "24.9896",
                "lon": "121.3137",
                "osm_type": "node",
                "osm_id": 12345,
                "type": "station",
            }]

    class FakeClient:
        def get(self, *args, **kwargs): return FakeResponse()

    monkeypatch.setattr(geocoding, "DEFAULT_HTTP_CLIENT", FakeClient())

    Project.objects.create(slug="worksite", name="WORKSITE")
    with override_settings(GEOCODING_PROVIDER="nominatim", NOMINATIM_GEOCODING_URL="https://example.test/search"):
        search = client.post("/api/projects/worksite/location-search/", data={"query": "桃園車站"}, content_type="application/json")
    assert search.status_code == 200
    assert search.json()["candidates"][0]["provider"] == "nominatim"
    save = client.post("/api/projects/worksite/search-center/", data={"label": "桃園車站", "latitude": 24.99, "longitude": 121.31}, content_type="application/json")
    assert save.status_code == 401
    assert Project.objects.get(slug="worksite").setup_status == "unlocated"


@pytest.mark.django_db
def test_location_search_supports_public_get_without_csrf(client):
    from worksite_gis.models import Project

    Project.objects.create(slug="worksite", name="WORKSITE")
    response = client.get("/api/projects/worksite/location-search/", {"query": "24.985556,121.310278"})
    assert response.status_code == 200
    assert response.json()["candidates"][0]["provider"] == "coordinates"


@pytest.mark.django_db
def test_location_search_enforces_csrf_and_preserves_json_response(django_user_model):
    from worksite_gis.models import Project

    Project.objects.create(slug="worksite", name="WORKSITE")
    csrf_client = Client(enforce_csrf_checks=True)
    csrf_client.force_login(django_user_model.objects.create_user(username="location-search-csrf", password="Valid-pass-123"))
    page = csrf_client.get("/map/")
    token = page.content.decode("utf-8").split('name="csrfmiddlewaretoken" value="', 1)[1].split('"', 1)[0]
    payload = json.dumps({"query": "24.985556,121.310278"})

    missing = csrf_client.post(
        "/api/projects/worksite/location-search/",
        data=payload,
        content_type="application/json",
    )
    assert missing.status_code == 403
    assert "CSRF" in missing.content.decode("utf-8")

    valid = csrf_client.post(
        "/api/projects/worksite/location-search/",
        data=payload,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
        HTTP_REFERER="http://testserver/map/",
    )
    assert valid.status_code == 200
    assert valid["Content-Type"].startswith("application/json")
    assert valid.json()["candidates"][0]["provider"] == "coordinates"


@pytest.mark.django_db
def test_map_uses_grid_class_and_real_preview_images(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="map-preview", password="Valid-pass-123"))
    page = client.get("/map/").content.decode("utf-8")
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "drawer-closed" in page or "drawer-closed" in script
    assert '<img' in page and 'data-i18n=' in page
    assert "basemap-tgos-preview" in page


def test_language_dictionary_updates_all_data_i18n_elements():
    script = Path("home/static/home/js/map.js").read_text(encoding="utf-8")
    assert "const translations" in script
    assert "applyLanguage" in script
    assert "querySelectorAll('[data-i18n]')" in script
    assert "localStorage.getItem('worksite-language')" in script


def test_tgos_provider_normalizes_official_candidate(monkeypatch):
    from worksite_gis.integrations.geocoding import TgosGeocodingProvider

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {"results": [{"label": "桃園車站", "address": "桃園市桃園區", "lat": 24.9896, "lon": 121.3137, "id": "station-1"}]}

    class FakeClient:
        def get(self, *args, **kwargs): return FakeResponse()

    result = TgosGeocodingProvider("key", "https://example.test/search", client=FakeClient()).search("桃園車站")
    assert result[0]["provider"] == "tgos"
    assert result[0]["latitude"] == 24.9896
    assert result[0]["raw_id"] == "station-1"


def test_nlsc_provider_normalizes_feature_response():
    from worksite_gis.integrations.geocoding import NlscGeocodingProvider

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {"data": [{"name": "桃園車站", "address": "桃園市桃園區", "lat": 24.9896, "lon": 121.3137, "id": "nlsc-1"}]}

    class FakeClient:
        def get(self, *args, **kwargs): return FakeResponse()

    result = NlscGeocodingProvider("https://example.test/nlsc", client=FakeClient()).search("桃園車站")
    assert result == [{"label": "桃園車站", "address": "桃園市桃園區", "latitude": 24.9896, "longitude": 121.3137, "provider": "nlsc", "precision": "address", "raw_id": "nlsc-1"}]


def test_nominatim_provider_normalizes_osm_response():
    from worksite_gis.integrations.geocoding import NominatimGeocodingProvider

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return [{
                "display_name": "Taoyuan Station, Taoyuan District, Taiwan",
                "lat": "24.9896",
                "lon": "121.3137",
                "osm_type": "node",
                "osm_id": 12345,
                "type": "station",
            }]

    class FakeClient:
        def get(self, *args, **kwargs):
            assert kwargs["params"]["format"] == "jsonv2"
            assert kwargs["params"]["limit"] == 10
            assert kwargs["params"]["countrycodes"] == "tw"
            assert kwargs["headers"]["User-Agent"] == "Worksite-GIS/1.0"
            return FakeResponse()

    result = NominatimGeocodingProvider("https://example.test/search", client=FakeClient()).search("桃園車站")
    assert result == [{
        "label": "Taoyuan Station, Taoyuan District, Taiwan",
        "address": "Taoyuan Station, Taoyuan District, Taiwan",
        "latitude": 24.9896,
        "longitude": 121.3137,
        "provider": "nominatim",
        "precision": "station",
        "raw_id": "node:12345",
    }]


def test_location_search_uses_nlsc_provider_and_limits_results(client, django_user_model, monkeypatch):
    from worksite_gis.models import Project
    Project.objects.create(slug="worksite", name="WORKSITE")
    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return {"results": [{"name": str(i), "lat": 24 + i / 1000, "lon": 121 + i / 1000} for i in range(20)]}
    class FakeClient:
        def get(self, *args, **kwargs): return FakeResponse()
    from worksite_gis.integrations import geocoding
    monkeypatch.setattr(geocoding, "DEFAULT_HTTP_CLIENT", FakeClient())
    with override_settings(GEOCODING_PROVIDER="nlsc", NLSC_GEOCODING_URL="https://example.test/nlsc"):
        response = client.post("/api/projects/worksite/location-search/", data=json.dumps({"query": "桃園車站"}), content_type="application/json")
    assert response.status_code == 200
    assert response.json()["provider"] == "nlsc"
    assert len(response.json()["candidates"]) == 10


@pytest.mark.django_db
def test_location_search_uses_nominatim_provider_and_limits_results(client, monkeypatch):
    from worksite_gis.models import Project
    Project.objects.create(slug="worksite", name="WORKSITE")
    class FakeResponse:
        def raise_for_status(self): pass
        def json(self):
            return [{"display_name": str(i), "lat": str(24 + i / 1000), "lon": str(121 + i / 1000), "osm_type": "node", "osm_id": i} for i in range(20)]
    class FakeClient:
        def get(self, *args, **kwargs): return FakeResponse()
    from worksite_gis.integrations import geocoding
    monkeypatch.setattr(geocoding, "DEFAULT_HTTP_CLIENT", FakeClient())
    with override_settings(GEOCODING_PROVIDER="nominatim", NOMINATIM_GEOCODING_URL="https://example.test/search"):
        response = client.post("/api/projects/worksite/location-search/", data=json.dumps({"query": "桃園車站"}), content_type="application/json")
    assert response.status_code == 200
    assert response.json()["provider"] == "nominatim"
    assert len(response.json()["candidates"]) == 10


@pytest.mark.django_db
def test_location_provider_switches_to_mock(client):
    from worksite_gis.models import Project
    Project.objects.create(slug="worksite", name="WORKSITE")
    with override_settings(GEOCODING_PROVIDER="mock"):
        response = client.post("/api/projects/worksite/location-search/", data=json.dumps({"query": "桃園車站"}), content_type="application/json")
    assert response.status_code == 200
    assert response.json()["candidates"][0]["provider"] == "mock"


@pytest.mark.django_db
def test_nominatim_failure_does_not_return_mock(client, monkeypatch):
    from worksite_gis.models import Project
    Project.objects.create(slug="worksite", name="WORKSITE")
    from worksite_gis.integrations import geocoding
    class FailingClient:
        def get(self, *args, **kwargs):
            raise OSError("network unavailable")
    monkeypatch.setattr(geocoding, "DEFAULT_HTTP_CLIENT", FailingClient())
    with override_settings(GEOCODING_PROVIDER="nominatim", NOMINATIM_GEOCODING_URL="https://example.test/search"):
        response = client.post("/api/projects/worksite/location-search/", data=json.dumps({"query": "桃園車站"}), content_type="application/json")
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "搜尋服務暫時無法連線"


def test_nominatim_default_session_ignores_environment_proxy():
    from worksite_gis.integrations.geocoding import NominatimGeocodingProvider
    provider = NominatimGeocodingProvider("https://example.test/search")
    assert getattr(provider.client, "trust_env", None) is False
