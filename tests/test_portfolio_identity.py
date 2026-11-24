from django.apps import apps
from django.contrib.auth.models import Permission
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder
from django.test import TestCase


class PortfolioIdentityTests(TestCase):
    def test_fresh_database_uses_worksite_namespace(self):
        config = apps.get_app_config("worksite_gis")
        self.assertEqual(config.name, "worksite_gis")
        self.assertIn(("worksite_gis", "0014_worksite_publication"),
                      MigrationRecorder(connection).applied_migrations())
        tables = connection.introspection.table_names()
        self.assertIn("worksite_gis_planworkflowevent", tables)
        self.assertIn("worksite_gis_planversion", tables)
        self.assertTrue(Permission.objects.filter(
            content_type__app_label="worksite_gis", codename="view_plan",
        ).exists())

    def test_demo_identity(self):
        from worksite_gis.demo import DEMO_PROJECT_SLUG

        self.assertEqual(DEMO_PROJECT_SLUG, "synthetic-worksite")
