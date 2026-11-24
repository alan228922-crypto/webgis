import base64
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from django.core.cache import cache
from django.utils import timezone

from worksite_gis.integrations.wra import WraInundationAdapter, WraRaster
from worksite_gis.models import ApiSnapshot, Project


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2ioAAAAASUVORK5CYII=")
BOUNDS = [[24.8, 120.9], [25.1, 121.3]]
METADATA = {
    "TimeStamp": "2025-02-01T01:00:00Z",
    "ULLatitude": 25.1,
    "ULLongitude": 120.9,
    "BRLatitude": 24.8,
    "BRLongitude": 121.3,
}


@pytest.fixture
def flood_project():
    return Project.objects.create(
        slug="dynamic-flood-contract", name="Synthetic flood",
        search_center_latitude=25.0, search_center_longitude=121.0,
    )


@pytest.fixture(autouse=True)
def isolate_wra_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def raster_source(monkeypatch, settings):
    settings.WRA_ALLOWED_REGIONS = ("taoyuan", "hsinchu")
    results = {}
    calls = []

    def fetch_raster(adapter, region, hour):
        calls.append((region, hour))
        value = results[(region, hour)]
        if isinstance(value, Exception):
            raise value
        return value

    def no_network(*args, **kwargs):
        pytest.fail("WRA test attempted an unexpected external HTTP request")

    monkeypatch.setattr(WraInundationAdapter, "fetch_raster", fetch_raster)
    monkeypatch.setattr(WraInundationAdapter, "fetch_metadata", no_network)
    monkeypatch.setattr(httpx.Client, "request", no_network)
    return results, calls


def endpoint(project):
    return f"/api/projects/{project.slug}/dynamic-flood/"


def raster(**metadata):
    return WraRaster(PNG, "image/png", {**METADATA, **metadata})


@pytest.mark.django_db
def test_frozen_batch_uses_image_metadata_and_never_refetches(client, flood_project, raster_source):
    results, calls = raster_source
    results["taoyuan", 2] = raster()
    response = client.get(endpoint(flood_project))
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["freshness"] == "fresh"
    assert payload["source_time"] == METADATA["TimeStamp"]
    assert payload["fetched_at"] != payload["source_time"]
    overlay = payload["overlay"]
    assert overlay["bounds"] == BOUNDS
    assert overlay["timestamp"] == METADATA["TimeStamp"]
    batch = parse_qs(urlparse(overlay["image_url"]).query)["batch"][0]
    assert overlay["batch_id"] == batch
    results["taoyuan", 2] = WraRaster(
        b"another provider image", "image/png",
        {**METADATA, "TimeStamp": "2025-02-01T02:00:00Z", "ULLatitude": 25.2},
    )
    image = client.get(overlay["image_url"])
    assert image.content == PNG
    assert image["X-WORKSITE-WRA-Batch"] == batch
    assert calls == [("taoyuan", 2)]
    assert ApiSnapshot.objects.filter(service="wra").count() == 0


@pytest.mark.django_db
def test_batch_cannot_be_reused_across_region_hour_or_without_id(client, flood_project, raster_source):
    results, calls = raster_source
    results["taoyuan", 2] = raster()
    url = client.get(endpoint(flood_project)).json()["overlay"]["image_url"]
    assert client.get(url.replace("taoyuan", "hsinchu")).status_code == 404
    assert client.get(url.replace("forecastHours=2", "forecastHours=0")).status_code == 404
    assert client.get(url.split("&batch=", 1)[0]).status_code == 404
    assert calls == [("taoyuan", 2)]


@pytest.mark.parametrize("source", [
    WraRaster(PNG, "image/png", {}),
    WraRaster(PNG, "image/png", {**METADATA, "TimeStamp": "unknown"}),
    WraRaster(PNG, "image/png", {**METADATA, "TimeStamp": "2025-02-01T01:00:00"}),
    WraRaster(PNG, "image/png", {**METADATA, "ULLatitude": 24.5}),
    WraRaster(PNG, "image/png", {**METADATA, "region": "hsinchu"}),
    WraRaster(PNG, "image/png", {**METADATA, "forecastHours": 0}),
    WraRaster(PNG, "image/png", {**METADATA, "ULLatitude": float("nan")}),
    WraRaster(b"<html>", "text/html", METADATA),
])
@pytest.mark.django_db
def test_unverified_raster_has_no_overlay(client, flood_project, raster_source, source):
    results, calls = raster_source
    results["taoyuan", 2] = source
    result = client.get(endpoint(flood_project))
    assert result.status_code == 503
    assert result.json()["status"] == "unverified"
    assert result.json()["overlay"] is None
    assert calls == [("taoyuan", 2)]


@pytest.mark.django_db
def test_outside_project_scope_does_not_render_as_covered(client, flood_project, raster_source):
    results, _ = raster_source
    results["taoyuan", 2] = raster()
    flood_project.search_center_longitude = 122
    flood_project.save(update_fields=["search_center_longitude"])
    payload = client.get(endpoint(flood_project)).json()
    assert payload["status"] == "unverified"
    assert payload["error"] == "project_outside_source_extent"
    assert payload["overlay"] is None


