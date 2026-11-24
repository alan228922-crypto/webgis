from django.conf import settings
from django.db import models


class LayerCatalog(models.Model):
    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    category = models.CharField(max_length=50)
    format = models.CharField(max_length=20, default="geojson")
    path = models.CharField(max_length=500)
    source = models.CharField(max_length=500, blank=True)
    crs = models.CharField(max_length=32, blank=True)
    feature_count = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["category", "slug"]


class ImportRun(models.Model):
    source = models.CharField(max_length=500)
    output = models.CharField(max_length=500)
    status = models.CharField(max_length=20)
    source_hash = models.CharField(max_length=64, blank=True)
    output_hash = models.CharField(max_length=64, blank=True)
    feature_count = models.PositiveIntegerField(default=0)
    validation_report = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class StationSeed(models.Model):
    kind = models.CharField(max_length=50)
    external_id = models.CharField(max_length=150)
    name = models.CharField(max_length=200)
    latitude = models.FloatField()
    longitude = models.FloatField()
    properties = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["kind", "external_id"], name="unique_station_seed")]
        indexes = [models.Index(fields=["kind", "external_id"], name="ws_station_kind_external_idx")]


class Alert(models.Model):
    source = models.CharField(max_length=100)
    severity = models.CharField(max_length=30)
    title = models.CharField(max_length=250)
    payload = models.JSONField(default=dict, blank=True)
    occurred_at = models.DateTimeField()
    cleared_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["occurred_at"], name="ws_alert_occurred_idx")]

    @classmethod
    def record(cls, **values):
        alert = cls.objects.create(**values)
        obsolete = cls.objects.order_by("-occurred_at", "-id").values_list("id", flat=True)[50:]
        cls.objects.filter(id__in=list(obsolete)).delete()
        return alert


class ApiServiceConfig(models.Model):
    STATUS_CHOICES = [("unknown", "Unknown"), ("healthy", "Healthy"), ("degraded", "Degraded"), ("error", "Error")]

    name = models.CharField(max_length=200)
    slug = models.SlugField(unique=True)
    base_url = models.URLField(blank=True)
    endpoint = models.CharField(max_length=300, blank=True)
    auth_env = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="unknown")
    last_checked_at = models.DateTimeField(null=True, blank=True)
    response_time_ms = models.PositiveIntegerField(null=True, blank=True)
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    error_summary = models.CharField(max_length=500, blank=True)
    update_frequency = models.CharField(max_length=100, blank=True)
    data_format = models.CharField(max_length=50, blank=True)
    attribution = models.CharField(max_length=300, blank=True)
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]


class ApiSnapshot(models.Model):
    service = models.SlugField()
    kind = models.SlugField()
    fetched_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    payload = models.JSONField(default=list)
    status = models.CharField(max_length=20, default="ok")
    error_summary = models.CharField(max_length=300, blank=True)

    class Meta:
        ordering = ["-fetched_at"]
        indexes = [models.Index(fields=["service", "kind", "-fetched_at"], name="ws_api_service_kind_fetch_idx")]


class Project(models.Model):
    STATUS_CHOICES = [("draft", "Draft"), ("active", "Active"), ("archived", "Archived")]

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=200)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_projects")
    description = models.TextField(blank=True)
    SETUP_STATUS_CHOICES = [("unlocated", "未定位"), ("located", "已定位"), ("editing", "編輯中"), ("saved", "已保存")]
    center_latitude = models.FloatField(default=24.985556)
    center_longitude = models.FloatField(default=121.310278)
    default_zoom = models.PositiveSmallIntegerField(default=14)
    display_bounds = models.JSONField(default=list, blank=True)
    measurement_crs = models.CharField(max_length=32, default="EPSG:3826", blank=True)
    search_center_latitude = models.FloatField(null=True, blank=True)
    search_center_longitude = models.FloatField(null=True, blank=True)
    search_radius_km = models.FloatField(default=10)
    search_center_label = models.CharField(max_length=250, blank=True)
    search_center_address = models.CharField(max_length=500, blank=True)
    search_center_provider = models.CharField(max_length=50, blank=True)
    search_center_raw_id = models.CharField(max_length=250, blank=True)
    search_center_precision = models.CharField(max_length=50, blank=True)
    search_center_searched_at = models.DateTimeField(null=True, blank=True)
    setup_status = models.CharField(max_length=20, choices=SETUP_STATUS_CHOICES, default="unlocated")
    timezone = models.CharField(max_length=64, default="Asia/Taipei")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    geology_generation = models.PositiveBigIntegerField(default=0, editable=False)
    geology_source_plan = models.ForeignKey(
        "Plan", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )

    class Meta:
        ordering = ["name"]


