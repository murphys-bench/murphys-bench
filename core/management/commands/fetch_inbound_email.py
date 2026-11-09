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
     some servers (Gmail POP) never offer a downloaded message again. A copy
     that cannot be read is kept and reported, never given up on.
  4. After MAX_ATTEMPTS failures MB gives up: a System Alert ticket carries the
     original message as an .eml attachment and rings the admin bell.
  5. POP3 ends with QUIT only when MB holds every message it retrieved (Gmail
     marks RETR'd mail as taken at QUIT); IMAP expunges only the UIDs it handled.
  6. A message recorded before these rules existed ('needs_review', or an old
     'error' that was not a refusal) goes to a person if it arrives again.
  7. A saved copy carries a fingerprint. A damaged copy is never trusted, and an
     intact copy never silently wins over a delivery that differs from it.
  8. A message that will not download goes to the back of the queue, so it cannot
     hold up later mail. After MAX_ATTEMPTS failures MB alerts and skips it.
"""

import contextvars
import email
from datetime import timedelta
import fcntl
import hashlib
import imaplib
import logging
import os
import poplib
import re
import time

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
    Attachment, BlockedSender, Client, Contact, InboundEmailLog,
    InboundStuckDownload, SiteSettings, Ticket, TicketLink, TicketReply,
)

TICKET_RE = re.compile(r'\[?(TKT-[\d-]+)\]?', re.IGNORECASE)

# How many times one message is tried before MB gives up and raises an alert.
MAX_ATTEMPTS = 3

# How long MB keeps its saved copy after the message is ticketed or handed to a
# System Alert. Deleting it at once let a backup record a pending retry (database
# snapshot) and then miss its file (copied a moment later).
COPY_GRACE = timedelta(hours=24)

# A message that has failed to download MAX_ATTEMPTS times is skipped so POP3 can
# end cleanly, then tried once more this often in case the server has recovered.
STUCK_RETRY_EVERY = timedelta(hours=1)

# No new download starts after this long in one run, so saved retries still get
# their turn behind a big backlog. Anything left stays in the mailbox.
DRAIN_TIME_LIMIT_SECONDS = 600
# The end of that time is held for messages waiting to try their download again,
# so steady new mail cannot keep them out forever.
DUE_RESERVE_SECONDS = 120
_clock = time.monotonic

# Statuses after which a message is finished with: it is never tried again, and
# if it shows up in the mailbox again it is only removed.
FINAL_STATUSES = frozenset({
    'new_ticket', 'reply', 'reply_flagged', 'new_ticket_linked',
    'refused', 'gave_up', 'duplicate', 'handled',
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
        # File first, then record it, THEN the row: if the row save fails, the
        # caller still knows which file to delete.
        a.file.save(filename, ContentFile(data), save=False)
        written = _attempt_files.get()
        if written is not None:
            written.append((a.file.storage, a.file.name))
        a.save()


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


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _fingerprint(raw):
    """Identifies one email's contents. Line endings at the very end are ignored,
    because POP3 and IMAP do not report them the same way."""
    return _sha256(raw.rstrip(b'\r\n'))


def _identify(raw_bytes):
    """Return (claim_key, from_email, subject, old_key) for the log row. old_key
    is what MB versions before Nov 2026 stored for a very long Message-ID (its
    first 500 characters), else ''.

    The claim key is the Message-ID. A message without one is keyed by a hash of
    its raw bytes instead, so it can still be deduplicated and its retries
    counted (identical bytes are the same delivery). Line endings at the very end
    are ignored, because POP3 and IMAP do not report them the same way."""
    mid = frm = subj = old_key = ''
    try:
        msg_obj = email.message_from_bytes(raw_bytes)
        mid = (msg_obj.get('Message-ID') or '').strip()
        frm = parseaddr(_decode_header_str(msg_obj.get('From', '')))[1][:255]
        subj = _decode_header_str(msg_obj.get('Subject', ''))[:500]
    except Exception:
        logger.exception('Could not read the headers of an inbound message.')
    if not mid:
        mid = 'no-message-id:sha256:' + _fingerprint(raw_bytes)
    elif len(mid) > 500:
        # Too long for the column. Cutting it short could make two different
        # messages look like one, and the second would be dropped as a duplicate.
        old_key = mid[:500]
        mid = 'long-message-id:sha256:' + _sha256(mid.encode('utf-8', 'backslashreplace'))
    return mid, frm, subj, old_key


# ── Mailboxes ─────────────────────────────────────────────────────────────────
# Both expose the same three steps, so the intake loop never needs to know which
# protocol it is talking to: messages() yields (ref, raw_bytes) without changing
# anything on the server, done(ref) takes one message out of the queue, and
# close() ends the session. With dry_run, nothing on the server changes at all.

_UID_RE = re.compile(rb'\bUID\s+(\d+)', re.IGNORECASE)

# Seconds a silent mail server may hold up one network operation. Without a limit
# a hung server blocks the run forever, so saved retries and give-up alerts never
# happen and the run lock stays held.
MAILBOX_TIMEOUT = 60


class _ImapMailbox:
    def __init__(self, site, dry_run=False):
        if site.inbound_ssl:
            self.conn = imaplib.IMAP4_SSL(site.inbound_host, site.inbound_port, timeout=MAILBOX_TIMEOUT)
        else:
            self.conn = imaplib.IMAP4(site.inbound_host, site.inbound_port, timeout=MAILBOX_TIMEOUT)
        self.conn.login(site.inbound_username, site.inbound_password)
        self.folder = site.inbound_folder or 'INBOX'
        typ, _ = self.conn.select(self.folder)
        if typ != 'OK':
            raise imaplib.IMAP4.error(f'Could not open IMAP folder {self.folder!r}')
        self.delete = site.inbound_delete_after_fetch
        # Capabilities can change after login, so ask now rather than trusting
        # the greeting.
        typ, data = self.conn.capability()
        caps = data[0].upper().split() if typ == 'OK' and data and data[0] else []
        # UID EXPUNGE (RFC 4315) removes only the messages we name. A plain
        # EXPUNGE removes EVERY message in the folder flagged \Deleted, including
        # ones another mail client flagged and MB never handled.
        self.uidplus = b'UIDPLUS' in caps
        self._to_expunge = []
        if self.delete and not self.uidplus:
            logger.warning('IMAP server has no UIDPLUS: handled messages are marked read and '
                           'flagged deleted, but MB will not purge the folder.')

    def messages(self):
        # UIDs, not sequence numbers: we hold the session open while tickets are
        # made, and a person deleting mail in webmail meanwhile would shift
        # sequence numbers under us.
        typ, data = self.conn.uid('SEARCH', None, 'UNSEEN')
        if typ != 'OK':
            # Not the same as an empty mailbox: an empty list would clear the
            # record of failing downloads.
            raise imaplib.IMAP4.error('IMAP SEARCH failed')
        uids = data[0].split() if data and data[0] else []
        keys = {uid: f'imap:{self.folder}:{uid.decode(errors="replace")}' for uid in uids}
        for uid in self.tracker.order(keys):
            if not self.tracker.has_time(keys[uid]):
                continue
            self.current = keys[uid]
            # BODY.PEEK[]: a plain RFC822 / BODY[] fetch marks the message read
            # on the server, which would take it out of the queue before MB has it.
            typ, msg_data = self.conn.uid('FETCH', uid, '(BODY.PEEK[])')
            raw = None
            parts = list(msg_data or [])
            for n, part in enumerate(parts):
                # Only the literal whose response names THIS uid and carries the
                # whole body counts; a server may also send unrelated FETCH data.
                # The UID item may come before the literal or after it (in the
                # bytes element that closes the response).
                if not (isinstance(part, tuple) and len(part) == 2
                        and b'BODY[]' in part[0].upper()):
                    continue
                tail = parts[n + 1] if n + 1 < len(parts) and isinstance(parts[n + 1], bytes) else b''
                m = _UID_RE.search(part[0]) or _UID_RE.search(tail)
                if m and m.group(1) == uid:
                    raw = part[1]
                    break
            if typ != 'OK' or raw is None:
                logger.warning('IMAP FETCH of UID %s did not return that message; left in the mailbox.',
                               uid.decode(errors='replace'))
                self.tracker.failed(keys[uid], 'The server did not return the message.')
                self.current = None
                continue
            self.tracker.succeeded(keys[uid])
            self.current = None
            yield uid, raw

    def done(self, uid):
        if not self.delete:
            flag = '(\\Seen)'
        elif self.uidplus:
            flag = '(\\Deleted)'
        else:
            flag = '(\\Seen \\Deleted)'
        typ, _ = self.conn.uid('STORE', uid, '+FLAGS', flag)
        if typ != 'OK':
            raise imaplib.IMAP4.error(f'STORE {flag} on UID {uid} failed')
        if self.delete and self.uidplus:
            self._to_expunge.append(uid)

    def close(self):
        try:
            if self._to_expunge:
                typ, _ = self.conn.uid('EXPUNGE', b','.join(self._to_expunge))
                if typ != 'OK':
                    # Still flagged \Deleted but unread, so the next run fetches
                    # them, recognises them and tries the expunge again.
                    logger.warning('IMAP UID EXPUNGE failed; will retry next run.')
        finally:
            self.conn.logout()


class _Pop3Mailbox:
    """POP3 applies DELE only at QUIT, and Gmail (normal mode) stops offering
    every message RETR'd in a session that ends with QUIT, DELE or not. So QUIT
    is sent only when MB holds every message it retrieved; otherwise the session
    is dropped without it, and the server offers them all again next run (the
    held ones are then recognised from their log rows and removed)."""

    def __init__(self, site, dry_run=False):
        if site.inbound_ssl:
            self.conn = poplib.POP3_SSL(site.inbound_host, site.inbound_port, timeout=MAILBOX_TIMEOUT)
        else:
            self.conn = poplib.POP3(site.inbound_host, site.inbound_port, timeout=MAILBOX_TIMEOUT)
        self.conn.user(site.inbound_username)
        self.conn.pass_(site.inbound_password)
        self.dry_run = dry_run
        self._retrieved = set()
        self._held = set()

    def _keys(self, count):
        """A stable id per message. UIDL is optional in POP3; without it, fall
        back on the message's position, which is good enough to take turns."""
        ids = {}
        try:
            _, lines, _ = self.conn.uidl()
            ids = dict(line.split(None, 1) for line in lines)
        except poplib.error_proto:
            logger.warning('POP3 server does not support UIDL; tracking failed downloads by position.')
        keys = {}
        for i in range(1, count + 1):
            uid = ids.get(str(i).encode())
            keys[i] = f'pop3:{uid.decode(errors="replace")}' if uid else f'pop3:position:{i}'
        return keys

    def messages(self):
        count, _ = self.conn.stat()
        keys = self._keys(count)
        for i in self.tracker.order(keys):
            if not self.tracker.has_time(keys[i]):
                continue
            self.current = keys[i]
            if self.dry_run:
                # Headers only (TOP), and the session is never QUIT: a dry run
                # must not take anything out of the queue.
                _, lines, _ = self.conn.top(i, 0)
            else:
                self._retrieved.add(i)   # before RETR: a half-read counts too
                _, lines, _ = self.conn.retr(i)
            self.tracker.succeeded(keys[i])
            self.current = None
            # poplib strips every line ending, including the last one.
            yield i, b'\r\n'.join(lines) + b'\r\n'

    def done(self, i):
        self.conn.dele(i)
        self._held.add(i)

    def close(self):
        if self.dry_run or self._retrieved - self._held:
            if not self.dry_run:
                logger.warning('Ending the POP3 session without QUIT: %d retrieved message(s) '
                               'are not yet safe in MB, so the server must offer them again.',
                               len(self._retrieved - self._held))
            self.conn.close()
        else:
            self.conn.quit()


def _delete_files(files):
    for storage, name in files:
        try:
            storage.delete(name)
        except Exception:
            logger.exception('Could not delete stored file %s.', name)


class _StuckDownloads:
    """Remembers messages whose download failed, across runs. MB never saw their
    contents, so all it has is the server's id for each one."""

    def __init__(self, dry_run):
        self.dry_run = dry_run
        self.rows = {} if dry_run else {r.key: r for r in InboundStuckDownload.objects.all()}
        self.started = _clock()
        self.due_waiting = False
        self.due_turn_taken = False
        self.warned = False

    def order(self, keys):
        """Given {ref: key}, return the refs to download: healthy messages first,
        then any that failed before, longest-waiting first. Messages past their
        attempts are left out until their next hourly try."""
        retry_before = timezone.now() - STUCK_RETRY_EVERY
        healthy, failing = [], []
        for ref, key in keys.items():
            row = self.rows.get(key)
            if row is None:
                healthy.append(ref)
            elif row.failures < MAX_ATTEMPTS or row.last_failed_at <= retry_before:
                failing.append((row.last_failed_at, ref))
        failing.sort(key=lambda item: item[0])
        self.due_waiting = bool(failing)
        gone = set(self.rows) - set(keys.values())
        if gone:
            InboundStuckDownload.objects.filter(key__in=gone).delete()
            for key in gone:
                del self.rows[key]
        return healthy + [ref for _, ref in failing]

    def has_time(self, key):
        """May this download start now? Checked before every attempt, whatever
        became of the one before it."""
        if key in self.rows and not self.due_turn_taken:
            # Every run gives one waiting message its turn, even if a slow message
            # used up the reserve. That is at most one download past the limit.
            self.due_turn_taken = True
            return True
        reserve = DUE_RESERVE_SECONDS if self.due_waiting and key not in self.rows else 0
        if _clock() - self.started <= DRAIN_TIME_LIMIT_SECONDS - reserve:
            return True
        if not self.warned:
            self.warned = True
            logger.warning('Inbound fetch reached its time limit; the remaining mail stays in the '
                           'mailbox for the next run.')
        return False

    def succeeded(self, key):
        row = self.rows.pop(key, None)
        if row is not None:
            row.delete()

    def failed(self, key, error):
        """Record a failed download. Never raises: the retry pass must still run."""
        if self.dry_run:
            return
        try:
            row = self.rows.get(key) or InboundStuckDownload(key=key[:255])
            row.failures += 1
            row.last_failed_at = timezone.now()
            row.last_error = str(error)[:1000]
            row.save()
            self.rows[key] = row
            if row.failures >= MAX_ATTEMPTS and not row.alerted:
                self._alert(row)
        except Exception:
            logger.exception('Could not record the failed download of %s.', key)

    def _alert(self, row):
        from core.system_alerts import create_system_alert
        with transaction.atomic():
            alert = create_system_alert(
                # One fixed subject: the alert helper folds repeats within the hour
                # into this ticket, so an outage is one alert and not one per message.
                'Inbound email could not be downloaded',
                f"Murphy's Bench tried {row.failures} times to download a message and could not, "
                'so it does not know who sent it. The message is still in the mailbox. MB now skips '
                'it so that later mail keeps arriving, and tries it again once an hour. If several '
                'messages fail together, this one alert covers them all, which usually means the '
                'mail server is having trouble.\n\n'
                f'Mailbox reference: {row.key}\n'
                f'Last error:        {row.last_error}\n\n'
                'If a later try succeeds the message becomes a ticket as usual. If this continues, '
                'look in the mailbox for a very large or damaged message, create the ticket by hand, '
                'and remove the message from the mailbox.',
            )
            # Whole line, so imap:INBOX:10 is not mistaken for imap:INBOX:100.
            if f'Mailbox reference: {row.key}\n' not in alert.description:   # folded into an earlier alert
                alert.description += f'\n\nAlso failing: {row.key} ({row.last_error})'
                alert.save(update_fields=['description'])
            row.alerted = True
            row.save(update_fields=['alerted'])


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
                'copy_unreadable': 0,
            }
            self.touched = set()   # log rows already handled in this run
            self.site = site

            try:
                mailbox = self._open_mailbox(site, dry_run)
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
                    if mailbox.current:
                        # Died downloading this message. The session is unusable,
                        # so remember it; it waits at the back of the queue next run.
                        self.stuck.failed(mailbox.current, f'{type(exc).__name__}: {exc}')
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
                try:
                    self._purge_expired_copies()
                except Exception:
                    logger.exception('Purging expired inbound copies failed; will retry next run.')

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

    def _open_mailbox(self, site, dry_run=False):
        self.stuck = _StuckDownloads(dry_run)
        mailbox = (_Pop3Mailbox if site.inbound_protocol == 'pop3' else _ImapMailbox)(site, dry_run)
        mailbox.tracker = self.stuck
        mailbox.current = None   # the message being downloaded right now, if any
        return mailbox

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
        key, frm, subj, old_key = _identify(raw)
        # A very long Message-ID that an older version stored shortened. It may be
        # that same email or a different one, so neither drop it nor ticket it.
        earlier = InboundEmailLog.objects.filter(message_id=old_key).first() if old_key else None
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
            if log.status == 'needs_review' or (log.status == 'error' and not _legacy_refusal(log)):
                # Recorded by MB before retries existed. That version could commit a
                # ticket and then fail to finish its log row, so whether a ticket
                # exists is unknown: a person decides, never an automatic retry.
                if log.pk in self.touched:
                    return False
                self.touched.add(log.pk)
                return self._give_up(log, raw, legacy=True)
            if log.status in FINAL_STATUSES or log.status == 'error':
                # Finished. It is back in the mailbox only because the last removal
                # did not take effect, so remove it now.
                return self._duplicate(key)
            if log.status == 'failed' or log.raw_copy:
                # MB retries this one from its own copy. Before letting the mailbox
                # copy go, make sure MB's copy is really there.
                if not self._ensure_copy(log, raw):
                    return False
                return self._duplicate(key)
            # 'processing' with no saved copy: a run died mid-attempt (the run lock
            # rules out a live one), or saving the copy failed. Take it again,
            # but not twice in one run (the same message listed twice).
            if log.pk in self.touched:
                return False
            self.touched.add(log.pk)
            if earlier is not None:
                # Before the attempts check: however many alerts have failed, the
                # one that gets through must still say to look for an existing ticket.
                return self._give_up(log, raw, legacy=True, earlier=earlier)
            if log.attempts >= MAX_ATTEMPTS:
                if not self._ensure_copy(log, raw):
                    return False
                self._give_up(log, raw)
                return True
            taken = InboundEmailLog.objects.filter(
                pk=log.pk, status='processing', attempts=log.attempts,
            ).update(attempts=F('attempts') + 1)
            if not taken:
                return False
            log.refresh_from_db()
        self.touched.add(log.pk)
        if earlier is not None:
            return self._give_up(log, raw, legacy=True, earlier=earlier)
        return self._attempt(log, raw)

    def _duplicate(self, key):
        self.counts['duplicate'] += 1
        if self.verbosity >= 1:
            self.stdout.write(self.style.WARNING(f'  [duplicate] already processed {key}'))
        return True

    def _ensure_copy(self, log, raw):
        """Make sure MB holds a usable copy of `raw` before the mailbox copy may
        go. A saved copy is replaced only by a delivery matching its fingerprint,
        which proves they are the same email. Any other delivery goes to a person
        and MB's copy is left exactly as it is, even if it cannot be read just now:
        that may be a passing fault. Returns False if the mailbox copy must stay."""
        stale = None
        if log.raw_copy:
            if _fingerprint(raw) != log.raw_copy_sha256:
                return self._keep_both(log, raw)
            stale = (log.raw_copy.storage, log.raw_copy.name)
            try:
                with log.raw_copy.open('rb') as fh:
                    held = fh.read()
            except Exception as exc:
                logger.warning('Saved copy of inbound message %s could not be read (%s: %s); '
                               'replacing it from this matching delivery.',
                               log.message_id, type(exc).__name__, exc)
            else:
                if held and _fingerprint(held) == log.raw_copy_sha256:
                    return True
                logger.warning('Saved copy of inbound message %s is damaged; replacing it from '
                               'this matching delivery.', log.message_id)
        saved = None
        try:
            log.raw_copy.save(f'{log.pk}.eml', ContentFile(raw), save=False)
            saved = (log.raw_copy.storage, log.raw_copy.name)
            log.raw_copy_sha256 = _fingerprint(raw)
            log.save(update_fields=['raw_copy', 'raw_copy_sha256'])
        except BaseException as exc:
            if saved:
                _delete_files([saved])
            if not isinstance(exc, Exception):
                raise   # Ctrl-C or exit: the half-made copy is gone, carry on stopping
            logger.exception('Could not save a copy of inbound message %s; leaving it in the mailbox.',
                             log.message_id)
            log.refresh_from_db()
            return False
        if stale and stale != saved:   # same name when the old file was gone
            _delete_files([stale])
        if self.verbosity >= 1:
            self.stdout.write(self.style.WARNING(f'  [copy restored] {log.message_id}'))
        return True

    def _attach_email(self, alert, raw, filename, written):
        a = Attachment(
            content_type=ContentType.objects.get_for_model(alert),
            object_id=alert.pk,
            original_filename=filename,
            mime_type='message/rfc822',
            size_bytes=len(raw),
        )
        a.file.save(filename, ContentFile(raw), save=False)
        written.append((a.file.storage, a.file.name))
        a.save()

    def _keep_both(self, log, raw):
        """MB's copy is intact, yet this delivery has the same id and different
        contents. Keep the copy for its retry and hand the delivery to a person.
        Returns True once the delivery is safe, so it may leave the mailbox."""
        from core.system_alerts import create_system_alert
        written = []
        try:
            with transaction.atomic():
                alert = create_system_alert(
                    f'Inbound email arrived in two versions (log #{log.pk})',
                    "Murphy's Bench is holding an inbound email for a retry, and another message "
                    'arrived with the same Message-ID but contents that do not match it.\n\n'
                    f'From:       {log.from_email or "(unknown)"}\n'
                    f'Subject:    {log.subject or "(no subject)"}\n'
                    f'Message-ID: {log.message_id}\n\n'
                    'MB keeps the one it holds, unchanged, and keeps retrying it. The other is '
                    'attached as other-version.eml. It may be a different email, so it has not '
                    'been ticketed. Read it and create a ticket by hand if it needs one.',
                )
                self._attach_email(alert, raw, 'other-version.eml', written)
        except BaseException as exc:
            _delete_files(written)
            if not isinstance(exc, Exception):
                raise
            logger.exception('Could not raise the two-versions alert for %s; leaving the delivery '
                             'in the mailbox.', log.message_id)
            return False
        self._say('two_versions', f'{log.message_id}: delivery differs from the saved copy; alert raised')
        return True

    # ── Pass 2: retries from saved copies ─────────────────────────────────────

    def _retry_saved(self):
        pending = (InboundEmailLog.objects
                   .filter(status__in=('failed', 'processing'))
                   .exclude(raw_copy='').exclude(raw_copy__isnull=True)
                   .exclude(pk__in=self.touched)
                   .order_by('created_at'))
        for log in pending:
            self.touched.add(log.pk)
            try:
                with log.raw_copy.open('rb') as fh:
                    raw = fh.read()
            except Exception as exc:
                # Never treat this as a failed attempt: the copy may be the only
                # one left. Keep the row and the file, tell the shop, try again
                # next run.
                self._copy_unreadable(log, exc)
                continue
            if not raw or _fingerprint(raw) != log.raw_copy_sha256:
                # Same rule: a damaged file must not become a damaged ticket.
                self._copy_unreadable(log, ValueError('the saved copy is empty or has changed '
                                                      'since it was saved'))
                continue
            if log.attempts >= MAX_ATTEMPTS:
                self._give_up(log, raw)   # attempts used up; only the give-up is left
                continue
            taken = InboundEmailLog.objects.filter(
                pk=log.pk, status=log.status, attempts=log.attempts,
            ).update(status='processing', attempts=F('attempts') + 1)
            if not taken:
                continue
            log.refresh_from_db()
            self._attempt(log, raw)

    def _copy_unreadable(self, log, exc):
        from core.system_alerts import create_system_alert
        logger.error('Saved copy of inbound message %s could not be read: %s: %s',
                     log.message_id, type(exc).__name__, exc)
        # A 'processing' row seen here belongs to a run that died (the run lock
        # rules out a live one). Make it 'failed' so Mark handled can reach it.
        InboundEmailLog.objects.filter(pk=log.pk, status='processing').update(status='failed')
        self.counts['copy_unreadable'] += 1
        body = (
            "Murphy's Bench is holding an inbound email that did not become a ticket, "
            'and could not read its saved copy of it.\n\n'
            f'From:       {log.from_email or "(unknown)"}\n'
            f'Subject:    {log.subject or "(no subject)"}\n'
            f'Message-ID: {log.message_id}\n'
            f'Saved copy: {log.raw_copy.name}\n'
            f'Error:      {type(exc).__name__}: {exc}\n\n'
            'MB keeps the record and tries again on every run. If the file is gone for good, '
            'find the email in the mailbox: if it is offered to MB again, MB saves a fresh copy '
            'and carries on.\n\n'
            'If you create the ticket by hand instead, go to Settings, Logs, find this email, and '
            'choose Mark handled. Until you do, MB keeps retrying and can make a second ticket.'
        )
        try:
            # One alert per email per week, not one per run.
            create_system_alert(f'Inbound email copy could not be read (log #{log.pk})', body,
                                dedupe_minutes=7 * 24 * 60)
        except Exception:
            logger.exception('Could not raise the unreadable-copy alert for %s.', log.message_id)

    # ── Old saved copies ──────────────────────────────────────────────────────

    def _purge_expired_copies(self):
        expired = (InboundEmailLog.objects
                   .filter(copy_expires_at__lte=timezone.now())
                   .exclude(raw_copy='').exclude(raw_copy__isnull=True))
        for log in expired:
            name, storage = log.raw_copy.name, log.raw_copy.storage
            # File first: if the delete fails, the row still names the file and
            # the next run tries again. These rows are finished, so one that
            # briefly names a deleted file is never read.
            try:
                storage.delete(name)
            except Exception:
                logger.exception('Could not delete expired inbound copy %s; will retry next run.', name)
                continue
            InboundEmailLog.objects.filter(pk=log.pk).update(raw_copy='', copy_expires_at=None)

    # ── One attempt, and what happens after it ────────────────────────────────

    def _attempt(self, log, raw):
        """Try to ticket `raw` once. The ticket/reply and the log row's new status
        commit together or not at all. Returns True when MB holds the message."""
        written = []
        token = _attempt_files.set(written)
        try:
            with transaction.atomic():
                status, detail, ticket = _process_message(raw, self.site, self.verbosity)
                log.status, log.detail, log.ticket = status, detail, ticket
                fields = ['status', 'detail', 'ticket']
                if log.raw_copy:
                    # Kept a while longer for backups; see COPY_GRACE.
                    log.copy_expires_at = timezone.now() + COPY_GRACE
                    fields.append('copy_expires_at')
                log.save(update_fields=fields)
        except Exception as exc:
            logger.exception('Inbound message %s failed on attempt %d.', log.message_id, log.attempts)
            _delete_files(written)   # rolled back: its attachment files must go too
            log.refresh_from_db()
            return self._failed(log, raw, f'{type(exc).__name__}: {exc}')
        except BaseException:
            _delete_files(written)   # Ctrl-C or exit: rolled back the same way
            raise
        finally:
            _attempt_files.reset(token)

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
                log.raw_copy_sha256 = _fingerprint(raw)
            log.status = 'failed'
            log.detail = f'Attempt {log.attempts} of {MAX_ATTEMPTS} failed: {error}'[:5000]
            log.save(update_fields=['status', 'detail', 'raw_copy', 'raw_copy_sha256'])
        except BaseException as exc:
            if saved:
                _delete_files([saved])
            if not isinstance(exc, Exception):
                raise   # Ctrl-C or exit: the half-made copy is gone, carry on stopping
            # No copy: leave it in the mailbox. The row stays 'processing', so the
            # next run takes it again as the next attempt.
            logger.exception('Could not save a copy of failed inbound message %s; '
                             'leaving it in the mailbox.', log.message_id)
            return False
        self._say('failed', log.detail)
        if log.attempts >= MAX_ATTEMPTS and self._give_up(log, raw):
            return True   # final: counted as gave up, not as awaiting a retry
        self.counts['failed'] += 1
        return True

    def _give_up(self, log, raw, legacy=False, earlier=None):
        """Raise a System Alert ticket that carries the original email, ring the
        admin bell, and mark the row given up, all in one transaction.

        Normally: out of attempts. With `legacy`: a message recorded by MB before
        retries existed came back, and whether it already made a ticket is
        unknown. If any of it fails, nothing is recorded; the next run tries the
        give-up again (from MB's copy, or from the mailbox, which still has it).
        Returns True when the alert was raised, or when a person finished the row
        first (Mark handled), in which case no alert is raised and nothing changes."""
        from core.system_alerts import create_system_alert

        facts = (
            f'From:       {log.from_email or "(unknown)"}\n'
            f'Subject:    {log.subject or "(no subject)"}\n'
            f'Message-ID: {log.message_id}\n'
            f'First seen: {timezone.localtime(log.created_at):%Y-%m-%d %H:%M}\n'
        )
        old = earlier or log
        if legacy:
            subject = f'Inbound email needs a manual check (log #{log.pk})'
            opening = (
                'This email has a very long Message-ID, and the start of it matches one that an '
                "earlier version of Murphy's Bench recorded. It may be that same email or a "
                f'different one. The earlier record says "{old.get_status_display()}".\n\n'
                if earlier else
                'This email arrived again. An earlier version of Murphy\'s Bench recorded it as '
                f'"{old.get_status_display()}" and may or may not have made a ticket from it.\n\n'
            )
            body = (
                opening
                + facts + f'Old detail: {old.detail or "(none recorded)"}\n\n'
                'Search tickets for this sender and subject first. If there is no ticket, open the '
                'attached original-email.eml and create one by hand.'
            )
        else:
            subject = f'Inbound email could not become a ticket (log #{log.pk})'
            body = (
                f"Murphy's Bench tried {log.attempts} times to turn an inbound email into a ticket and gave up.\n\n"
                + facts + f'Last error: {log.detail or "(none recorded)"}\n\n'
                'The original email is attached as original-email.eml. Open it and create the ticket by hand.'
            )
        written = []
        try:
            with transaction.atomic():
                # Claim first, the same way Mark handled does, so only one can win.
                claimed = InboundEmailLog.objects.filter(pk=log.pk, status=log.status).update(status='gave_up')
                if not claimed:
                    log.refresh_from_db()
                    return True
                # The log id keeps each email's alert its own ticket: the alert
                # helper merges same-subject alerts within an hour.
                alert = create_system_alert(subject, body)
                self._attach_email(alert, raw, 'original-email.eml', written)
                if legacy:
                    detail = (f'From before this update (was "{old.get_status_display()}"); '
                              f'sent for a manual check in System Alert {alert.ticket_number}. '
                              f'{old.detail}')
                else:
                    detail = (f'Gave up after {log.attempts} attempts; System Alert '
                              f'{alert.ticket_number} raised. {log.detail}')
                log.status = 'gave_up'
                log.ticket = alert
                log.detail = detail[:5000]
                fields = ['status', 'ticket', 'detail']
                if log.raw_copy:
                    log.copy_expires_at = timezone.now() + COPY_GRACE
                    fields.append('copy_expires_at')
                log.save(update_fields=fields)
        except Exception:
            logger.exception('Could not raise the alert for inbound message %s.', log.message_id)
            _delete_files(written)
            log.refresh_from_db()
            return False
        except BaseException:
            _delete_files(written)   # Ctrl-C or exit: rolled back the same way
            raise
        self.counts['gave_up'] += 1
        self._say('gave_up', log.detail)
        return True


def _legacy_refusal(log):
    """Pre-retry 'error' rows that were really deliberate refusals stay final."""
    d = log.detail or ''
    return d.startswith('Blocked sender:') or 'has suppress_emails' in d
