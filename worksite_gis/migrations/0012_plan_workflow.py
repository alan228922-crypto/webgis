from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("worksite_gis", "0011_project_owner")]

    operations = [
        migrations.AlterField(
            model_name="plan",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "草稿"),
                    ("submitted", "送審"),
                    ("needs_revision", "需修訂"),
                    ("published", "已發布"),
                    ("archived", "已封存"),
                    ("review", "送審（舊值）"),
                    ("approved", "已核定（舊值）"),
                    ("rejected", "退回（舊值）"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="plan",
            name="return_reason",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="planversion",
            name="return_reason",
            field=models.TextField(blank=True, null=True),
        ),
    ]
