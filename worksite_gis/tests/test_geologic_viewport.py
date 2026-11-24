import json
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from worksite_gis.data_pipeline.spatial_index import build_spatial_index

pytestmark = pytest.mark.django_db


def _write_source(path: Path) -> None:
    features = []
    for index, category in enumerate(("L", "F", "G", "H")):
        west = 121.30 + index * 0.01
        features.append({
            "type": "Feature",
            "properties": {
                "source_category": category,
                "official_id": f"{category}0001",
                "official_name": f"{category} test",
                "C_NAME": None,
                "AREA": None,
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[west, 24.98], [west + 0.005, 24.98], [west + 0.005, 24.985], [west, 24.985], [west, 24.98]]],
            },
        })
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")


@pytest.fixture(scope="module")
def viewport_index():
    root = (Path(".tmp-tests") / "phase5b-viewport").resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = root / "eligible.geojson"
    index = root / "geologic_sensitive.sqlite3"
    _write_source(source)
    build_spatial_index([("geologic_sensitive_areas", "hazards", source)], index)
    return index


def _get(client, index, query="bbox=121.29,24.97,121.36,24.99", authenticated=True):
    if authenticated:
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(username="viewport-test-user")
        if created:
            user.set_password("viewport-test-password")
            user.save(update_fields=["password"])
        client.force_login(user)
    with override_settings(WORKSITE_GEOLOGIC_SENSITIVE_VIEWPORT_INDEX_PATH=index):
        return client.get(f"/api/layers/geologic_sensitive_areas/viewport/?{query}")


@pytest.mark.parametrize("query", [
    "",
    "bbox=121.3,24.98,121.31",
    "bbox=121.3,24.98,not-a-number,24.99",
    "bbox=121.31,24.98,121.30,24.99",
    "bbox=121.3,24.99,121.31,24.98",
    "bbox=181,24.98,182,24.99",
    "bbox=121.3,91,121.31,92",
    "bbox=121.3,24.98,NaN,24.99",
    "bbox=121.3,24.98,Infinity,24.99",
])
def test_viewport_rejects_invalid_bbox(client, viewport_index, query):
    response = _get(client, viewport_index, query)
    assert response.status_code == 400
    assert response.json()["error"]["message"]


def test_viewport_returns_feature_collection_and_properties(client, viewport_index):
    response = _get(client, viewport_index)

    assert response.status_code == 200
    payload = response.json()
    assert payload["type"] == "FeatureCollection"
    assert len(payload["features"]) == 4
    assert all(feature["geometry"]["type"] in {"Polygon", "MultiPolygon"} for feature in payload["features"])
    assert all(isinstance(feature["properties"], dict) for feature in payload["features"])
    assert payload["features"][0]["properties"]["C_NAME"] is None
    assert payload["features"][0]["properties"]["AREA"] is None


def test_viewport_requires_authentication_with_json_401(client, viewport_index):
    response = _get(client, viewport_index, authenticated=False)

    assert response.status_code == 401
    assert response["Content-Type"].startswith("application/json")
    assert response.json() == {"error": {"message": "authentication required"}}
    assert b"/accounts/login/" not in response.content
    assert b"FeatureCollection" not in response.content


@pytest.mark.parametrize("category", ["L", "F", "G", "H"])
def test_viewport_filters_category_in_index_query(client, viewport_index, category):
    response = _get(client, viewport_index, f"bbox=121.29,24.97,121.36,24.99&category={category}")

    assert response.status_code == 200
    features = response.json()["features"]
    assert len(features) == 1
    assert features[0]["properties"]["source_category"] == category


@pytest.mark.parametrize("query", ["category=", "category=X", "category=LF"])
def test_viewport_rejects_invalid_category(client, viewport_index, query):
    response = _get(client, viewport_index, f"bbox=121.29,24.97,121.36,24.99&{query}")
    assert response.status_code == 400


def test_viewport_applies_default_and_custom_limit(client, viewport_index):
    assert len(_get(client, viewport_index).json()["features"]) == 4
    response = _get(client, viewport_index, "bbox=121.29,24.97,121.36,24.99&limit=2")
    assert response.status_code == 200
    assert len(response.json()["features"]) == 2


@pytest.mark.parametrize("limit", ["0", "-1", "not-a-number", "1001"])
def test_viewport_rejects_invalid_limit(client, viewport_index, limit):
    response = _get(client, viewport_index, f"bbox=121.29,24.97,121.36,24.99&limit={limit}")
    assert response.status_code == 400


def test_viewport_returns_empty_feature_collection(client, viewport_index):
    response = _get(client, viewport_index, "bbox=122,25,123,26")
    assert response.status_code == 200
    result = response.json()
    assert result["type"] == "FeatureCollection" and result["features"] == []
    assert result["data_quality"]["scan_complete"]


def test_viewport_does_not_fallback_when_full_index_is_missing(client):
    missing = Path(".tmp-tests") / "phase5b-viewport" / "missing-full-index.sqlite3"
    response = _get(client, missing)
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "geologic sensitive viewport index unavailable"
