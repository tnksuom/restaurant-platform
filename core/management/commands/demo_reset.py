from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

from core.images import ImageStore


class Command(BaseCommand):
    help = (
        "DEMO ONLY: wipe every record and uploaded photo, then load the example restaurant again. "
        "Refuses to run unless DEMO_MODE=true."
    )

    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("demo_reset only runs with DEMO_MODE=true. It deletes all data.")
        # TRUNCATE empties every table, including the append-only history tables
        # (their triggers guard row updates and deletes, not a full reset).
        call_command("flush", interactive=False, verbosity=0)
        removed = 0
        for root in (settings.PRIVATE_UPLOAD_ROOT, settings.PUBLIC_UPLOAD_ROOT):
            for path in ImageStore(root).root().iterdir():
                if path.is_file():
                    path.unlink()
                    removed += 1
        call_command("seed_demo", password=settings.DEMO_PASSWORD, stdout=self.stdout)
        self.stdout.write(self.style.SUCCESS(f"Demo reset: database refilled, {removed} old photo(s) removed."))
