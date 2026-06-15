"""
Management command: fetch_inbound_email

Polls the configured mailbox (IMAP or POP3) and processes messages:
  - Subject contains [TKT-YYYYMMDD-NNNN]  →  add TicketReply
  - Otherwise                              →  create new Ticket

Run via cron every 1-5 minutes:
    */2 * * * * /path/to/venv/bin/python /path/to/manage.py fetch_inbound_email
"""

import email
import imaplib
import logging
import poplib
import re
import traceback

logger = logging.getLogger('core')
from email.header import decode_header, make_header
from email.utils import parseaddr

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone

from fnmatch import fnmatch

# Free/consumer email providers where domain ≠ company.
# Senders from these get their own per-person client record instead of being
# grouped under the domain name (e.g. two Gmail users → two clients, not one).
_FREE_EMAIL_DOMAINS = {
    'gmail.com', 'googlemail.com',
    'yahoo.com', 'yahoo.co.uk', 'yahoo.ca', 'yahoo.com.au', 'ymail.com',
    'hotmail.com', 'hotmail.co.uk', 'hotmail.ca', 'hotmail.com.au',
    'outlook.com', 'outlook.co.uk',
    'live.com', 'live.co.uk', 'live.ca',
    'msn.com',
    'icloud.com', 'me.com', 'mac.com',
    'aol.com',
    'protonmail.com', 'proton.me',
    'fastmail.com',
}

from core.models import (
    Attachment, BlockedSender, Client, Contact, InboundEmailLog, SiteSettings,
    Ticket, TicketReply,
)

TICKET_RE = re.compile(r'\[?(TKT-[\d-]+)\]?', re.IGNORECASE)

# Common patterns that mark the start of quoted reply text
QUOTE_PATTERNS = [
    re.compile(r'\n>[ \t]'),                                 # "> quoted"
    re.compile(r'\nOn .{10,100} wrote:\s*\n', re.DOTALL),   # "On Mon, Jan 1 … wrote:"
    re.compile(r'\n-{3,}\s*Original Message\s*-{3,}', re.IGNORECASE),
    re.compile(r'\n-{3,}\s*Forwarded Message\s*-{3,}', re.IGNORECASE),
    re.compile(r'\nFrom:.*\nSent:.*\nTo:', re.IGNORECASE),
]


def _decode_header_str(value):
    """Decode an email header value that may be RFC2047 encoded."""
    if not value:
        return ''
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _get_body(msg):
    """Extract plain-text body from a (possibly multipart) email.Message."""
    body = ''
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get('Content-Disposition', ''))
            if ct == 'text/plain' and 'attachment' not in cd:
                charset = part.get_content_charset() or 'utf-8'
                try:
                    body = part.get_payload(decode=True).decode(charset, errors='replace')
                except Exception:
                    body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                break
    else:
        charset = msg.get_content_charset() or 'utf-8'
        try:
            body = msg.get_payload(decode=True).decode(charset, errors='replace')
        except Exception:
            body = str(msg.get_payload())
    return body.strip()


def _strip_quotes(text):
    """Remove quoted-reply sections from body text."""
    earliest = len(text)
    for pat in QUOTE_PATTERNS:
        m = pat.search(text)
        if m and m.start() < earliest:
            earliest = m.start()
    return text[:earliest].strip()


def _get_attachments(msg):
    """Yield (filename, content_type, data_bytes) for each attachment part."""
    if not msg.is_multipart():
        return
    for part in msg.walk():
        cd = str(part.get('Content-Disposition', ''))
        if 'attachment' in cd:
            filename = part.get_filename()
            if not filename:
                continue
            filename = _decode_header_str(filename)
            ct = part.get_content_type() or 'application/octet-stream'
            data = part.get_payload(decode=True)
            if data:
                yield filename, ct, data


def _save_attachments(obj, msg):
    """Save email attachments to the Attachment model."""
    ct = ContentType.objects.get_for_model(obj)
    for filename, mime_type, data in _get_attachments(msg):
        a = Attachment(
            content_type=ct,
            object_id=obj.pk,
            original_filename=filename,
            mime_type=mime_type,
            size_bytes=len(data),
        )
        a.file.save(filename, ContentFile(data), save=True)


