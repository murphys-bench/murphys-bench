"""Stamp this install as initialised, so demo seeding can never run again.

scripts/install.sh calls this once it has finished a successful install —
including a `--no-demo-data` install, which is exactly the case a data-shaped
guard cannot see. From then on `seed_demo_data` declines regardless of what the
tables hold, which is what makes re-running the installer on a live shop safe.

Idempotent: the first stamp wins, so a later re-run never moves the date.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import SiteSettings


class Command(BaseCommand):
    help = 'Mark this install as initialised (blocks demo-data seeding from here on).'

    def handle(self, *args, **options):
        site = SiteSettings.get()
        if site.install_initialized_at:
            self.stdout.write(
                f'Already marked initialised on {site.install_initialized_at:%Y-%m-%d}.'
            )
            return

        site.install_initialized_at = timezone.now()
        site.save(update_fields=['install_initialized_at'])
        self.stdout.write(self.style.SUCCESS('Install marked initialised.'))
