from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def repair_worksite(apps, schema_editor):
    Project = apps.get_model("worksite_gis", "Project")
    Project.objects.filter(slug="worksite").update(
        name="Worksite GIS — Engineering Geology Screening",
        center_latitude=24.985556,
        center_longitude=121.310278,
        default_zoom=14,
        timezone="Asia/Taipei",
        measurement_crs="EPSG:3826",
    )


class Migration(migrations.Migration):
    dependencies = [("worksite_gis", "0008_planversion"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.AddField(model_name="project", name="display_bounds", field=models.JSONField(blank=True, default=list)),
        migrations.AddField(model_name="project", name="measurement_crs", field=models.CharField(blank=True, default="EPSG:3826", max_length=32)),
        migrations.AddField(model_name="project", name="search_center_address", field=models.CharField(blank=True, max_length=500)),
        migrations.AddField(model_name="project", name="search_center_label", field=models.CharField(blank=True, max_length=250)),
        migrations.AddField(model_name="project", name="search_center_precision", field=models.CharField(blank=True, max_length=50)),
        migrations.AddField(model_name="project", name="search_center_provider", field=models.CharField(blank=True, max_length=50)),
        migrations.AddField(model_name="project", name="search_center_raw_id", field=models.CharField(blank=True, max_length=250)),
        migrations.AddField(model_name="project", name="search_center_searched_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="project", name="setup_status", field=models.CharField(choices=[("unlocated", "未定位"), ("located", "已定位"), ("editing", "編輯中"), ("saved", "已保存")], default="unlocated", max_length=20)),
        migrations.AlterField(model_name="project", name="center_latitude", field=models.FloatField(default=24.985556)),
        migrations.AlterField(model_name="project", name="center_longitude", field=models.FloatField(default=121.310278)),
        migrations.AlterField(model_name="project", name="default_zoom", field=models.PositiveSmallIntegerField(default=14)),
        migrations.AlterField(model_name="plan", name="status", field=models.CharField(choices=[("draft", "草稿"), ("review", "送審"), ("approved", "已核定"), ("rejected", "退回")], default="draft", max_length=20)),
        migrations.AddField(model_name="plan", name="created_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_plans", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="plan", name="updated_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="updated_plans", to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name="planversion", name="change_summary", field=models.CharField(blank=True, max_length=500)),
        migrations.AddField(model_name="planversion", name="created_by", field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="plan_versions", to=settings.AUTH_USER_MODEL)),
        migrations.CreateModel(name="CctvImportBatch", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("source", models.CharField(max_length=100)), ("source_file", models.CharField(max_length=500)), ("imported_at", models.DateTimeField(auto_now_add=True)), ("imported_count", models.PositiveIntegerField(default=0)), ("updated_count", models.PositiveIntegerField(default=0)), ("invalid_count", models.PositiveIntegerField(default=0)), ("error_summary", models.JSONField(blank=True, default=list))], options={"ordering": ["-imported_at"]}),
        migrations.CreateModel(name="CctvCamera", fields=[("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")), ("provider", models.CharField(max_length=100)), ("source_type", models.CharField(blank=True, max_length=100)), ("camera_id", models.CharField(max_length=200)), ("city", models.CharField(blank=True, max_length=100)), ("road_name", models.CharField(blank=True, max_length=200)), ("location_name", models.CharField(blank=True, max_length=300)), ("longitude", models.FloatField()), ("latitude", models.FloatField()), ("stream_url", models.URLField(blank=True, max_length=1000)), ("media_type", models.CharField(blank=True, max_length=30)), ("snapshot_date", models.DateField(blank=True, null=True)), ("imported_at", models.DateTimeField(auto_now=True)), ("source_file", models.CharField(blank=True, max_length=500)), ("metadata", models.JSONField(blank=True, default=dict))], options={"ordering": ["provider", "camera_id"]}),
        migrations.AddConstraint(model_name="cctvcamera", constraint=models.UniqueConstraint(fields=("provider", "camera_id"), name="unique_cctv_provider_camera")),
        migrations.AddIndex(model_name="cctvcamera", index=models.Index(fields=["latitude", "longitude"], name="ws_camera_lat_lon_idx")),
        migrations.RunPython(repair_worksite, migrations.RunPython.noop),
    ]
