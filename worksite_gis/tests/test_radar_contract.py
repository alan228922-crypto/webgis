from datetime import datetime, timezone

import pytest

from worksite_gis.integrations.base import AdapterResult
from worksite_gis.models import Project


def _cache_for(result):
    class Cache:
        def __init__(self, *args, **kwargs):
            pass

        def fetch(self, adapter):
            return result

    return Cache


class FakeRadarAdapter:
    def __init__(self, *args, **kwargs):
        pass


@pytest.mark.django_db
def test_radar_ok_response_contract_without_search_center(client, monkeypatch):
    fetched_at = datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)
    overlay = {
        "url": "https://example.test/radar.png",
        "bounds": [[17.75, 115.0], [29.25, 126.5]],
        "updated_at": "2026-08-19T01:00:00+00:00",
        "content_type": "image/png",
    }
    result = AdapterResult("ok", "cwa", fetched_at, [overlay])
    monkeypatch.setattr("worksite_gis.views.CwaRadarAdapter", FakeRadarAdapter)
    monkeypatch.setattr("worksite_gis.views.SnapshotCache", _cache_for(result))
    project = Project.objects.create(slug="radar-ok", name="Radar")

    response = client.get(f"/api/projects/{project.slug}/radar/")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"status", "source", "fetched_at", "stale", "error", "overlay"}
    assert payload["status"] == "ok"
    assert payload["source"] == "cwa"
    assert payload["fetched_at"] == fetched_at.isoformat()
    assert payload["stale"] is False
    assert payload["error"] == ""
    assert payload["overlay"] == overlay


@pytest.mark.django_db
def test_radar_stale_response_keeps_overlay_and_returns_ok(client, monkeypatch):
    fetched_at = datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)
    overlay = {
        "url": "https://example.test/stale-radar.png",
        "bounds": [[17.75, 115.0], [29.25, 126.5]],
        "updated_at": "2026-08-19T00:55:00+00:00",
        "content_type": "image/png",
    }
    result = AdapterResult("stale", "cwa", fetched_at, [overlay], True, "temporary failure")
    monkeypatch.setattr("worksite_gis.views.CwaRadarAdapter", FakeRadarAdapter)
    monkeypatch.setattr("worksite_gis.views.SnapshotCache", _cache_for(result))
    project = Project.objects.create(slug="radar-stale", name="Radar stale")

    response = client.get(f"/api/projects/{project.slug}/radar/")

    assert response.status_code == 200
    assert response.json() == {
        "status": "stale",
        "source": "cwa",
        "fetched_at": fetched_at.isoformat(),
        "stale": True,
        "error": "temporary failure",
        "overlay": overlay,
    }


@pytest.mark.django_db
def test_radar_error_without_overlay_returns_503_and_error_contract(client, monkeypatch):
    fetched_at = datetime(2026, 8, 19, 1, 0, tzinfo=timezone.utc)
    result = AdapterResult("error", "cwa", fetched_at, [], False, "radar unavailable")
    monkeypatch.setattr("worksite_gis.views.CwaRadarAdapter", FakeRadarAdapter)
    monkeypatch.setattr("worksite_gis.views.SnapshotCache", _cache_for(result))
    project = Project.objects.create(slug="radar-error", name="Radar error")

    response = client.get(f"/api/projects/{project.slug}/radar/")

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "source": "cwa",
        "fetched_at": fetched_at.isoformat(),
        "stale": False,
        "error": "radar unavailable",
        "overlay": None,
    }
