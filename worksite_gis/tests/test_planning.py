import json

import pytest

from worksite_gis.models import Plan, PlanVersion, Project
from worksite_gis.planning import validate_feature_collection


def polygon_collection():
    return {"type": "FeatureCollection", "features": [{"type": "Feature", "properties": {}, "geometry": {
        "type": "Polygon", "coordinates": [[[120, 23], [121, 23], [121, 24], [120, 24], [120, 23]]]
    }}]}


def test_geometry_validation_adds_measurements():
    validated = validate_feature_collection(polygon_collection())
    assert validated["feature_count"] == 1
    assert validated["geometry"]["features"][0]["properties"]["area_m2"] > 0
    assert validated["measurement_crs"] == "EPSG:3826"


@pytest.mark.django_db
def test_saving_plan_creates_immutable_version(client, django_user_model):
    user = django_user_model.objects.create_user(username="planner-version", password="secret", is_staff=True)
    client.force_login(user)
    Project.objects.create(slug="worksite", name="WORKSITE")

    created = client.post("/api/projects/worksite/plans/", data={"name": "工區", "geometry": polygon_collection()}, content_type="application/json")
    plan_id = created.json()["plan"]["id"]
    updated = client.patch(f"/api/projects/worksite/plans/{plan_id}/", data={"name": "工區修訂"}, content_type="application/json", headers={"X-WORKSITE-Lock-Version": "1"})

    assert created.status_code == 201
    assert updated.status_code == 200
    assert list(PlanVersion.objects.filter(plan_id=plan_id).values_list("version", flat=True)) == [1, 2]
    assert Plan.objects.get(id=plan_id).version == 2


@pytest.mark.django_db
def test_plan_geojson_export(client, django_user_model):
    user = django_user_model.objects.create_user(username="plan-export-reader")
    client.force_login(user)
    project = Project.objects.create(slug="worksite", name="WORKSITE")
    plan = Plan.objects.create(project=project, name="工區", geometry=polygon_collection(), created_by=user)
    response = client.get(f"/api/projects/worksite/plans/{plan.id}/export/")
    assert response.status_code == 200
    assert response.json()["type"] == "FeatureCollection"
    assert "attachment" in response["Content-Disposition"]
