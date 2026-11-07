"""
Management command: fetch_inbound_email

Polls the configured mailbox (IMAP or POP3) and processes messages:
  - Subject contains [TKT-YYYYMMDD-NNNN]  →  add TicketReply
  - Otherwise                              →  create new Ticket

Run on a timer every 1-5 minutes (deploy/README.md has the systemd unit).

No customer email may vanish. The rules that guarantee it:
  1. A message leaves the mailbox (deleted, or marked read) only once MB holds
     it: the ticket/reply is committed, the message was deliberately refused, or
     MB has saved its own copy of the raw message.
  2. Each attempt is all-or-nothing (one transaction), so a failure halfway
     leaves no half-made ticket behind.
  3. A failed message is retried from MB's own copy, never from the mailbox:
     some servers (Gmail POP) never offer a downloaded message again.
  4. After MAX_ATTEMPTS failures MB gives up: a System Alert ticket carries the
     original message as an .eml attachment and rings the admin bell.
"""

import contextvars
import email
import fcntl
import hashlib
import imaplib
import logging
import os
import poplib
import re

logger = logging.getLogger('core')
from email.header import decode_header, make_header
from email.utils import parseaddr
from html import unescape
from html.parser import HTMLParser

from django.contrib.contenttypes.models import ContentType
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db import IntegrityError, transaction
from django.db.models import F
from django.conf import settings as django_settings

from fnmatch import fnmatch

# Free/consumer email providers where domain ≠ company.
# T2 / Helpdesk Buttons posts every button ticket from a fixed no-reply relay
# address; the real end user is carried in a forwarded-message header inside the
# body. We unwrap that so the ticket attributes to the actual contact instead of
# the relay. Add other T2 relay addresses here if they ever change.
_T2_RELAY_ADDRESSES = {'email-connector@tier2tickets.com'}

# First "From:" line inside a forwarded body (T2 wraps the original sender).
_FORWARDED_FROM_RE = re.compile(r'^\s*From:\s*(.+)$', re.IGNORECASE | re.MULTILINE)

from core.models import (
    Attachment, BlockedSender, Client, Contact, InboundEmailLog, SiteSettings,
    Ticket, TicketLink, TicketReply,
)

TICKET_RE = re.compile(r'\[?(TKT-[\d-]+)\]?', re.IGNORECASE)

# How many times one message is tried before MB gives up and raises an alert.
MAX_ATTEMPTS = 3

# Statuses after which a message is finished with: it is never tried again, and
# if it shows up in the mailbox again it is only removed. 'error' is the
# pre-Nov-2026 status for every failure; those rows predate retries and stay final.
FINAL_STATUSES = frozenset({
    'new_ticket', 'reply', 'reply_flagged', 'new_ticket_linked',
    'refused', 'gave_up', 'error', 'duplicate',
})
SUCCESS_STATUSES = frozenset({'new_ticket', 'reply', 'reply_flagged', 'new_ticket_linked'})

# Files written to storage during the current attempt. A rolled-back attempt
# leaves its DB rows undone but not its files; the caller deletes these.
_attempt_files = contextvars.ContextVar('inbound_attempt_files', default=None)

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


# Tags that start a new line (one per logical block / table row).
_HTML_LINE_TAGS = {
    'p', 'div', 'br', 'tr', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'blockquote', 'header', 'footer', 'section', 'article',
}
# Tags that should leave a blank line around their content (major sections).
_HTML_SECTION_TAGS = {'table', 'ul', 'ol'}
# Tags whose contents are not human-readable body text.
_HTML_SKIP_TAGS = {'style', 'script', 'head', 'title', 'meta', 'link'}


