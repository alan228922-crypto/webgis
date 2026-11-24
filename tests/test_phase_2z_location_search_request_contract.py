import json

import pytest
from django.test import Client, override_settings

from worksite_gis.models import Project


@pytest.mark.django_db
def test_worksite_project_slug_is_resolvable_for_location_search():
    Project.objects.create(slug="worksite", name="WORKSITE", status="active")
    project = Project.objects.get(slug="worksite")
    assert project.slug == "worksite"
    assert project.status == "active"


@pytest.mark.django_db
def test_json_location_search_post_uses_mocked_provider_and_returns_candidates(client, monkeypatch):
    from worksite_gis.integrations import geocoding

    class FakeResponse:
        def raise_for_status(self):
            pass

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
            return FakeResponse()

    Project.objects.create(slug="worksite", name="WORKSITE", status="active")
    monkeypatch.setattr(geocoding, "DEFAULT_HTTP_CLIENT", FakeClient())
    with override_settings(GEOCODING_PROVIDER="nominatim", NOMINATIM_GEOCODING_URL="https://example.test/search"):
        response = client.post(
            "/api/projects/worksite/location-search/",
            data=json.dumps({"query": "Taoyuan Station"}),
            content_type="application/json",
        )

    assert response.status_code == 200
    assert response["Content-Type"].startswith("application/json")
    assert response.json() == {
        "candidates": [{
            "label": "Taoyuan Station, Taoyuan District, Taiwan",
            "address": "Taoyuan Station, Taoyuan District, Taiwan",
            "latitude": 24.9896,
            "longitude": 121.3137,
            "provider": "nominatim",
            "precision": "station",
            "raw_id": "node:12345",
        }],
        "provider": "nominatim",
    }


@pytest.mark.django_db
def test_json_location_search_returns_503_when_provider_is_unavailable(client, monkeypatch):
    from worksite_gis.integrations import geocoding

    class FailingClient:
        def get(self, *args, **kwargs):
            raise OSError("network unavailable")

    Project.objects.create(slug="worksite", name="WORKSITE", status="active")
    monkeypatch.setattr(geocoding, "DEFAULT_HTTP_CLIENT", FailingClient())
    with override_settings(GEOCODING_PROVIDER="nominatim", NOMINATIM_GEOCODING_URL="https://example.test/search"):
        response = client.post(
            "/api/projects/worksite/location-search/",
            data=json.dumps({"query": "Taoyuan Station"}),
            content_type="application/json",
        )

    assert response.status_code == 503
    assert response.json() == {"error": {"message": "搜尋服務暫時無法連線"}}


@pytest.mark.django_db
def test_urlencoded_location_search_post_is_rejected_by_json_only_endpoint():
    Project.objects.create(slug="worksite", name="WORKSITE", status="active")
    response = Client().post(
        "/api/projects/worksite/location-search/",
        data={"query": "桃園車站", "csrfmiddlewaretoken": "test-token"},
        content_type="application/x-www-form-urlencoded",
    )

    assert response.status_code == 404
    assert response.json() == {"error": {"message": "project not found or request body invalid"}}