def _resolve_client_contact(from_email, from_name, settings):
    """Return (client, contact) for the sender. Creates records if needed."""
    # Returning sender — already in the system
    contact = Contact.objects.filter(email__iexact=from_email).select_related('client').first()
    if contact:
        return contact.client, contact

    # Parse sender name parts now — needed for both client name and contact creation
    parts = from_name.strip().split() if from_name and from_name.strip() else []
    first = parts[0] if parts else ''
    last = ' '.join(parts[1:]) if len(parts) > 1 else ''

    domain = from_email.split('@')[-1].lower() if '@' in from_email else from_email
    local  = from_email.split('@')[0] if '@' in from_email else from_email

    if settings.inbound_default_client_name:
        # Admin has configured a catch-all client for unknown senders
        client_name = settings.inbound_default_client_name
    elif domain in _FREE_EMAIL_DOMAINS:
        # Consumer address — each sender gets their own client record.
        # Use "First Last" if available, fall back to the local part of the address.
        client_name = from_name.strip() if from_name and from_name.strip() else local
    else:
        # Business domain — group everyone from the same domain under one client
        client_name = domain

    client, _ = Client.objects.get_or_create(
        name=client_name,
        defaults={'email': '', 'is_active': True},
    )

    contact = Contact.objects.create(
        client=client,
        first_name=first or local,
        last_name=last,
        email=from_email,
        is_primary=not client.contacts.exists(),
    )
    return client, contact


def _process_message(raw_msg_bytes, settings, verbosity):
    """Parse and process one raw email message. Returns (status, detail, ticket)."""
    try:
        msg = email.message_from_bytes(raw_msg_bytes)
    except Exception as exc:
        return 'error', f'Failed to parse message: {exc}', None

    message_id = (msg.get('Message-ID') or '').strip()
    subject = _decode_header_str(msg.get('Subject', '(No Subject)'))
    from_raw = _decode_header_str(msg.get('From', ''))
    from_name, from_email = parseaddr(from_raw)
    from_email = from_email.lower().strip()

    if verbosity >= 2:
        print(f'  Processing: "{subject}" from {from_email}')

    # Blocked sender check
    blocked_patterns = list(BlockedSender.objects.values_list('pattern', flat=True))
    if any(fnmatch(from_email, p.lower()) for p in blocked_patterns):
        return 'error', f'Blocked sender: {from_email}', None

    # Duplicate guard on Message-ID
    if message_id and InboundEmailLog.objects.filter(message_id=message_id).exclude(status='error').exists():
        return 'duplicate', f'Already processed message-id {message_id}', None

    body = _get_body(msg)
    if settings.strip_quoted_replies:
        body = _strip_quotes(body)
    if not body:
        body = '(No message body)'

    # --- Threading: check subject for existing ticket number ---
    ticket_match = TICKET_RE.search(subject)
    if ticket_match:
        ticket_number = ticket_match.group(1).upper()
        ticket = Ticket.objects.filter(ticket_number=ticket_number).first()
        if ticket:
            # A client reply always threads into the matched ticket — including
            # 'converted' and 'closed'. The ticket is the single client-facing
            # channel; a reply must never spawn an orphan ticket just because the
            # ticket moved on. We do NOT un-convert a converted ticket (it's a WO
            # now) — we only flag it for response so the tech sees the reply.
            reply = TicketReply.objects.create(
                ticket=ticket,
                reply_type='customer_visible',
                content=body,
                created_by=None,
            )
            _save_attachments(reply, msg)
            update_fields = ['needs_response', 'updated_at']
            ticket.needs_response = True
            # Reopen tickets that had been considered done; leave 'converted'
            # alone (the active record is the work order, not the ticket status).
            if ticket.status in ('resolved', 'waiting_on_customer', 'closed'):
                ticket.status = 'open'
                update_fields.append('status')
            ticket.save(update_fields=update_fields)
            return 'reply', f'Added reply to {ticket_number}', ticket

    # --- New ticket ---
    client, contact = _resolve_client_contact(from_email, from_name, settings)

    if client.suppress_emails:
        return 'error', f'Client {client.name} has suppress_emails — skipping', None

    ticket = Ticket(
        ticket_number=Ticket.generate_ticket_number(),
        client=client,
        contact=contact,
        subject=subject[:255],
        description=body,
        source='email',
        status='new',
        created_by=None,
    )
    ticket.save()
    _save_attachments(ticket, msg)
    return 'new_ticket', f'Created {ticket.ticket_number} for {client.name}', ticket