class _HTMLToText(HTMLParser):
    """Convert an HTML email body to readable plain text.

    Deliberately lossy: we render inbound mail as text (never as live HTML), so
    a malicious or malformed alert can't inject markup into the ticket view.

    The key behaviour for RMM-style alerts: a table *row* becomes one line and
    its *cells* are joined with a separator, so "Product version:" and "2.5.0.67"
    stay together as "Product version: 2.5.0.67" instead of splitting across
    lines. Whitespace inside a cell is collapsed (HTML treats newlines as spaces
    anyway). <style>/<script> content is dropped.
    """

    CELL_SEP = '  '  # between cells of the same row

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._lines = []        # finished output lines
        self._cur = []          # data fragments for the current line
        self._skip_depth = 0

    def _flush_line(self):
        text = re.sub(r'[ \t]+', ' ', ''.join(self._cur)).strip()
        if text:
            self._lines.append(text)
        self._cur = []

    def _blank_line(self):
        self._flush_line()
        if self._lines and self._lines[-1] != '':
            self._lines.append('')

    def handle_starttag(self, tag, attrs):
        if tag in _HTML_SKIP_TAGS:
            self._skip_depth += 1
        elif tag in ('td', 'th'):
            if self._cur:
                self._cur.append(self.CELL_SEP)
        elif tag in _HTML_SECTION_TAGS:
            self._blank_line()
        elif tag in _HTML_LINE_TAGS:
            self._flush_line()

    def handle_endtag(self, tag):
        if tag in _HTML_SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _HTML_SECTION_TAGS:
            self._blank_line()
        elif tag in _HTML_LINE_TAGS:
            self._flush_line()

    def handle_data(self, data):
        if self._skip_depth:
            return
        # Collapse intra-element whitespace (incl. newlines) so cell content
        # stays on one line.
        self._cur.append(re.sub(r'\s+', ' ', data))

    def get_lines(self):
        self._flush_line()
        return self._lines


def _html_to_text(html):
    """Render an HTML fragment to collapsed, readable plain text."""
    parser = _HTMLToText()
    try:
        parser.feed(html)
        parser.close()
        lines = parser.get_lines()
    except Exception:
        # Fall back to a crude tag strip rather than dumping raw markup.
        logger.exception('HTML-to-text parse failed; falling back to tag strip.')
        # [^<>], not [^>]: a run of '<' with no '>' made [^>]+ rescan to the
        # end from every '<' (CodeQL polynomial-redos, Oct 3 2026). Inbound
        # mail is anyone on the internet.
        stripped = unescape(re.sub(r'<[^<>]+>', ' ', html))
        lines = [re.sub(r'[ \t]+', ' ', ln).strip() for ln in stripped.splitlines()]
    # Drop runs of blank lines down to a single separator.
    out = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return '\n'.join(out).strip()


def _decode_part(part):
    """Decode a single email part's payload to a string."""
    charset = part.get_content_charset() or 'utf-8'
    try:
        return part.get_payload(decode=True).decode(charset, errors='replace')
    except Exception:
        try:
            return part.get_payload(decode=True).decode('utf-8', errors='replace')
        except Exception:
            return str(part.get_payload())


def _get_body(msg):
    """Extract a readable text body from a (possibly multipart) email.Message.

    Prefers a real text/plain part. If the message is HTML-only (e.g. many RMM
    alerts), the HTML is converted to plain text rather than stored as raw markup.
    """
    plain = ''
    html = ''
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get('Content-Disposition', ''))
            if 'attachment' in cd:
                continue
            if ct == 'text/plain' and not plain:
                plain = _decode_part(part)
            elif ct == 'text/html' and not html:
                html = _decode_part(part)
    else:
        ct = msg.get_content_type()
        decoded = _decode_part(msg)
        if ct == 'text/html':
            html = decoded
        else:
            plain = decoded

    if plain.strip():
        return plain.strip()
    if html.strip():
        return _html_to_text(html)
    return ''


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


def _save_attachments(obj, msg, settings):
    """Save email attachments to the Attachment model.

    Inbound is the UNtrusted path (anyone who can email support), so it enforces
    the same guards as manual upload: the blocked-extension list and the size cap.
    Skips are logged (fail loud) rather than silently dropped."""
    ct = ContentType.objects.get_for_model(obj)
    blocked = settings.get_blocked_extensions()
    max_bytes = settings.max_attachment_size_mb * 1024 * 1024
    for filename, mime_type, data in _get_attachments(msg):
        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
        if ext in blocked:
            logger.warning('Inbound attachment %r blocked by extension (.%s); skipped.', filename, ext)
            continue
        if len(data) > max_bytes:
            logger.warning('Inbound attachment %r is %d bytes, over the %d MB cap; skipped.',
                           filename, len(data), settings.max_attachment_size_mb)
            continue
        a = Attachment(
            content_type=ct,
            object_id=obj.pk,
            original_filename=filename,
            mime_type=mime_type,
            size_bytes=len(data),
        )
        a.file.save(filename, ContentFile(data), save=True)
        written = _attempt_files.get()
        if written is not None:
            written.append((a.file.storage, a.file.name))


