"""
Management command: check_sla_overdue

Run via cron every 15 minutes to log newly-overdue tickets.
This command does not send emails — SLA alerts are in-app only.

Example crontab:
    */15 * * * * /path/to/venv/bin/python /path/to/manage.py check_sla_overdue
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import Ticket


class Command(BaseCommand):
    help = 'Check for newly overdue tickets and report counts.'

    def handle(self, *args, **options):
        now = timezone.now()
        open_statuses = ['new', 'open', 'in_progress', 'waiting_on_customer']

        overdue_qs = Ticket.objects.filter(
            status__in=open_statuses,
            due_at__lt=now,
            due_at__isnull=False,
        ).exclude(
            sla_plan__disable_overdue_alerts=True,
        )

        total = overdue_qs.count()
        unacked = overdue_qs.filter(overdue_acknowledged_at__isnull=True).count()

        self.stdout.write(
            self.style.WARNING(
                f'[SLA] {now:%Y-%m-%d %H:%M} — {total} overdue ticket(s), {unacked} unacknowledged.'
            )
        )
        if unacked:
            for ticket in overdue_qs.filter(overdue_acknowledged_at__isnull=True).select_related('client')[:20]:
                self.stdout.write(f'  ⚠  {ticket.ticket_number} — {ticket.client.name} — due {ticket.due_at:%Y-%m-%d %H:%M}')