class Command(BaseCommand):
    help = 'Fetch and process inbound email from IMAP or POP3 mailbox.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Parse and report messages without creating tickets or marking as read.',
        )

    def handle(self, *args, **options):
        site = SiteSettings.get()
        dry_run = options['dry_run']
        verbosity = options['verbosity']

        if not site.inbound_email_enabled:
            if verbosity >= 1:
                self.stdout.write('Inbound email is disabled in SiteSettings.')
            return

        if not site.inbound_host or not site.inbound_username:
            self.stderr.write(self.style.ERROR('Inbound email host/username not configured.'))
            return

        if verbosity >= 1:
            self.stdout.write(
                f'[{timezone.now():%Y-%m-%d %H:%M}] Fetching from {site.inbound_protocol.upper()} '
                f'{site.inbound_host} as {site.inbound_username}'
                + (' [DRY RUN]' if dry_run else '')
            )

        try:
            if site.inbound_protocol == 'pop3':
                messages = self._fetch_pop3(site, dry_run, verbosity)
            else:
                messages = self._fetch_imap(site, dry_run, verbosity)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'Connection error: {exc}'))
            return

        if not messages:
            if verbosity >= 1:
                self.stdout.write('  No new messages.')
            return

        counts = {'new_ticket': 0, 'reply': 0, 'duplicate': 0, 'error': 0}
        for raw_bytes in messages:
            if dry_run:
                msg = email.message_from_bytes(raw_bytes)
                subj = _decode_header_str(msg.get('Subject', ''))
                frm = _decode_header_str(msg.get('From', ''))
                self.stdout.write(f'  [DRY RUN] From: {frm} | Subject: {subj}')
                continue

            status, detail, ticket = _process_message(raw_bytes, site, verbosity)
            counts[status] = counts.get(status, 0) + 1

            try:
                msg_obj = email.message_from_bytes(raw_bytes)
                InboundEmailLog.objects.create(
                    message_id=(msg_obj.get('Message-ID') or '')[:500],
                    from_email=parseaddr(_decode_header_str(msg_obj.get('From', '')))[1][:255],
                    subject=_decode_header_str(msg_obj.get('Subject', ''))[:500],
                    ticket=ticket,
                    status=status,
                    detail=detail,
                )
            except Exception:
                # An InboundEmailLog write failure must not interrupt processing,
                # but it should be visible rather than swallowed.
                logger.exception('Failed to write InboundEmailLog row for a fetched message.')

            style = self.style.SUCCESS if status in ('new_ticket', 'reply') else self.style.WARNING
            if verbosity >= 1:
                self.stdout.write(style(f'  [{status}] {detail}'))

        if not dry_run and verbosity >= 1:
            self.stdout.write(
                f'Done — {counts["new_ticket"]} new tickets, {counts["reply"]} replies, '
                f'{counts["duplicate"]} duplicates, {counts["error"]} errors.'
            )

    # ── IMAP ──────────────────────────────────────────────────────────────────

    def _fetch_imap(self, site, dry_run, verbosity):
        if site.inbound_ssl:
            conn = imaplib.IMAP4_SSL(site.inbound_host, site.inbound_port)
        else:
            conn = imaplib.IMAP4(site.inbound_host, site.inbound_port)

        conn.login(site.inbound_username, site.inbound_password)
        conn.select(site.inbound_folder or 'INBOX')

        _, data = conn.search(None, 'UNSEEN')
        msg_ids = data[0].split() if data[0] else []

        if verbosity >= 2:
            self.stdout.write(f'  {len(msg_ids)} unseen message(s) in {site.inbound_folder}.')

        messages = []
        for msg_id in msg_ids:
            _, msg_data = conn.fetch(msg_id, '(RFC822)')
            raw = msg_data[0][1]
            messages.append(raw)
            if not dry_run:
                if site.inbound_delete_after_fetch:
                    conn.store(msg_id, '+FLAGS', '\\Deleted')
                else:
                    conn.store(msg_id, '+FLAGS', '\\Seen')

        if not dry_run and site.inbound_delete_after_fetch:
            conn.expunge()

        conn.logout()
        return messages

    # ── POP3 ──────────────────────────────────────────────────────────────────

    def _fetch_pop3(self, site, dry_run, verbosity):
        if site.inbound_ssl:
            conn = poplib.POP3_SSL(site.inbound_host, site.inbound_port)
        else:
            conn = poplib.POP3(site.inbound_host, site.inbound_port)

        conn.user(site.inbound_username)
        conn.pass_(site.inbound_password)

        count, _ = conn.stat()
        if verbosity >= 2:
            self.stdout.write(f'  {count} message(s) in mailbox.')

        messages = []
        for i in range(1, count + 1):
            _, lines, _ = conn.retr(i)
            raw = b'\r\n'.join(lines)
            messages.append(raw)
            if not dry_run:
                conn.dele(i)  # POP3 always deletes after retrieval

        conn.quit()
        return messages