def _resolve_client_contact(from_email, from_name, settings):
    """Return (client, contact) for the sender. Creates records if needed.

    A returning sender (matched by contact email) routes to their existing
    client. An unmatched sender is parked under the system "Unsorted / Unverified"
    bucket for triage — we never auto-create a junk named client from a cold
    email. An admin-configured ``inbound_default_client_name`` catch-all still
    wins over the bucket if set."""
    # Returning sender — already in the system
    contact = Contact.objects.filter(email__iexact=from_email).select_related('client').first()
    if contact:
        return contact.client, contact

    # Parse sender name parts for the new contact record
    parts = from_name.strip().split() if from_name and from_name.strip() else []
    first = parts[0] if parts else ''
    last = ' '.join(parts[1:]) if len(parts) > 1 else ''
    local = from_email.split('@')[0] if '@' in from_email else from_email

    if settings.inbound_default_client_name:
        # Admin-configured catch-all wins over the Unsorted bucket
        client, _ = Client.objects.get_or_create(
            name=settings.inbound_default_client_name,
            defaults={'email': '', 'is_active': True},
        )
    else:
        # Unmatched sender → hold under the system triage bucket for onboarding
        client = Client.get_unsorted()

    contact = Contact.objects.create(
        client=client,
        first_name=first or local,
        last_name=last,
        email=from_email,
        is_primary=not client.contacts.exists(),
    )
    return client, contact


def _extract_forwarded_sender(body):
    """T2/Helpdesk Buttons wraps the real sender in a forwarded-message header
    inside the body. Return (name, email) parsed from the first 'From:' line, or
    (None, None) if there isn't a usable one."""
    if not body:
        return None, None
    match = _FORWARDED_FROM_RE.search(body)
    if not match:
        return None, None
    name, addr = parseaddr(match.group(1).strip())
    if not addr or '@' not in addr:
        return None, None
    return (name or ''), addr


# T2 / Helpdesk Buttons lets the requester tick urgency boxes before sending;
# they arrive as plain lines under a "[selections]" heading in the body. Map the
# ones that say how badly it hurts onto a ticket priority. Verified against real
# T2 mail on prod (Aug 18 2026): the block holds only the requester's OWN picks.
# Matched by lowercase substring so T2 rewording punctuation does not break it;
# the highest matched level wins. Lines with no urgency meaning ("recurring
# issue", "call me before remoting in") map to nothing.
_T2_SELECTION_PRIORITY = (
    ('emergency', 'urgent'),
    ('unable to use my system', 'urgent'),
    ('work around it', 'normal'),
    ('just a question', 'low'),
)
# Picks that say nothing about urgency. Listed so a line that matches NEITHER
# table can be reported: if T2 rewords an urgent choice, the ticket would
# silently land at Normal, and the only trace would be this warning.
_T2_SELECTION_NEUTRAL = (
    'recurring issue',
    'call me before remoting',
    'remotely access my computer',
    'issue is with another device',
)
_PRIORITY_RANK = {'low': 0, 'normal': 1, 'high': 2, 'urgent': 3}


def _t2_priority_from_selections(body):
    """Return the ticket priority implied by a T2 body's [selections] block, or
    None when the block is absent or carries no urgency line. Logs a warning
    when the block holds a line neither table recognises (fail loud on drift)."""
    if not body:
        return None
    marker = body.find('[selections]')
    if marker == -1:
        return None
    best = None
    seen_any = False
    unknown = []
    for line in body[marker + len('[selections]'):].splitlines():
        text = line.strip().lower()
        if not text:
            if seen_any:
                break  # the block is the run of non-blank lines under the heading
            continue
        if text.startswith('['):
            break  # next T2 section
        seen_any = True
        matched = False
        for needle, level in _T2_SELECTION_PRIORITY:
            if needle in text:
                matched = True
                if best is None or _PRIORITY_RANK[level] > _PRIORITY_RANK[best]:
                    best = level
        if not matched and not any(n in text for n in _T2_SELECTION_NEUTRAL):
            unknown.append(line.strip())
    if unknown:
        logger.warning(
            'T2 [selections] contained %d line(s) the priority mapping does not '
            'recognise; ticket priority may be under-ranked until the mapping is '
            'updated: %r', len(unknown), unknown[:5])
    return best


