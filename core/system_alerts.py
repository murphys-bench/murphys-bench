"""System self-monitoring → a ticket you already watch.

`create_system_alert` is the single internal channel for MB's own operational
alerts (failed jobs, disk pressure, unhandled 500s). Rather than depending on
outbound mail (the box can't send system mail, and "mail is broken" is itself a
thing we'd want to alert on), it writes the alert straight into MB as a Ticket
under a dedicated "System Alerts" client, then pings the admin notification bell.

Callers: `manage.py send_alert` (systemd OnFailure, the backup script, the disk
check) and the app's 500 handler. The backup *liveness* gap (a job that never
runs) is covered separately by the external healthchecks.io heartbeat.
"""
import logging
from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger('core')

SYSTEM_ALERTS_CLIENT_NAME = 'System Alerts'


def create_system_alert(subject, body='', *, dedupe_minutes=60):
    """Create (or reuse) a System Alert ticket and notify admins.

    Returns the Ticket. To avoid a flapping failure spamming the queue, an open
    alert with the same subject created within `dedupe_minutes` is reused instead
    of opening a new one (pass dedupe_minutes=0 to force a fresh ticket).
    """
    from .models import Client, Ticket, Notification, User

    subject = (subject or 'System alert')[:255]
    body = body or subject

    client, _ = Client.objects.get_or_create(
        name=SYSTEM_ALERTS_CLIENT_NAME,
        defaults={'is_active': True},
    )

    if dedupe_minutes:
        cutoff = timezone.now() - timedelta(minutes=dedupe_minutes)
        existing = (
            Ticket.objects
            .filter(client=client, subject=subject, created_at__gte=cutoff)
            .exclude(status__in=['closed', 'resolved'])
            .first()
        )
        if existing:
            logger.warning(
                'System alert suppressed (duplicate within %dm): %s',
                dedupe_minutes, subject,
            )
            return existing

    ticket = Ticket(
        ticket_number=Ticket.generate_ticket_number(),
        client=client,
        subject=subject,
        description=body,
        source='system',
        status='new',
        created_by=None,
    )
    ticket.save()

    admins = User.objects.filter(
        Q(is_staff=True) | Q(role_obj__can_manage_settings=True)
    ).distinct()
    for u in admins:
        Notification.objects.create(
            recipient=u, kind='system_alert', text=subject, ticket=ticket,
        )

    logger.warning('System alert ticket %s created: %s', ticket.ticket_number, subject)
    return ticket
