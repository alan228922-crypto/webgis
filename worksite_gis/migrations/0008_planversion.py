from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("worksite_gis", "0007_apisnapshot")]
    operations = [
        migrations.CreateModel(
            name="PlanVersion",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.PositiveIntegerField()),
                ("name", models.CharField(max_length=200)),
                ("purpose", models.CharField(blank=True, max_length=200)),
                ("status", models.CharField(max_length=20)),
                ("geometry", models.JSONField(default=dict)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("plan", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="versions", to="worksite_gis.plan")),
            ], options={"ordering": ["version"]},
        ),
        migrations.AddConstraint(model_name="planversion", constraint=models.UniqueConstraint(fields=("plan", "version"), name="unique_plan_version")),
    ]
