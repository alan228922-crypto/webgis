from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("worksite_gis", "0006_plan")]
    operations = [
        migrations.CreateModel(
            name="ApiSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("service", models.SlugField()),
                ("kind", models.SlugField()),
                ("fetched_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                ("payload", models.JSONField(default=list)),
                ("status", models.CharField(default="ok", max_length=20)),
                ("error_summary", models.CharField(blank=True, max_length=300)),
            ],
            options={"ordering": ["-fetched_at"]},
        ),
        migrations.AddIndex(
            model_name="apisnapshot",
            index=models.Index(fields=["service", "kind", "-fetched_at"], name="ws_api_service_kind_fetch_idx"),
        ),
    ]