class Plan(models.Model):
    STATUS_CHOICES = [
        ("draft", "草稿"),
        ("submitted", "送審"),
        ("needs_revision", "需修訂"),
        ("published", "已發布"),
        ("archived", "已封存"),
        ("review", "送審（舊值）"),
        ("approved", "已核定（舊值）"),
        ("rejected", "退回（舊值）"),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="plans")
    name = models.CharField(max_length=200)
    purpose = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    version = models.PositiveIntegerField(default=1)
    geometry = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="created_plans")
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="updated_plans")
    return_reason = models.TextField(null=True, blank=True)
    lock_version = models.PositiveBigIntegerField(default=1, editable=False)
    publication_event = models.ForeignKey(
        "PlanWorkflowEvent", null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["project", "name"]


class _HistoricalRows(models.QuerySet):
    def update(self, **kwargs):
        # A removed account may detach its FK; the recorded event/content stays unchanged.
        actor_fields = {"actor", "actor_id", "created_by", "created_by_id"}
        if kwargs and all(field in actor_fields and value is None for field, value in kwargs.items()):
            return super().update(**kwargs)
        raise ValueError("Historical worksite records are immutable")


class _HistoricalRecord(models.Model):
    objects = _HistoricalRows.as_manager()

    class Meta:
        abstract = True

    def save(self, *, force_insert=False, force_update=False, using=None, update_fields=None):
        if not self._state.adding or force_update or update_fields is not None:
            raise ValueError("Historical worksite records are immutable")
        return super().save(force_insert=True, using=using)


class PlanVersion(_HistoricalRecord):
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    purpose = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=20)
    geometry = models.JSONField(default=dict)
    metadata = models.JSONField(default=dict, blank=True)
    change_summary = models.CharField(max_length=500, blank=True)
    return_reason = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="plan_versions")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["version"]
        constraints = [models.UniqueConstraint(fields=["plan", "version"], name="unique_plan_version")]


class PlanWorkflowEvent(_HistoricalRecord):
    class Action(models.TextChoices):
        CREATED = "created", "Created"
        SUBMITTED = "submitted", "Submitted"
        RETURNED = "returned", "Returned"
        RESUBMITTED = "resubmitted", "Resubmitted"
        PUBLISHED = "published", "Published"
        ARCHIVED = "archived", "Archived"
        SOURCE_SELECTED = "source_selected", "Geology source selected"

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="workflow_events")
    revision = models.ForeignKey(PlanVersion, on_delete=models.CASCADE, related_name="workflow_events")
    action = models.CharField(max_length=24, choices=Action.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    actor_label = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["id"]


class CctvImportBatch(models.Model):
    source = models.CharField(max_length=100)
    source_file = models.CharField(max_length=500)
    imported_at = models.DateTimeField(auto_now_add=True)
    imported_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    invalid_count = models.PositiveIntegerField(default=0)
    error_summary = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-imported_at"]


class CctvCamera(models.Model):
    provider = models.CharField(max_length=100)
    source_type = models.CharField(max_length=100, blank=True)
    camera_id = models.CharField(max_length=200)
    city = models.CharField(max_length=100, blank=True)
    road_name = models.CharField(max_length=200, blank=True)
    location_name = models.CharField(max_length=300, blank=True)
    longitude = models.FloatField()
    latitude = models.FloatField()
    stream_url = models.URLField(max_length=1000, blank=True)
    media_type = models.CharField(max_length=30, blank=True)
    snapshot_date = models.DateField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now=True)
    source_file = models.CharField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["provider", "camera_id"]
        constraints = [models.UniqueConstraint(fields=["provider", "camera_id"], name="unique_cctv_provider_camera")]
        indexes = [models.Index(fields=["latitude", "longitude"], name="ws_camera_lat_lon_idx")]
