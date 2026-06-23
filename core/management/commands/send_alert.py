"""CLI entry to MB's internal alert channel.

Used by systemd OnFailure handlers, the backup script, and the disk-usage check
to turn an operational failure into a System Alert ticket. Example:

    venv/bin/python manage.py send_alert "Backup failed" "see logs/backup.log"
"""
from django.core.management.base import BaseCommand

from core.system_alerts import create_system_alert


class Command(BaseCommand):
    help = 'Create a System Alert ticket (used by backup/systemd/disk monitors).'

    def add_arguments(self, parser):
        parser.add_argument('subject', help='Short alert subject.')
        parser.add_argument('body', nargs='?', default='', help='Optional detail body.')
        parser.add_argument(
            '--no-dedupe', action='store_true',
            help='Always create a fresh ticket (skip the duplicate-subject window).',
        )

    def handle(self, *args, **options):
        ticket = create_system_alert(
            options['subject'],
            options['body'],
            dedupe_minutes=0 if options['no_dedupe'] else 60,
        )
        self.stdout.write(self.style.SUCCESS(f'system alert: {ticket.ticket_number}'))
