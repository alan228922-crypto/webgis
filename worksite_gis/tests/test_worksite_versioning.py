import pytest
from django.utils import timezone

from worksite_gis.integrations.geology_cloud import GeologyCloudAdapter
from worksite_gis.models import Plan, PlanVersion, Project


def worksite(lon):
    ring = [[lon, 25], [lon + .01, 25], [lon + .01, 25.01], [lon, 25.01], [lon, 25]]
    return {"type": "FeatureCollection", "features": [{"type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"source": "synthetic", "administrative_area": "synthetic"}}]}


@pytest.mark.django_db
def test_revision_events_publication_and_source_lifecycle(client, django_user_model, monkeypatch):
    from worksite_gis.models import PlanWorkflowEvent

    owner = django_user_model.objects.create_user(username="revision-owner")
    manager = django_user_model.objects.create_user(username="revision-reviewer", is_staff=True)
    reader = django_user_model.objects.create_user(username="revision-reader")
    project = Project.objects.create(slug="snapshots", name="Snapshots", owner=owner)
    base = "/api/projects/snapshots/"
    client.force_login(owner)
    created = client.post(base + "plans/", {"name": "Public v1", "geometry": worksite(121.0)}, content_type="application/json")
    assert created.status_code == 201
    payload = created.json()["plan"]
    plan_id = payload["id"]
    lock = payload["lock_version"]
    path = base + f"plans/{plan_id}/"

    def write(suffix, body=None, method="post"):
        nonlocal lock
        response = getattr(client, method)(
            path + suffix, body or {}, content_type="application/json",
            headers={"X-WORKSITE-Lock-Version": str(lock)},
        )
        assert response.status_code == 200, response.content
        lock = response.json()["plan"]["lock_version"]
        return response.json()["plan"]

    v1 = PlanVersion.objects.filter(plan_id=plan_id).values().get()
    assert client.patch(path, {"name": "missing"}, content_type="application/json").status_code == 428
    assert Plan.objects.get(pk=plan_id).lock_version == lock
    updated = write("", {"name": "Public v1"}, "patch")
    assert updated["version"] == 1
    write("submit/")
    client.force_login(manager)
    write("review/", {"decision": "return", "reason": "Return one"})
    client.force_login(owner)
    write("submit/")
    client.force_login(manager)
    write("review/", {"decision": "return", "reason": "Return two"})
    client.force_login(owner)
    deletion = client.delete(path, headers={"X-WORKSITE-Lock-Version": str(lock)})
    assert deletion.status_code == 409
    assert Plan.objects.get(pk=plan_id).lock_version == lock
    write("submit/")
    client.force_login(manager)
    write("review/", {"decision": "publish"})
    write("geology-source/")
    assert PlanVersion.objects.filter(pk=v1["id"]).values().get() == v1
    assert list(PlanWorkflowEvent.objects.filter(plan_id=plan_id, action="returned").values_list("actor_id", "revision__version", "reason")) == [(manager.pk, 1, "Return one"), (manager.pk, 1, "Return two")]

    payload = write("", {"name": "PRIVATE V2", "geometry": worksite(121.03), "metadata": {"internal": "private"}}, "patch")
    assert payload["version"] == 2 and payload["published_snapshot"]["version"] == 1
    stale = client.patch(path, {"name": "stale"}, content_type="application/json", headers={"X-WORKSITE-Lock-Version": str(lock-1)})
    assert stale.status_code == 409
    assert Plan.objects.get(pk=plan_id).name == "PRIVATE V2"

    def fake_fetch(adapter, query):
        return {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [121.005, 25.005]}, "properties": {}}]} if adapter.layer == "drill" else {"type": "FeatureCollection", "features": []}, timezone.now()

    monkeypatch.setattr(GeologyCloudAdapter, "fetch", fake_fetch)
    client.force_login(reader)
    public = client.get(base + "plans/").json()["plans"][0]
    assert public["version"] == 1 and public["name"] == "Public v1"
    assert "lock_version" not in public and "internal" not in public["metadata"]
    assert client.get(path + "export/").json() == v1["geometry"]
    assert [item["version"] for item in client.get(path + "versions/").json()["versions"]] == [1]
    assert client.get(base + "plans/ui-metadata/").json()["plans"][0]["status"] == "published"

    bbox = {"bbox": "120.99,24.99,121.07,25.07"}
    layer_url = base + "geology/drill/"
    summary_url = base + "geology/summary/"
    v1_layer = client.get(layer_url, bbox)
    v1_summary = client.get(summary_url, bbox)
    assert v1_layer.json()["screening"]["intersects"] is True
    assert v1_layer["X-WORKSITE-Worksite"] == v1_summary["X-WORKSITE-Worksite"]
    assert len(v1_summary.json()["layers"]) == 5

    client.force_login(owner)
    write("submit/")
    client.force_login(manager)
    write("review/", {"decision": "publish"})
    v2_layer = client.get(layer_url, bbox)
    assert v2_layer["X-WORKSITE-Worksite"] != v1_layer["X-WORKSITE-Worksite"]
    assert v2_layer.json()["screening"]["intersects"] is False
    Plan.objects.create(project=project, name="Other", status="approved", geometry=worksite(121.0))
    assert write("archive/")["version"] == 2
    assert Plan.objects.get(pk=plan_id).version == 2
    assert PlanVersion.objects.filter(plan_id=plan_id).count() == 2
    assert client.get(summary_url, bbox).json()["status"] == "unavailable"
    assert client.get(layer_url, bbox).json()["status"] == "unavailable"