def _create_new_ticket(subject, body, from_email, from_name, settings, msg, priority=None):
    """Resolve client/contact and create a fresh ticket from an inbound email.
    Shared by the no-subject-match path and the past-reopen-window path
    (Slice 4) — same ticket-creation logic either way. `priority` is what the
    SOURCE declared (T2 selections today); None means the source said nothing
    and the model default (normal) applies."""
    client, contact = _resolve_client_contact(from_email, from_name, settings)

    if client.suppress_emails:
        return 'refused', f'Client {client.name} has suppress_emails — skipping', None

    ticket = Ticket(
        ticket_number=Ticket.generate_ticket_number(),
        client=client,
        contact=contact,
        subject=subject[:255],
        description=body,
        source='email',
        priority=priority or 'normal',
        status='new',
        created_by=None,
    )
    ticket.save()
    _save_attachments(ticket, msg, settings)
    return 'new_ticket', f'Created {ticket.ticket_number} for {client.name}', ticket


def _process_message(raw_msg_bytes, settings, verbosity):
    """Parse and process one raw email message. Returns (status, detail, ticket).

    'refused' means deliberately not ticketed (blocked sender, suppressed
    client): final, never retried. Anything that goes wrong RAISES; the caller
    turns that into a retry. Callers run this inside a transaction."""
    try:
        msg = email.message_from_bytes(raw_msg_bytes)
    except Exception as exc:
        raise ValueError(f'Failed to parse message: {exc}') from exc

    message_id = (msg.get('Message-ID') or '').strip()
    subject = _decode_header_str(msg.get('Subject', '(No Subject)'))
    from_raw = _decode_header_str(msg.get('From', ''))
    from_name, from_email = parseaddr(from_raw)
    from_email = from_email.lower().strip()

    if verbosity >= 2:
        print(f'  Processing: "{subject}" from {from_email}')

    body = _get_body(msg)

    # T2 / Helpdesk Buttons relay: the envelope sender is a no-reply relay, so
    # unwrap the real end user from the forwarded body (before any quote
    # stripping) and attribute to them — that's what makes replies route to the
    # actual contact instead of the relay. If we can't parse it, fall back to the
    # relay address but log loudly so the bad attribution is visible.
    inbound_priority = None
    if from_email in _T2_RELAY_ADDRESSES:
        # Read the requester's urgency picks from the RAW body, before any quote
        # stripping, for the same reason the sender is unwrapped here.
        inbound_priority = _t2_priority_from_selections(body)
        real_name, real_email = _extract_forwarded_sender(body)
        if real_email:
            from_name, from_email = real_name, real_email.lower().strip()
        else:
            logger.warning(
                'T2 relay email had no parseable forwarded sender; attributing '
                'to the relay address. Message-ID=%s subject=%r',
                message_id or '(none)', subject)

    if settings.strip_quoted_replies:
        body = _strip_quotes(body)
    if not body:
        body = '(No message body)'

    # Blocked sender check (runs on the real sender once a T2 relay is unwrapped)
    blocked_patterns = list(BlockedSender.objects.values_list('pattern', flat=True))
    if any(fnmatch(from_email, p.lower()) for p in blocked_patterns):
        return 'refused', f'Blocked sender: {from_email}', None

    # NOTE: duplicate detection is handled atomically by the caller
    # (Command._intake) via a unique "claim" row on the Message-ID, backed by a DB
    # constraint. We deliberately do NOT check-then-act here — that non-atomic
    # race is exactly what let one email become two tickets when two fetch runs
    # overlapped.

    # --- Threading: check subject for existing ticket number ---
    ticket_match = TICKET_RE.search(subject)
    if ticket_match:
        ticket_number = ticket_match.group(1).upper()
        ticket = Ticket.objects.filter(ticket_number=ticket_number).first()
        if ticket:
            if ticket.status in Ticket.CLOSED_AT_STATUSES:
                # Closed/resolved: MB used to auto-reopen on ANY reply, which
                # turned a client's "thanks!" or a re-engagement after Mike
                # closed a stale unanswered ticket into busywork (re-close) and
                # polluted any reopen-rate metric. Now: thread in + flag, but
                # stay closed — a human explicitly Reopens or Dismisses.
                window_days = SiteSettings.get().ticket_reopen_window_days
                # Same rule as auto-close (Ticket.past_reopen_window): a ticket
                # the timer would close is one a reply must not thread into.
                if ticket.within_reopen_window(window_days=window_days):
                    reply = TicketReply.objects.create(
                        ticket=ticket, reply_type='customer_visible',
                        content=body, created_by=None,
                    )
                    _save_attachments(reply, msg, settings)
                    ticket.needs_response = True
                    ticket.save(update_fields=['needs_response', 'updated_at'])
                    return (
                        'reply_flagged',
                        f'Flagged closed ticket {ticket_number} for review (reply threaded, not reopened)',
                        ticket,
                    )
                # Past the reopen window — the original context is stale
                # enough that reviving it isn't useful; start a fresh ticket
                # but link it to the old one so the history isn't lost.
                status, detail, new_ticket = _create_new_ticket(subject, body, from_email, from_name, settings, msg, priority=inbound_priority)
                if new_ticket:
                    TicketLink.objects.create(ticket_a=ticket, ticket_b=new_ticket, link_type='related')
                    return (
                        'new_ticket_linked',
                        f'{ticket_number} closed past the {window_days}-day reopen window — '
                        f'created {new_ticket.ticket_number}, linked',
                        new_ticket,
                    )
                return status, detail, new_ticket

            # 'converted' and every other still-active status: thread in.
            # 'converted' never reopens (the work order is the active record,
            # not the ticket status) — waiting_on_customer does, since the
            # client responding is exactly what that status was waiting for.
            reply = TicketReply.objects.create(
                ticket=ticket,
                reply_type='customer_visible',
                content=body,
                created_by=None,
            )
            _save_attachments(reply, msg, settings)
            update_fields = ['needs_response', 'updated_at']
            ticket.needs_response = True
            if ticket.status == 'waiting_on_customer':
                ticket.status = 'open'
                update_fields.append('status')
            ticket.save(update_fields=update_fields)
            return 'reply', f'Added reply to {ticket_number}', ticket

    # --- New ticket ---
    return _create_new_ticket(subject, body, from_email, from_name, settings, msg, priority=inbound_priority)


