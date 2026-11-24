import pytest

from worksite_gis.models import Alert, ApiServiceConfig, LayerCatalog, Plan, Project


@pytest.mark.django_db
def test_layer_catalog_slug_is_unique():
    LayerCatalog.objects.create(
        slug="mrt_stations",
        name="捷運車站",
        category="transport",
        format="geojson",
        path="data/processed/gis/transport/mrt_stations.geojson",
    )
    assert LayerCatalog.objects.filter(slug="mrt_stations").count() == 1


@pytest.mark.django_db
def test_api_service_config_stores_health_metadata():
    service = ApiServiceConfig.objects.create(
        name="Weather", slug="weather", base_url="https://example.test",
        endpoint="/observations", status="unknown",
    )
    assert service.slug == "weather"
    assert service.status == "unknown"


@pytest.mark.django_db
def test_project_stores_map_defaults():
    project = Project.objects.create(slug="worksite", name="WORKSITE PoC", center_latitude=23.7, center_longitude=120.9, default_zoom=7)
    assert project.default_zoom == 7


@pytest.mark.django_db
def test_alert_can_be_cleared():
    from django.utils import timezone
    alert = Alert.objects.create(source="test", severity="red", title="Test", occurred_at=timezone.now())
    assert alert.cleared_at is None


@pytest.mark.django_db
def test_plan_belongs_to_project():
    project = Project.objects.create(slug="worksite", name="WORKSITE PoC")
    plan = Plan.objects.create(project=project, name="施工分區", purpose="工區規劃")
    assert plan.project.slug == "worksite"
