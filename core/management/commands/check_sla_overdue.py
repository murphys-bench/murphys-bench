"""
Management command: check_sla_overdue

Run via cron every 15 minutes to log newly-overdue tickets, and to close
Resolved tickets whose reopen window has run out (see auto_close_resolved).
This command does not send emails — SLA alerts are in-app only, and the
auto-close is silent by design.

Example crontab:
    */15 * * * * /path/to/venv/bin/python /path/to/manage.py check_sla_overdue
"""
import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import SiteSettings, Ticket

logger = logging.getLogger(__name__)


def auto_close_resolved(now=None):
    """Flip Resolved tickets to Closed once their reopen window has expired.

    Ruling (Aug 19 2026): the shop sets one window (Settings > Reopen Window).
    Inside it a client reply threads in and flags the still-Resolved ticket;
    once it has run out the ticket is Closed automatically, silently: no
    client email, no status-changed notice, nothing for a tech to remember.
    Resolved and Closed are treated identically everywhere else in MB, so the
    only visible change is the label.

    Uses the same age rule as the inbound reply path (closed_at + window
    days). Tickets with no closed_at (pre-reopen-window history) are treated
    there as forever inside the window, so they are left alone here too.
    A queryset update is deliberate: updated_at must NOT move, or the
    "closed in period" report numbers would shift on a timer tick.
    Returns the number of tickets closed."""
    now = now or timezone.now()
    window_days = SiteSettings.get().ticket_reopen_window_days
    cutoff = now - timedelta(days=window_days)
    qs = Ticket.objects.filter(status='resolved', closed_at__lt=cutoff)
    numbers = list(qs.values_list('ticket_number', flat=True))
    if not numbers:
        return 0
    closed = qs.update(status='closed')
    logger.info(
        'Auto-closed %d resolved ticket(s) past the %d-day reopen window: %s',
        closed, window_days, ', '.join(numbers),
    )
    return closed


class Command(BaseCommand):
    help = 'Check for newly overdue tickets and report counts; auto-close resolved tickets past the reopen window.'

    def handle(self, *args, **options):
        now = timezone.now()
        closed = auto_close_resolved(now)
        if closed:
            self.stdout.write(f'[CLOSE] {now:%Y-%m-%d %H:%M} — {closed} resolved ticket(s) closed (reopen window expired).')
        # Single source of truth — mirrors Ticket.is_overdue (incl. first_responded_at).
        overdue_qs = Ticket.overdue_queryset()

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