def _identify(raw_bytes):
    """Return (claim_key, from_email, subject) for the log row.

    The claim key is the Message-ID. A message without one is keyed by a hash of
    its raw bytes instead, so it can still be deduplicated and its retries
    counted (identical bytes are the same delivery)."""
    mid = frm = subj = ''
    try:
        msg_obj = email.message_from_bytes(raw_bytes)
        mid = (msg_obj.get('Message-ID') or '').strip()[:500]
        frm = parseaddr(_decode_header_str(msg_obj.get('From', '')))[1][:255]
        subj = _decode_header_str(msg_obj.get('Subject', ''))[:500]
    except Exception:
        logger.exception('Could not read the headers of an inbound message.')
    if not mid:
        mid = 'no-message-id:sha256:' + hashlib.sha256(raw_bytes).hexdigest()
    return mid, frm, subj


# ── Mailboxes ─────────────────────────────────────────────────────────────────
# Both expose the same three steps, so the intake loop never needs to know which
# protocol it is talking to: messages() yields (ref, raw_bytes) without changing
# anything on the server, done(ref) takes one message out of the queue, and
# close() ends the session.

class _ImapMailbox:
    def __init__(self, site):
        if site.inbound_ssl:
            self.conn = imaplib.IMAP4_SSL(site.inbound_host, site.inbound_port)
        else:
            self.conn = imaplib.IMAP4(site.inbound_host, site.inbound_port)
        self.conn.login(site.inbound_username, site.inbound_password)
        self.folder = site.inbound_folder or 'INBOX'
        typ, _ = self.conn.select(self.folder)
        if typ != 'OK':
            raise imaplib.IMAP4.error(f'Could not open IMAP folder {self.folder!r}')
        self.delete = site.inbound_delete_after_fetch
        self._deleted_any = False

    def messages(self):
        # UIDs, not sequence numbers: we now hold the session open while tickets
        # are made, and a person deleting mail in webmail meanwhile would shift
        # sequence numbers under us.
        typ, data = self.conn.uid('SEARCH', None, 'UNSEEN')
        uids = data[0].split() if typ == 'OK' and data and data[0] else []
        for uid in uids:
            # BODY.PEEK[]: a plain RFC822 / BODY[] fetch marks the message read
            # on the server, which would take it out of the queue before MB has it.
            typ, msg_data = self.conn.uid('FETCH', uid, '(BODY.PEEK[])')
            raw = next((part[1] for part in (msg_data or []) if isinstance(part, tuple)), None)
            if typ != 'OK' or raw is None:
                logger.warning('IMAP FETCH of UID %s returned nothing usable; left in the mailbox.', uid)
                continue
            yield uid, raw

    def done(self, uid):
        flag = '(\\Deleted)' if self.delete else '(\\Seen)'
        typ, _ = self.conn.uid('STORE', uid, '+FLAGS', flag)
        if typ != 'OK':
            raise imaplib.IMAP4.error(f'STORE {flag} on UID {uid} failed')
        if self.delete:
            self._deleted_any = True

    def close(self):
        try:
            if self._deleted_any:
                self.conn.expunge()
        finally:
            self.conn.logout()