@pytest.mark.django_db
def test_missing_legacy_snapshot_is_marked_without_a_fabricated_event(client, django_user_model, monkeypatch):
    from worksite_gis.models import PlanWorkflowEvent

    user = django_user_model.objects.create_user(username="legacy-reader")
    project = Project.objects.create(slug="legacy-source", name="Legacy")
    legacy = Plan.objects.create(project=project, name="Legacy", status="approved", geometry=worksite(121))
    client.force_login(user)
    monkeypatch.setattr(GeologyCloudAdapter, "fetch", lambda self, query: ({"type": "FeatureCollection", "features": []}, timezone.now()))
    url = "/api/projects/legacy-source/geology/drill/?bbox=120.99,24.99,121.07,25.07"
    response = client.get(url)
    assert response["X-WORKSITE-Worksite"].startswith(f"legacy_unverified:{legacy.pk}:1:")
    assert not PlanVersion.objects.filter(plan=legacy).exists()
    assert not PlanWorkflowEvent.objects.filter(plan=legacy).exists()
    Plan.objects.create(project=project, name="Ambiguous", status="published", geometry=worksite(121))
    ambiguous = client.get(url)
    assert ambiguous.json()["status"] == "unavailable"
    assert ambiguous["X-WORKSITE-Worksite-Reason"] == "ambiguous_source"
    assert "X-WORKSITE-Worksite" not in ambiguous


@pytest.mark.django_db
def test_failed_event_append_rolls_back_plan_and_related_rows(client, django_user_model, monkeypatch):
    from worksite_gis.models import PlanWorkflowEvent

    owner = django_user_model.objects.create_user(username="rollback-owner")
    project = Project.objects.create(slug="rollback", name="Rollback", owner=owner)
    plan = Plan.objects.create(project=project, name="Original", created_by=owner)
    client.force_login(owner)

    def fail(*args, **kwargs):
        raise RuntimeError("fault injection before event insert")

    monkeypatch.setattr("worksite_gis.views_plans._event", fail)
    original_plan = Plan.objects.filter(pk=plan.pk).values().get()
    original_project = Project.objects.filter(pk=project.pk).values().get()
    with pytest.raises(RuntimeError, match="fault injection"):
        client.post(f"/api/projects/rollback/plans/{plan.pk}/submit/",
                    headers={"X-WORKSITE-Lock-Version": "1"})
    assert Plan.objects.filter(pk=plan.pk).values().get() == original_plan
    assert Project.objects.filter(pk=project.pk).values().get() == original_project
    assert not PlanVersion.objects.filter(plan=plan).exists()
    assert not PlanWorkflowEvent.objects.filter(plan=plan).exists()


@pytest.mark.django_db(transaction=True)
def test_sqlite_file_two_connections_reports_contention_and_stale_separately(client, django_user_model):
    import sqlite3

    from django.db import connection

    if connection.vendor != "sqlite" or str(connection.settings_dict["NAME"]).startswith("file:memory"):
        pytest.skip("requires isolated file-backed SQLite via the test settings")
    owner = django_user_model.objects.create_user(username="two-connections")
    project = Project.objects.create(slug="sqlite-contention", name="SQLite", owner=owner)
    plan = Plan.objects.create(project=project, name="Original", created_by=owner)
    client.force_login(owner)
    url = f"/api/projects/sqlite-contention/plans/{plan.pk}/"
    headers = {"X-WORKSITE-Lock-Version": "1"}
    other = sqlite3.connect(connection.settings_dict["NAME"])
    connection.ensure_connection()
    timeout = connection.connection.execute("PRAGMA busy_timeout").fetchone()[0]
    try:
        connection.connection.execute("PRAGMA busy_timeout=10")
        other.execute("BEGIN IMMEDIATE")
        blocked = client.patch(url, {"name": "Busy"}, content_type="application/json", headers=headers)
        assert blocked.status_code == 503 and blocked.headers["Retry-After"] == "1"
        assert Plan.objects.get(pk=plan.pk).name == "Original"
        assert not PlanVersion.objects.filter(plan=plan).exists()
        other.rollback()
        winner = client.patch(url, {"name": "Winner"}, content_type="application/json", headers=headers)
        assert winner.status_code == 200
        conflict = client.patch(url, {"name": "Stale"}, content_type="application/json", headers=headers)
        assert conflict.status_code == 409
        assert Plan.objects.get(pk=plan.pk).name == "Winner"
        assert list(PlanVersion.objects.filter(plan=plan).values_list("version", flat=True)) == [2]
    finally:
        other.close()
        connection.connection.execute(f"PRAGMA busy_timeout={timeout}")


