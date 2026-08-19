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

from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import SiteSettings, Ticket

logger = logging.getLogger(__name__)


def auto_close_resolved(now=None):
    """Flip Resolved tickets to Closed once their reopen window has expired.

    Ruling (Aug 19 2026): the shop sets one window (Settings > Reopen Window).
    Inside it a client reply threads in and flags the still-Resolved ticket;
    once it has run out the ticket is Closed automatically with no client
    email and nothing for a tech to remember. "Silent" means no email: each
    flip IS recorded, in the ticket's audit history (Settings > Logs > Record
    Changes) and the app log.

    The window rule is shared with the inbound reply path
    (Ticket.past_reopen_window / within_reopen_window), so the instant a
    reply would start a new linked ticket is the instant auto-close applies.
    Tickets with no closed_at are inside the window forever there, so they are
    left alone here too.

    Each ticket is saved individually with update_fields=['status'] so the
    auditlog post_save hook records the change, while updated_at stays put
    (it is not in update_fields), so the "closed in period" report numbers do
    not shift on a timer tick. Returns the number of tickets closed."""
    now = now or timezone.now()
    window_days = SiteSettings.get().ticket_reopen_window_days
    closed = []
    for ticket in Ticket.past_reopen_window(now, window_days):
        ticket.status = 'closed'
        ticket.save(update_fields=['status'])
        closed.append(ticket.ticket_number)
    if closed:
        logger.info(
            'Auto-closed %d resolved ticket(s) past the %d-day reopen window: %s',
            len(closed), window_days, ', '.join(closed),
        )
    return len(closed)


class Command(BaseCommand):
    help = 'Check for newly overdue tickets and report counts; auto-close resolved tickets past the reopen window.'

    def handle(self, *args, **options):
        now = timezone.now()
        self._sla_report(now)
        # Auto-close runs AFTER the SLA report and inside its own guard, so a
        # fault here can never stop the existing SLA job from doing its work.
        try:
            closed = auto_close_resolved(now)
        except Exception:
            logger.exception('Auto-close of resolved tickets failed; SLA check unaffected.')
            self.stderr.write('[CLOSE] auto-close failed; see app log.')
        else:
            if closed:
                self.stdout.write(f'[CLOSE] {now:%Y-%m-%d %H:%M} — {closed} resolved ticket(s) closed (reopen window expired).')

    def _sla_report(self, now):
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
