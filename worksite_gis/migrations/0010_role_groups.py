from django.db import migrations


def create_and_map_roles(apps, schema_editor):
    from worksite_gis.permissions import sync_existing_user_roles

    sync_existing_user_roles(using=schema_editor.connection.alias)


class Migration(migrations.Migration):
    dependencies = [("worksite_gis", "0009_dashboard_rebuild")]

    operations = [migrations.RunPython(create_and_map_roles, migrations.RunPython.noop)]