@pytest.mark.django_db(transaction=True)
def test_additive_migration_preserves_preexisting_versions_without_backfill():
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor

    previous = [("worksite_gis", "0013_role_permissions")]
    target = [("worksite_gis", "0014_worksite_publication")]
    executor = MigrationExecutor(connection)
    executor.migrate(previous)
    try:
        old = executor.loader.project_state(previous).apps
        project = old.get_model("worksite_gis", "Project").objects.create(slug="migrated", name="Migrated")
        plan = old.get_model("worksite_gis", "Plan").objects.create(project=project, status="approved", version=2, name="Old")
        old.get_model("worksite_gis", "PlanVersion").objects.create(plan=plan, version=3, status="archived", name="Old extra")
    finally:
        MigrationExecutor(connection).migrate(target)
    from worksite_gis.models import PlanWorkflowEvent

    plan = Plan.objects.get(project__slug="migrated")
    assert (plan.status, plan.version, plan.lock_version, plan.publication_event_id) == ("approved", 2, 1, None)
    assert list(plan.versions.values_list("version", "status", "name")) == [(3, "archived", "Old extra")]
    assert not PlanWorkflowEvent.objects.filter(plan=plan).exists()


@pytest.mark.django_db
def test_revising_a_submitted_plan_requires_a_fresh_submission(client, django_user_model):
    from worksite_gis.models import PlanWorkflowEvent

    manager = django_user_model.objects.create_user(username="edit-during-review", is_staff=True)
    project = Project.objects.create(slug="edit-during-review", name="Review")
    plan = Plan.objects.create(project=project, name="Original", status="submitted", geometry=worksite(121))
    client.force_login(manager)
    url = f"/api/projects/edit-during-review/plans/{plan.pk}/"
    edited = client.patch(url, {"geometry": worksite(121.02)}, content_type="application/json", headers={"X-WORKSITE-Lock-Version": "1"})
    assert edited.status_code == 200
    assert edited.json()["plan"]["status"] == "draft"
    with pytest.raises(ValueError, match="immutable"):
        plan.versions.update(name="invalid")
    failed = client.post(url + "review/", {"decision": "publish"}, content_type="application/json", headers={"X-WORKSITE-Lock-Version": "2"})
    assert failed.status_code == 400
    assert not PlanWorkflowEvent.objects.filter(plan=plan, action="published").exists()
    assert Plan.objects.get(pk=plan.pk).lock_version == 2


@pytest.mark.django_db(transaction=True)
def test_two_sqlite_editors_cannot_both_change_the_same_generation(django_user_model, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from django.db import connection, connections
    from django.test import Client

    from worksite_gis import views_plans

    if connection.vendor != "sqlite" or str(connection.settings_dict["NAME"]).startswith("file:memory"):
        pytest.skip("requires isolated file-backed SQLite via the test settings")
    user = django_user_model.objects.create_user(username="parallel-editor", is_staff=True)
    project = Project.objects.create(slug="parallel-editor", name="Parallel")
    plan = Plan.objects.create(project=project, name="Starting", created_by=user)
    client = Client()
    client.force_login(user)
    both_read = Barrier(2, timeout=5)
    write = views_plans._write_plan

    def synchronize(*args, **kwargs):
        both_read.wait()
        return write(*args, **kwargs)

    monkeypatch.setattr(views_plans, "_write_plan", synchronize)

    def edit(name):
        other = Client()
        other.cookies = client.cookies.copy()
        try:
            return other.patch(
                f"/api/projects/parallel-editor/plans/{plan.pk}/",
                {"name": name}, content_type="application/json",
                headers={"X-WORKSITE-Lock-Version": "1"},
            ).status_code
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as editors:
        results = sorted(editors.map(edit, ["Editor A", "Editor B"]))
    assert results in ([200, 409], [200, 503])
    plan.refresh_from_db()
    assert plan.lock_version == 2 and plan.version == 2
    assert list(plan.versions.values_list("name", flat=True)) == [plan.name]
