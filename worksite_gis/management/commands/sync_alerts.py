from django.core.management.base import BaseCommand

from worksite_gis.views import _sync_cwa_alerts


class Command(BaseCommand):
    help = "同步 CWA 即時警報並寫入本機快取"

    def handle(self, *args, **options):
        status, error = _sync_cwa_alerts()
        if error:
            self.stdout.write(self.style.WARNING(f"{status}: {error}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"警報同步完成：{status}"))