class _Pop3Mailbox:
    """POP3 marks deletions during the session and applies them at QUIT, so a
    session that dies early deletes nothing; those messages are fetched again
    and recognised by their log row."""

    def __init__(self, site):
        if site.inbound_ssl:
            self.conn = poplib.POP3_SSL(site.inbound_host, site.inbound_port)
        else:
            self.conn = poplib.POP3(site.inbound_host, site.inbound_port)
        self.conn.user(site.inbound_username)
        self.conn.pass_(site.inbound_password)

    def messages(self):
        count, _ = self.conn.stat()
        for i in range(1, count + 1):
            _, lines, _ = self.conn.retr(i)
            yield i, b'\r\n'.join(lines)

    def done(self, i):
        self.conn.dele(i)

    def close(self):
        self.conn.quit()


def _delete_files(files):
    for storage, name in files:
        try:
            storage.delete(name)
        except Exception:
            logger.exception('Could not delete stored file %s.', name)


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
        self.verbosity = verbosity

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

        # Single-runner lock: never let two fetches run concurrently. Overlapping
        # runs racing on the same message is what created duplicate tickets. The
        # retry logic below also relies on it: a 'processing' row seen at the
        # start of a run can only be one a previous run died in the middle of.
        lock_path = str(getattr(
            django_settings, 'INBOUND_FETCH_LOCK_PATH',
            os.path.join(str(django_settings.BASE_DIR), 'inbound_fetch.lock'),
        ))
        lock_file = open(lock_path, 'w')
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            if verbosity >= 1:
                self.stdout.write('Another fetch is already running; skipping this run.')
            return

        try:
            self.counts = {
                'new_ticket': 0, 'reply': 0, 'reply_flagged': 0, 'new_ticket_linked': 0,
                'duplicate': 0, 'refused': 0, 'failed': 0, 'gave_up': 0,
            }
            self.touched = set()   # log rows already handled in this run
            self.site = site

            try:
                mailbox = self._open_mailbox(site)
            except Exception as exc:
                logger.exception('Inbound mailbox connection failed.')
                self.stderr.write(self.style.ERROR(f'Connection error: {exc}'))
                mailbox = None

            if mailbox is not None:
                try:
                    self._drain(mailbox, dry_run)
                except Exception as exc:
                    logger.exception('Inbound mailbox error part-way through a fetch.')
                    self.stderr.write(self.style.ERROR(f'Connection error: {exc}'))
                finally:
                    try:
                        mailbox.close()
                    except Exception:
                        # POP3 applies deletions at QUIT; IMAP expunges here. If this
                        # fails, those messages are fetched again next run and
                        # recognised from their log rows, so nothing is lost.
                        logger.exception('Closing the inbound mailbox failed; removals '
                                         'will be redone on the next run.')

            # Retries come from MB's own saved copies, so they run even when the
            # mailbox could not be reached.
            if not dry_run:
                self._retry_saved()

            if not dry_run and verbosity >= 1:
                c = self.counts
                self.stdout.write(
                    f'Done — {c["new_ticket"]} new tickets, {c["reply"]} replies, '
                    f'{c["reply_flagged"]} flagged on closed tickets, '
                    f'{c["new_ticket_linked"]} new tickets linked past reopen window, '
                    f'{c["duplicate"]} duplicates, {c["refused"]} refused, '
                    f'{c["failed"]} failed (will retry), {c["gave_up"]} gave up (alert raised).'
                )
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
            lock_file.close()

    def _open_mailbox(self, site):
        if site.inbound_protocol == 'pop3':
            return _Pop3Mailbox(site)
        return _ImapMailbox(site)

    def _say(self, status, detail):
        if self.verbosity >= 1:
            style = self.style.SUCCESS if status in SUCCESS_STATUSES else self.style.WARNING
            self.stdout.write(style(f'  [{status}] {detail}'))

    # ── Pass 1: the mailbox ───────────────────────────────────────────────────

    def _drain(self, mailbox, dry_run):
        seen = 0
        for ref, raw in mailbox.messages():
            seen += 1
            if dry_run:
                msg = email.message_from_bytes(raw)
                subj = _decode_header_str(msg.get('Subject', ''))
                frm = _decode_header_str(msg.get('From', ''))
                self.stdout.write(f'  [DRY RUN] From: {frm} | Subject: {subj}')
                continue
            if self._intake(raw):
                try:
                    mailbox.done(ref)
                except Exception:
                    logger.exception('MB holds an inbound message but could not take it out '
                                     'of the mailbox; it will be fetched again and skipped.')
        if seen == 0 and self.verbosity >= 1:
            self.stdout.write('  No new messages.')

    def _intake(self, raw):
        """Handle one message fresh from the mailbox. Returns True once MB holds
        it (finished, refused, or saved for retry), i.e. it may leave the mailbox."""
        key, frm, subj = _identify(raw)
        try:
            # Atomically CLAIM this message before doing any work. The unique
            # constraint means a re-fetch of the same email loses the insert here
            # instead of making a second ticket. The savepoint keeps the
            # IntegrityError from poisoning any surrounding transaction.
            with transaction.atomic():
                log = InboundEmailLog.objects.create(
                    message_id=key, from_email=frm, subject=subj,
                    status='processing', attempts=1,
                )
        except IntegrityError:
            log = InboundEmailLog.objects.filter(message_id=key).first()
            if log is None:
                return False
            if log.status in FINAL_STATUSES or log.status == 'failed' or log.raw_copy:
                # Finished, or MB already holds its own copy and retries from that.
                # It is back in the mailbox only because the last removal did not
                # take effect, so remove it now.
                self.counts['duplicate'] += 1
                if self.verbosity >= 1:
                    self.stdout.write(self.style.WARNING(f'  [duplicate] already processed {key}'))
                return True
            # 'processing' with no saved copy: a run died mid-attempt (the run lock
            # rules out a live one), or saving the copy failed. Take it again,
            # but not twice in one run (the same message listed twice).
            if log.pk in self.touched:
                return False
            self.touched.add(log.pk)
            if log.attempts >= MAX_ATTEMPTS:
                return self._give_up(log, raw)
            taken = InboundEmailLog.objects.filter(
                pk=log.pk, status='processing', attempts=log.attempts,
            ).update(attempts=F('attempts') + 1)
            if not taken:
                return False
            log.refresh_from_db()
        self.touched.add(log.pk)
        return self._attempt(log, raw)

    # ── Pass 2: retries from saved copies ─────────────────────────────────────

    def _retry_saved(self):
        pending = (InboundEmailLog.objects
                   .filter(status__in=('failed', 'processing'))
                   .exclude(raw_copy='').exclude(raw_copy__isnull=True)
                   .exclude(pk__in=self.touched)
                   .order_by('created_at'))
        for log in pending:
            self.touched.add(log.pk)
            raw = None
            try:
                with log.raw_copy.open('rb') as fh:
                    raw = fh.read()
            except Exception:
                logger.exception('Saved copy of inbound message %s could not be read.', log.message_id)
            if raw is None or log.attempts >= MAX_ATTEMPTS:
                self._give_up(log, raw)
                continue
            taken = InboundEmailLog.objects.filter(
                pk=log.pk, status=log.status, attempts=log.attempts,
            ).update(status='processing', attempts=F('attempts') + 1)
            if not taken:
                continue
            log.refresh_from_db()
            self._attempt(log, raw)

    # ── One attempt, and what happens after it ────────────────────────────────

    def _attempt(self, log, raw):
        """Try to ticket `raw` once. The ticket/reply and the log row's new status
        commit together or not at all. Returns True when MB holds the message."""
        written = []
        token = _attempt_files.set(written)
        old_copy = (log.raw_copy.storage, log.raw_copy.name) if log.raw_copy else None
        try:
            with transaction.atomic():
                status, detail, ticket = _process_message(raw, self.site, self.verbosity)
                log.status, log.detail, log.ticket = status, detail, ticket
                log.raw_copy = ''
                log.save(update_fields=['status', 'detail', 'ticket', 'raw_copy'])
        except Exception as exc:
            logger.exception('Inbound message %s failed on attempt %d.', log.message_id, log.attempts)
            _delete_files(written)   # rolled back: its attachment files must go too
            log.refresh_from_db()
            return self._failed(log, raw, f'{type(exc).__name__}: {exc}')
        finally:
            _attempt_files.reset(token)

        if old_copy:
            _delete_files([old_copy])   # ticketed: MB's spare copy is no longer needed
        self.counts[status] = self.counts.get(status, 0) + 1
        self._say(status, detail)

        # Tell the shop. The notify helpers never raise, so a mail problem
        # cannot break intake; the ticket/reply above is already saved.
        if ticket is not None:
            from core.email_utils import notify_new_ticket, notify_ticket_reply
            if status in ('new_ticket', 'new_ticket_linked'):
                notify_new_ticket(ticket)
            elif status in ('reply', 'reply_flagged'):
                notify_ticket_reply(ticket)
        return True

    def _failed(self, log, raw, error):
        """Record a failed attempt and make sure MB holds its own copy."""
        saved = None
        try:
            if not log.raw_copy:
                log.raw_copy.save(f'{log.pk}.eml', ContentFile(raw), save=False)
                saved = (log.raw_copy.storage, log.raw_copy.name)
            log.status = 'failed'
            log.detail = f'Attempt {log.attempts} of {MAX_ATTEMPTS} failed: {error}'[:5000]
            log.save(update_fields=['status', 'detail', 'raw_copy'])
        except Exception:
            # No copy: leave it in the mailbox. The row stays 'processing', so the
            # next run takes it again as the next attempt.
            logger.exception('Could not save a copy of failed inbound message %s; '
                             'leaving it in the mailbox.', log.message_id)
            if saved:
                _delete_files([saved])
            return False
        self.counts['failed'] += 1
        self._say('failed', log.detail)
        if log.attempts >= MAX_ATTEMPTS:
            self._give_up(log, raw)
        return True

    def _give_up(self, log, raw):
        """Out of attempts: raise a System Alert ticket that carries the original
        email, ring the admin bell, and mark the row given up. All in one
        transaction; if any of it fails, the row keeps its copy and the next run
        tries the give-up again. Returns True when MB holds the message."""
        from core.system_alerts import create_system_alert

        attached = raw is not None
        body = (
            f"Murphy's Bench tried {log.attempts} times to turn an inbound email into a ticket and gave up.\n\n"
            f'From:       {log.from_email or "(unknown)"}\n'
            f'Subject:    {log.subject or "(no subject)"}\n'
            f'Message-ID: {log.message_id}\n'
            f'First seen: {timezone.localtime(log.created_at):%Y-%m-%d %H:%M}\n'
            f'Last error: {log.detail or "(none recorded)"}\n\n'
            + ('The original email is attached as original-email.eml. Open it and create the ticket by hand.'
               if attached else
               "MB's saved copy of this email could not be read, so it is not attached. "
               'Look for the email in the mailbox.')
        )
        written = []
        old_copy = (log.raw_copy.storage, log.raw_copy.name) if log.raw_copy else None
        try:
            with transaction.atomic():
                # The log id keeps each email's alert its own ticket: the alert
                # helper merges same-subject alerts within an hour.
                alert = create_system_alert(
                    f'Inbound email could not become a ticket (log #{log.pk})', body)
                if attached:
                    a = Attachment(
                        content_type=ContentType.objects.get_for_model(alert),
                        object_id=alert.pk,
                        original_filename='original-email.eml',
                        mime_type='message/rfc822',
                        size_bytes=len(raw),
                    )
                    a.file.save('original-email.eml', ContentFile(raw), save=True)
                    written.append((a.file.storage, a.file.name))
                log.status = 'gave_up'
                log.ticket = alert
                log.raw_copy = ''
                log.detail = (f'Gave up after {log.attempts} attempts; System Alert '
                              f'{alert.ticket_number} raised. {log.detail}')[:5000]
                log.save(update_fields=['status', 'ticket', 'raw_copy', 'detail'])
        except Exception:
            logger.exception('Could not raise the give-up alert for inbound message %s.',
                             log.message_id)
            _delete_files(written)
            # If MB still holds a copy, the next run retries the give-up from it.
            return bool(old_copy)
        if old_copy:
            _delete_files([old_copy])   # the alert ticket now carries the email
        self.counts['gave_up'] += 1
        self._say('gave_up', log.detail)
        return True
