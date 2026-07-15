"""Regenerate the backup destination files (backup-config.env + .rclone.conf)
from the current SiteSettings.

The admin Backups form renders these on save, but the files live outside the repo
and could be missing on a fresh box, after a restore, or after a disk wipe — in
which case mb_backup.sh would silently fall back to local-only. Running this on
every deploy keeps the on-disk files in sync with the configured destination.
"""
from django.core.management.base import BaseCommand

from core import backup_ops
from core.models import SiteSettings


class Command(BaseCommand):
    help = 'Render backup-config.env (+ .rclone.conf) from SiteSettings.'

    def handle(self, *args, **options):
        site = SiteSettings.get()
        backup_ops.render_config(site)
        dests = []
        if site.backup_onsite_enabled:
            dests.append(f'onsite:{backup_ops.onsite_remote_target(site)}')
        if site.backup_offsite_enabled:
            dests.append(f'offsite:{backup_ops.rclone_remote_target(site)}')
        summary = ' + '.join(dests) if dests else 'no destination configured'
        self.stdout.write(self.style.SUCCESS(f'Backup config rendered — destinations: {summary}'))