@pytest.mark.django_db
def test_back_to_zero_hours_is_explicit(client, flood_project, raster_source):
    results, calls = raster_source
    results["taoyuan", 2] = httpx.ReadTimeout("timeout")
    results["taoyuan", 0] = raster()
    response = client.get(endpoint(flood_project))
    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "fallback"
    assert data["requested_forecast_hours"] == 2
    assert data["forecast_hours"] == data["overlay"]["forecast_hours"] == 0
    assert data["reason"] == "timeout"
    assert "forecastHours=0" in data["overlay"]["image_url"]
    assert calls == [("taoyuan", 2), ("taoyuan", 0)]


@pytest.mark.parametrize(("source", "status", "http_code"), [
    (WraRaster(b"", "image/png", {"IsEmptyRasterMap": True}), "empty", 200),
    (httpx.ReadTimeout("token=unprintable"), "timeout", 503),
    (httpx.ConnectError("token=unprintable"), "unavailable", 503),
])
@pytest.mark.django_db
def test_empty_and_failures_remain_distinct_and_sanitized(
    client, flood_project, raster_source, source, status, http_code
):
    results, calls = raster_source
    hour = 2 if status == "empty" else 0
    results["taoyuan", hour] = source
    response = client.get(endpoint(flood_project), {"forecastHours": hour})
    assert response.status_code == http_code
    assert response.json()["status"] == status
    assert response.json()["overlay"] is None
    assert b"unprintable" not in response.content
    assert calls == [("taoyuan", hour)]


@pytest.mark.django_db
def test_stale_reuses_verified_batch_for_same_region_and_hour_with_bound(
    client, flood_project, raster_source, monkeypatch
):
    results, calls = raster_source
    start = timezone.now()
    monkeypatch.setattr(timezone, "now", lambda: start)
    results["taoyuan", 2] = raster()
    original = client.get(endpoint(flood_project)).json()
    results["taoyuan", 2] = httpx.ReadTimeout("private timeout")
    results["hsinchu", 0] = httpx.ConnectError("unavailable")
    results["taoyuan", 0] = httpx.ConnectError("unavailable")
    monkeypatch.setattr(timezone, "now", lambda: start + timedelta(minutes=6))
    response = client.get(endpoint(flood_project)).json()
    assert response["status"] == response["freshness"] == "stale"
    assert response["reason"] == "timeout"
    assert response["source_time"] == original["source_time"]
    assert response["fetched_at"] == original["fetched_at"]
    assert response["overlay"] == original["overlay"]
    assert client.get(response["overlay"]["image_url"]).content == PNG
    other_region = client.get(endpoint(flood_project), {"region": "hsinchu", "forecastHours": 0}).json()
    assert other_region["overlay"] is None
    other_hour = client.get(endpoint(flood_project), {"forecastHours": 0}).json()
    assert other_hour["overlay"] is None
    monkeypatch.setattr(timezone, "now", lambda: start + timedelta(minutes=16))
    assert client.get(response["overlay"]["image_url"]).status_code == 404
    expired = client.get(endpoint(flood_project)).json()
    assert expired["status"] == "timeout"
    assert expired["overlay"] is None
    assert calls == [("taoyuan", 2), ("taoyuan", 2), ("hsinchu", 0), ("taoyuan", 0), ("taoyuan", 2), ("taoyuan", 0)]


@pytest.mark.django_db
def test_region_and_hours_bounded_without_demo_egress(client, flood_project, raster_source, settings):
    results, calls = raster_source
    assert client.get(endpoint(flood_project), {"region": "unknown"}).status_code == 400
    assert client.get(endpoint(flood_project), {"forecastHours": "one"}).status_code == 400
    assert calls == []
    results["taoyuan", 0] = raster()
    results["taoyuan", 6] = raster()
    assert client.get(endpoint(flood_project), {"forecastHours": -2}).json()["forecast_hours"] == 0
    assert client.get(endpoint(flood_project), {"forecastHours": 80}).json()["forecast_hours"] == 6
    settings.WORKSITE_DEMO = True
    response = client.get(endpoint(flood_project))
    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
    assert response.json()["overlay"] is None
    assert client.get(endpoint(flood_project) + "image/").status_code == 404
    assert calls == [("taoyuan", 0), ("taoyuan", 6)]


def test_demo_blocks_direct_wra_adapter_entrypoints(settings, monkeypatch):
    settings.WORKSITE_DEMO = True

    def unexpected_http(*args, **kwargs):
        pytest.fail("The disabled adapter attempted HTTP")

    monkeypatch.setattr(httpx.Client, "request", unexpected_http)
    adapter = WraInundationAdapter()
    try:
        assert adapter.fetch_metadata("taoyuan", 2).status == "disabled"
        with pytest.raises(httpx.RequestError, match="disabled"):
            adapter.fetch_raster("taoyuan", 2)
    finally:
        adapter.close()
