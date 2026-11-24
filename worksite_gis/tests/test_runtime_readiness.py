from pathlib import Path
from unittest.mock import patch

import pytest
from django.db import OperationalError, connection

from worksite_gis.models import ApiSnapshot


@pytest.mark.django_db
def test_status_with_all_optional_paths_present_and_liveness(client, settings, tmp_path):
    index = tmp_path / "index.sqlite3"
    index.touch()
    settings.WORKSITE_GEOLOGIC_SENSITIVE_VIEWPORT_INDEX_PATH = index
    settings.OFFLINE_TILE_ROOT = tmp_path
    (tmp_path / "synthetic.png").touch()

    with patch("httpx.Client.send", side_effect=AssertionError("unexpected HTTP")):
        before = ApiSnapshot.objects.count()
        response = client.get("/api/status/")
        assert response.status_code == 200
        assert response.json()["basemap"]["available"]
        assert response.json()["layers"]["missing_outputs"] == []
        assert ApiSnapshot.objects.count() == before


@pytest.mark.django_db
def test_readiness_ignores_optional_resources_but_requires_static(client, settings, tmp_path):
    settings.DEBUG = True
    settings.OFFLINE_TILE_ROOT = tmp_path / "missing"
    settings.WORKSITE_GEOLOGIC_SENSITIVE_VIEWPORT_INDEX_PATH = tmp_path / "missing.sqlite3"
    with patch("httpx.Client.send", side_effect=AssertionError("unexpected HTTP")):
        before = ApiSnapshot.objects.count()
        response = client.get("/api/ready/")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
        assert client.get("/api/status/").json()["status"] == "degraded"
        assert ApiSnapshot.objects.count() == before
    settings.DEBUG = False
    settings.STATIC_ROOT = tmp_path / "missing-static"
    assert client.get("/api/ready/").status_code == 503


@pytest.mark.django_db
def test_db_failure_leaves_liveness_healthy(client):
    failure = OperationalError("sensitive connection details")
    with (patch.object(connection, "cursor", side_effect=failure),
          patch("worksite_gis.runtime_health._probe_cursor", side_effect=failure)):
        assert client.get("/api/health/").status_code == 200
        response = client.get("/api/status/")
        assert response.status_code == 503
        assert response.json()["status"] == "unavailable"
        assert b"sensitive" not in response.content
        response = client.get("/api/ready/")
        assert response.status_code == 503
        assert response.json()["checks"]["database"] == "unavailable"
        assert b"sensitive" not in response.content


@pytest.mark.django_db
def test_readiness_requires_worksite_schema(client, settings, tmp_path):
    settings.DEBUG = True
    alternate = tmp_path / "empty.sqlite3"
    alternate.touch()
    with patch.dict(connection.settings_dict, {"NAME": Path(alternate)}):
        response = client.get("/api/ready/")
    assert response.status_code == 503
    assert response.json()["checks"]["schema"] == "unavailable"


@pytest.mark.django_db
def test_readiness_does_not_create_missing_sqlite_database(client, tmp_path):
    missing = tmp_path / "missing.sqlite3"
    with patch.dict(connection.settings_dict, {"NAME": missing}):
        assert client.get("/api/ready/").status_code == 503
    assert not missing.exists()


@pytest.mark.parametrize("data", [b"\xff", b"{", b"[]"])
@pytest.mark.django_db
def test_status_does_not_validate_unreadable_optional_report(client, settings, tmp_path, data):
    settings.DATA_CATALOG_DIR = tmp_path
    (tmp_path / "validation_report.json").write_bytes(data)
    response = client.get("/api/status/")
    assert response.status_code == 200
    assert response.json()["processed_data"] == {"available": False, "count": 0, "valid": 0, "invalid": 0}


@pytest.mark.parametrize("data", [b"\xff", b'{"file_count":"unknown"}', b'{"file_count":-1}'])
@pytest.mark.django_db
def test_status_treats_invalid_basemap_manifest_count_as_unknown(client, settings, tmp_path, data):
    settings.DATA_PROCESSED_DIR = settings.OFFLINE_TILE_ROOT = tmp_path
    (tmp_path / "synthetic.png").touch()
    (tmp_path / "basemap").mkdir()
    (tmp_path / "basemap" / "offline_tiles_manifest.json").write_bytes(data)
    response = client.get("/api/status/")
    assert response.status_code == 200
    assert response.json()["basemap"] == {"available": True, "file_count": 0}
