"""Search across MB's audit trails, as one stream or one source at a time.

MB keeps five independent logs — outbound email, inbound email, org credential
access, device credential access, and the model audit log. They answer two
different kinds of question, and the Logs tab serves both from one page:

  * "What happened around 9:01 PM?" — incident forensics, which needs every
    source interleaved in time. `unified()` does that, merging in memory because
    the sources are separate tables with no common ordering column. It is
    deliberately bounded: it is an overview, not an archive.

  * "Did that email to Wayne send?" — a source-specific question, which needs
    that log's own columns and its full history. `search_source()` returns a real
    queryset so the view can page it in the database, all the way back.

Which of those a shop reaches for first differs by shop, so the page defaults to
the unified stream and the source filter is one click away rather than a stored
setting. If that default ever needs to change, it is `LogQuery.source` picking a
branch in the view — nothing here assumes one is primary.
"""
from dataclasses import dataclass
from datetime import datetime, time

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

#: Cap on rows pulled from each source before merging. Five sources, so the
#: unified view shows at most 5x this before trimming to `UNIFIED_LIMIT`.
PER_SOURCE_FETCH = 200
UNIFIED_LIMIT = 300


@dataclass(frozen=True)
class LogRow:
    """One event, normalized so five different models can share a table."""
    when: datetime
    source: str
    source_label: str
    actor: str
    summary: str
    detail: str
    url: str = ''


def _person(user):
    if user is None:
        return 'System'
    return user.get_full_name() or user.username


# ── Per-source adapters ──────────────────────────────────────────────────────
# Each knows three things: how to get its queryset, which fields a text search
# should cover, and how to flatten one row into a LogRow.

def _email_out_qs():
    from .models import EmailSendLog
    return EmailSendLog.objects.select_related('ticket')


def _email_out_row(e):
    # `reason` declares REASON_CHOICES but the field never binds them, so there
    # is no display method — the raw value is what the old table showed too.
    bits = [b for b in (e.get_status_display(), e.detail or e.reason) if b]
    return LogRow(
        when=e.created_at, source='email_out', source_label='Outbound Email',
        actor=e.to_email or '—',
        summary=f'{e.trigger} → {e.to_email or "no address"}',
        detail=' · '.join(bits),
        url=reverse('core:ticket_detail', args=[e.ticket_id]) if e.ticket_id else '',
    )


def _email_in_qs():
    from .models import InboundEmailLog
    return InboundEmailLog.objects.select_related('ticket')


def _email_in_row(e):
    return LogRow(
        when=e.created_at, source='email_in', source_label='Inbound Email',
        actor=e.from_email or '—',
        summary=e.subject or '(no subject)',
        detail=e.get_status_display(),
        url=reverse('core:ticket_detail', args=[e.ticket_id]) if e.ticket_id else '',
    )


def _org_cred_qs():
    from .models import CredentialAccessLog
    return CredentialAccessLog.objects.select_related('credential', 'user')


def _org_cred_row(e):
    return LogRow(
        when=e.accessed_at, source='org_cred', source_label='Org Credentials',
        actor=_person(e.user),
        summary=f'{e.get_action_display()} "{e.credential.name}"',
        detail='',
    )


def _device_cred_qs():
    from .models import DeviceCredentialAccessLog
    return DeviceCredentialAccessLog.objects.select_related('device', 'user')


def _device_cred_row(e):
    bits = []
    if e.field:
        bits.append(e.field)
    if e.replaced_existing:
        bits.append('replaced an existing value')
    return LogRow(
        when=e.accessed_at, source='device_cred', source_label='Device Credentials',
        actor=_person(e.user),
        summary=f'{e.get_action_display()} credentials on {e.device.name}',
        detail=' · '.join(bits),
        url=reverse('core:device_detail', args=[e.device_id]),
    )


_AUDIT_ACTIONS = {0: 'Created', 1: 'Updated', 2: 'Deleted'}


def _audit_qs():
    from auditlog.models import LogEntry
    return LogEntry.objects.select_related('actor', 'content_type')


def _audit_row(e):
    return LogRow(
        when=e.timestamp, source='audit', source_label='Record Changes',
        actor=_person(e.actor),
        summary=f'{_AUDIT_ACTIONS.get(e.action, "Changed")} '
                f'{e.content_type.model if e.content_type else "record"} '
                f'{e.object_repr}'.strip(),
        detail=(e.changes_text or '')[:200],
    )


SOURCES = {
    'email_out': {
        'label': 'Outbound Email', 'qs': _email_out_qs, 'row': _email_out_row,
        'time_field': 'created_at',
        'search': ['to_email', 'trigger', 'detail', 'ticket__ticket_number', 'ticket__subject'],
    },
    'email_in': {
        'label': 'Inbound Email', 'qs': _email_in_qs, 'row': _email_in_row,
        'time_field': 'created_at',
        'search': ['from_email', 'subject', 'detail', 'ticket__ticket_number'],
    },
    'org_cred': {
        'label': 'Org Credentials', 'qs': _org_cred_qs, 'row': _org_cred_row,
        'time_field': 'accessed_at',
        'search': ['credential__name', 'user__username', 'user__first_name', 'user__last_name'],
    },
    'device_cred': {
        'label': 'Device Credentials', 'qs': _device_cred_qs, 'row': _device_cred_row,
        'time_field': 'accessed_at',
        'search': ['device__name', 'field', 'user__username', 'user__first_name', 'user__last_name'],
    },
    'audit': {
        'label': 'Record Changes', 'qs': _audit_qs, 'row': _audit_row,
        'time_field': 'timestamp',
        'search': ['object_repr', 'changes_text', 'actor__username',
                   'actor__first_name', 'actor__last_name'],
    },
}


# ── Query ────────────────────────────────────────────────────────────────────

def parse_date(value, *, end_of_day=False):
    """'2026-07-27' → an aware datetime, or None. Bad input is ignored, not an
    error: a mistyped date should not blank the page the user is reading."""
    if not value:
        return None
    try:
        d = datetime.strptime(value.strip(), '%Y-%m-%d').date()
    except (ValueError, AttributeError):
        return None
    stamp = datetime.combine(d, time.max if end_of_day else time.min)
    return timezone.make_aware(stamp) if timezone.is_naive(stamp) else stamp


def search_source(key, q='', dt_from=None, dt_to=None):
    """A real queryset for one source, so the caller can page it in SQL."""
    spec = SOURCES[key]
    qs = spec['qs']()
    tf = spec['time_field']
    if dt_from:
        qs = qs.filter(**{f'{tf}__gte': dt_from})
    if dt_to:
        qs = qs.filter(**{f'{tf}__lte': dt_to})
    if q:
        matches = Q()
        for field in spec['search']:
            matches |= Q(**{f'{field}__icontains': q})
        qs = qs.filter(matches)
    return qs.order_by(f'-{tf}')


def rows_for(key, entries):
    row = SOURCES[key]['row']
    return [row(e) for e in entries]


def unified(q='', dt_from=None, dt_to=None, limit=UNIFIED_LIMIT):
    """Every source interleaved, newest first.

    Merged in Python because the five tables share no orderable column. Each
    source contributes at most PER_SOURCE_FETCH rows, so one noisy log can crowd
    the others out of a wide window — the fix for that is filtering to a source,
    which is exactly what the filter is for.
    """
    merged = []
    for key in SOURCES:
        entries = list(search_source(key, q, dt_from, dt_to)[:PER_SOURCE_FETCH])
        merged.extend(rows_for(key, entries))
    merged.sort(key=lambda r: r.when, reverse=True)
    return merged[:limit]
