import logging
from fnmatch import fnmatch

logger = logging.getLogger('core')


#: EmailSendLog.detail is a CharField(255).
_DETAIL_MAX = 255


def _error_detail(exc):
    """Flatten a send exception into something the Logs page can show.

    Without this the log records only the slug `send_error`, and the actual
    cause — auth rejected, connection refused, recipient unknown — lives solely
    in murphys_bench.log on the server, which is no use to an admin looking at
    the UI. The exception type is included because SMTP errors are often terse
    or empty on their own.
    """
    text = str(exc).strip() or exc.__class__.__name__
    if exc.__class__.__name__ not in text:
        text = f'{exc.__class__.__name__}: {text}'
    text = ' '.join(text.split())
    return text[:_DETAIL_MAX - 1] + '…' if len(text) > _DETAIL_MAX else text


def _status_label(slug, entity_type):
    from .models import StatusDefinition
    sd = StatusDefinition.objects.filter(entity_type=entity_type, slug=slug).first()
    return sd.label if sd else slug.replace('_', ' ').title()


def _contrast_text_color(bg_hex):
    """Return white or near-black so text stays readable on the given background."""
    try:
        h = (bg_hex or '').lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return '#1f2937' if luminance > 0.6 else '#ffffff'
    except Exception:
        return '#ffffff'


def _load_logo_resized(path, mime_type, max_px=480):
    """Return logo bytes scaled to <= max_px on the long side (keeps emails small
    and the header logo a sane size). Falls back to the original bytes on error."""
    try:
        import io
        from PIL import Image
        img = Image.open(path)
        img.thumbnail((max_px, max_px))
        fmt = {'image/jpeg': 'JPEG', 'image/png': 'PNG',
               'image/gif': 'GIF', 'image/webp': 'WEBP'}.get(mime_type, 'PNG')
        if fmt == 'JPEG' and img.mode in ('RGBA', 'P'):
            img = img.convert('RGB')
        buf = io.BytesIO()
        img.save(buf, format=fmt)
        return buf.getvalue()
    except Exception:
        try:
            with open(path, 'rb') as f:
                return f.read()
        except Exception:
            return None


def _email_header_color(site):
    """The email header bar color — the dedicated email setting, else the app's
    Title Bar color, else a dark fallback."""
    return getattr(site, 'email_header_color', '') or '#1f2937'


def _email_logo_field(site):
    """The logo to use in emails — the dedicated email logo, else the company logo."""
    return getattr(site, 'email_logo', None) or site.company_logo


def _suppression_reason(to_email, client, site):
    """Return (reason, detail) if this address must NOT be emailed, else ('', '').

    Shared by every outbound path so suppression rules live in one place. Layers:
    per-client suppress flag, the pattern blocklist, and the exact-address list.
    Does NOT consider Contact.receives_email — that's a recipient-level check the
    caller applies when it has resolved a specific contact.
    """
    from .models import SuppressedAddress
    # A prospect can stand in for the client here; it carries no suppress flag,
    # so only the pattern and exact-address layers apply to it.
    if client is not None and getattr(client, 'suppress_emails', False):
        return 'client_flag', ''
    patterns = [p.strip() for p in site.email_suppression_patterns.splitlines() if p.strip()]
    for pattern in patterns:
        if fnmatch(to_email.lower(), pattern.lower()):
            return 'pattern', pattern
    if SuppressedAddress.objects.filter(email__iexact=to_email).exists():
        return 'exact_address', ''
    return '', ''


def _build_html_email(body, signature_body, subject, ticket, site, embed_logo=True):
    """Render the HTML email wrapper. Returns (html_str, logo_data, logo_mime_type).

    embed_logo=False skips the inline CID logo (the header falls back to the
    company name) — used by document cover emails, where the branding lives in
    the attached PDF and a 'related' inline image would complicate the MIME tree
    that also carries the attachment.
    """
    from django.template.loader import render_to_string
    import os

    logo_data = None
    logo_mime_type = 'image/png'
    has_logo = False

    logo_field = _email_logo_field(site) if embed_logo else None
    if logo_field:
        try:
            logo_path = logo_field.path
            if os.path.isfile(logo_path):
                ext = os.path.splitext(logo_path)[1].lower()
                logo_mime_type = {
                    '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                    '.png': 'image/png', '.gif': 'image/gif',
                    '.webp': 'image/webp',
                }.get(ext, 'image/png')
                logo_data = _load_logo_resized(logo_path, logo_mime_type)
                has_logo = logo_data is not None
        except Exception:
            logger.exception('Failed to load company logo for email.')

    title_bar_color = _email_header_color(site)
    html = render_to_string('core/email/base_email.html', {
        'subject': subject,
        'body': body,
        'signature': signature_body,
        'company_name': site.company_name or "Murphy's Bench",
        'has_logo': has_logo,
        'title_bar_color': title_bar_color,
        # Contrast against the bar color — never the dark page-title color.
        'title_text_color': _contrast_text_color(title_bar_color),
        'ticket_number': ticket.ticket_number if ticket else '',
    })
    return html, logo_data, logo_mime_type


def _resolve_ticket_contact(ticket):
    """The contact an automated ticket email is addressed to: the ticket's
    assigned contact (when it has an email), else the client's primary contact,
    else any active contact with an email, else the assigned contact as-is.
    Pure given the ticket's related rows — kept separate so it's unit-testable."""
    contact = ticket.contact if ticket.contact_id else None
    if contact and contact.email:
        return contact
    return (ticket.client.contacts.filter(is_primary=True, is_active=True).first()
            or ticket.client.contacts.filter(is_active=True, email__gt='').first()
            or contact)


def _greeting_name(client, contact):
    """Name for a personal salutation in an email body. Always the contact's
    first name when there's a contact on file ("Hi Wayne,") — for business
    clients too, since the mail goes to a company but still greets a person.
    Falls back to the client name only when no contact exists."""
    if contact and contact.first_name:
        return contact.first_name
    return client.name


def _smtp_send(site, subject, plain_body, html_body, logo_data, logo_mime_type, to_email, cc=None, bcc=None, label=''):
    """Send one branded message through the shop's SMTP settings. Returns
    (status, reason, detail) for the send log; never raises."""
    from django.core.mail import EmailMultiAlternatives, get_connection
    from django.conf import settings as django_settings

    use_ssl = site.email_port == 465
    connection = get_connection(
        backend='django.core.mail.backends.smtp.EmailBackend',
        host=site.email_host or django_settings.EMAIL_HOST,
        port=site.email_port or django_settings.EMAIL_PORT,
        username=site.email_username or django_settings.EMAIL_HOST_USER,
        password=site.email_password or django_settings.EMAIL_HOST_PASSWORD,
        use_tls=site.email_use_tls and not use_ssl,
        use_ssl=use_ssl,
        fail_silently=True,
    )
    from_email = site.email_from or django_settings.DEFAULT_FROM_EMAIL
    try:
        msg = EmailMultiAlternatives(
            subject=subject.strip(),
            body=plain_body,
            from_email=from_email,
            to=[to_email],
            cc=[e for e in (cc or []) if e and e != to_email],
            bcc=[e for e in (bcc or []) if e and e != to_email],
            connection=connection,
        )
        msg.attach_alternative(html_body, 'text/html')
        if logo_data:
            from email.mime.image import MIMEImage
            # 'related' (not the default 'mixed') so the image is bound to the HTML
            # and `cid:logo` resolves inline.
            msg.mixed_subtype = 'related'
            img = MIMEImage(logo_data, _subtype=logo_mime_type.split('/')[-1])
            img.add_header('Content-ID', '<logo>')
            img.add_header('Content-Disposition', 'inline', filename='logo')
            msg.attach(img)
        sent = msg.send(fail_silently=False)
        if sent:
            return 'sent', '', ''
        return 'failed', 'send_error', 'SMTP accepted no recipients'
    except Exception as exc:
        logger.exception('SMTP send failed for %s -> %s.', label, to_email)
        return 'failed', 'send_error', _error_detail(exc)


def render_email_template(template, ctx):
    """Render a template's subject and body against ctx (a plain dict).
    Raises on a syntax error so the caller can report it."""
    from django.template import Template, Context
    context = Context(ctx, autoescape=False)
    return (Template(template.subject_template).render(context).strip(),
            Template(template.body_template).render(context))


def email_context(*, client, contact=None, ticket=None, work_order=None, user=None, site=None):
    """The variables every client-facing template can use. Shared by the event
    path and the hand-sent custom path so a template written for one works in
    the other."""
    from .models import SiteSettings
    site = site or SiteSettings.get()
    tech = None
    if ticket is not None and ticket.created_by:
        tech = ticket.created_by
    elif work_order is not None and getattr(work_order, 'assigned_to', None):
        tech = work_order.assigned_to
    elif user is not None:
        tech = user
    return {
        'ticket': ticket,
        'work_order': work_order,
        'client': client,
        'contact': contact,
        'customer_name': _greeting_name(client, contact),
        'tech_name': tech.get_full_name() if tech else '',
        'status': _status_label(ticket.status, 'ticket') if ticket is not None else '',
        'site_name': site.company_name or "Murphy's Bench",
    }


def send_custom_email(template, *, to_email, client, contact=None, ticket=None,
                      work_order=None, user=None, subject=None, body=None, cc=None):
    """Send a custom (person-chosen) template to one address. subject/body,
    when given, are the already-edited text from the compose screen and win
    over a fresh render. Honors every suppression layer; always writes an
    EmailSendLog row. Returns the log row."""
    from .models import SiteSettings, EmailSignature, EmailSendLog
    site = SiteSettings.get()
    trigger = f'custom:{template.name or template.pk}'[:30]

    def _log(status, reason='', detail=''):
        return EmailSendLog.objects.create(
            ticket=ticket, to_email=to_email or '', trigger=trigger,
            status=status, reason=reason, detail=detail,
        )

    if not site.email_enabled:
        return _log('suppressed', 'email_disabled')
    if not to_email:
        return _log('suppressed', 'no_address')
    # Recipient-level opt-out, same as document email.
    if contact is not None and not contact.receives_email:
        return _log('suppressed', 'contact_flag')
    reason, detail = _suppression_reason(to_email, client, site)
    if reason:
        return _log('suppressed', reason, detail)

    if subject is None or body is None:
        try:
            subject, body = render_email_template(
                template, email_context(client=client, contact=contact, ticket=ticket,
                                        work_order=work_order, user=user, site=site))
        except Exception:
            logger.exception('Email template render failed for custom template %s.', template.pk)
            return _log('failed', 'send_error', 'template render error')

    sig_obj = template.signature or EmailSignature.objects.filter(is_default=True).first()
    signature_body = sig_obj.body if sig_obj else ''
    html_body, logo_data, logo_mime_type = _build_html_email(body, signature_body, subject, ticket, site)
    plain_body = f"{body}\n\n--\n{signature_body}" if signature_body else body
    status, reason, detail = _smtp_send(site, subject, plain_body, html_body, logo_data, logo_mime_type,
                                        to_email, cc=cc, label=trigger)
    return _log(status, reason, detail)


def send_ticket_email(trigger, ticket, extra_context=None, cc=None, bcc=None):
    """
    Send an automated email for a ticket event.
    Checks all three suppression layers before sending.
    Always writes an EmailSendLog entry for auditing.
    """
    from .models import SiteSettings, EmailTemplate, EmailSignature, EmailSendLog

    site = SiteSettings.get()

    if not site.email_enabled:
        return

    # Resolve the contact this email is addressed to (used for both the
    # recipient address and the greeting name).
    contact = _resolve_ticket_contact(ticket)

    # Allow explicit override (e.g. resend to a specific address)
    if extra_context and extra_context.get('_override_to'):
        to_email = extra_context.pop('_override_to')
    else:
        to_email = (contact.email if contact else '') or ticket.client.email

    if not to_email:
        EmailSendLog.objects.create(
            ticket=ticket, to_email='', trigger=trigger,
            status='suppressed', reason='no_address',
        )
        return

    # Suppression layers (client flag → pattern blocklist → exact-address list)
    reason, detail = _suppression_reason(to_email, ticket.client, site)
    if reason:
        EmailSendLog.objects.create(
            ticket=ticket, to_email=to_email, trigger=trigger,
            status='suppressed', reason=reason, detail=detail,
        )
        return

    # Get active template
    template = EmailTemplate.objects.filter(trigger=trigger, is_active=True).select_related('signature').first()
    if not template:
        return  # No template → no email, no log entry (intentionally quiet)

    # Resolve signature: template's own → default → none
    sig_obj = template.signature or EmailSignature.objects.filter(is_default=True).first()
    signature_body = sig_obj.body if sig_obj else ''

    # Render subject and body as Django templates
    from django.template import Template, Context
    ctx = {
        'ticket': ticket,
        'client': ticket.client,
        'contact': contact,
        'customer_name': _greeting_name(ticket.client, contact),
        'tech_name': ticket.created_by.get_full_name() if ticket.created_by else '',
        'status': _status_label(ticket.status, 'ticket'),
        'site_name': site.company_name or "Murphy's Bench",
    }
    if extra_context:
        ctx.update(extra_context)
    context = Context(ctx, autoescape=False)

    try:
        subject = Template(template.subject_template).render(context)
        body = Template(template.body_template).render(context)
    except Exception:
        logger.exception(
            'Email template render failed for trigger %s (ticket %s) — check the '
            'template syntax in Settings → Email Templates.',
            trigger, getattr(ticket, 'ticket_number', '?'),
        )
        EmailSendLog.objects.create(
            ticket=ticket, to_email=to_email, trigger=trigger,
            status='failed', reason='send_error', detail='template render error',
        )
        return

    # Build HTML version
    html_body, logo_data, logo_mime_type = _build_html_email(body, signature_body, subject.strip(), ticket, site)

    # Plain-text version appends signature below a separator
    plain_body = body
    if signature_body:
        plain_body = f"{body}\n\n--\n{signature_body}"

    status, reason, detail = _smtp_send(site, subject, plain_body, html_body, logo_data, logo_mime_type,
                                        to_email, cc=cc, bcc=bcc, label=f'trigger {trigger}')

    EmailSendLog.objects.create(
        ticket=ticket, to_email=to_email, trigger=trigger,
        status=status, reason=reason, detail=detail,
    )


def send_document_email(to_email, subject, cover_body, *, from_email=None,
                        reply_to=None, attachments=None, client=None,
                        contact=None, cc=None, trigger='document',
                        related_ticket=None):
    """Send an MB-generated document (repair report, quote) as a short HTML cover
    email with PDF attachment(s).

    Honors every suppression layer and always writes an EmailSendLog row (ticket
    optional — these aren't ticket-triggered). Fail-loud: SMTP errors are logged
    and recorded as 'failed', never swallowed. Returns the EmailSendLog row.

    attachments: list of (filename, content_bytes, mimetype).
    """
    from .models import SiteSettings, EmailSendLog, EmailSignature
    from django.core.mail import EmailMultiAlternatives, get_connection
    from django.conf import settings as django_settings

    site = SiteSettings.get()

    def _log(status, reason='', detail=''):
        return EmailSendLog.objects.create(
            ticket=related_ticket, to_email=to_email or '', trigger=trigger,
            status=status, reason=reason, detail=detail,
        )

    if not site.email_enabled:
        return _log('suppressed', 'email_disabled')
    if not to_email:
        return _log('suppressed', 'no_address')
    # Recipient-level opt-out (a specific contact who declines automated mail).
    if contact is not None and not contact.receives_email:
        return _log('suppressed', 'contact_flag')

    reason, detail = _suppression_reason(to_email, client, site)
    if reason:
        return _log('suppressed', reason, detail)

    # Short branded cover (no inline logo — the PDF carries the branding, and a
    # 'related' image would complicate the multipart/mixed tree that holds the
    # attachment). Default signature if one is configured.
    sig_obj = EmailSignature.objects.filter(is_default=True).first()
    signature_body = sig_obj.body if sig_obj else ''
    html_body, _logo, _mime = _build_html_email(
        cover_body, signature_body, subject, None, site, embed_logo=False,
    )
    plain_body = cover_body
    if signature_body:
        plain_body = f"{cover_body}\n\n--\n{signature_body}"

    use_ssl = site.email_port == 465
    connection = get_connection(
        backend='django.core.mail.backends.smtp.EmailBackend',
        host=site.email_host or django_settings.EMAIL_HOST,
        port=site.email_port or django_settings.EMAIL_PORT,
        username=site.email_username or django_settings.EMAIL_HOST_USER,
        password=site.email_password or django_settings.EMAIL_HOST_PASSWORD,
        use_tls=site.email_use_tls and not use_ssl,
        use_ssl=use_ssl,
        fail_silently=True,
    )

    sender = from_email or site.email_from or django_settings.DEFAULT_FROM_EMAIL

    try:
        msg = EmailMultiAlternatives(
            subject=subject,
            body=plain_body,
            from_email=sender,
            to=[to_email],
            cc=[e for e in (cc or []) if e and e != to_email],
            reply_to=[reply_to] if reply_to else None,
            connection=connection,
        )
        msg.attach_alternative(html_body, 'text/html')
        for filename, content, mimetype in (attachments or []):
            msg.attach(filename, content, mimetype)
        sent = msg.send(fail_silently=False)
        status = 'sent' if sent else 'failed'
        reason = '' if sent else 'send_error'
        detail = '' if sent else 'SMTP accepted no recipients'
    except Exception as exc:
        logger.exception('SMTP send failed for %s → %s.', trigger, to_email)
        status, reason = 'failed', 'send_error'
        detail = _error_detail(exc)

    return _log(status, reason, detail)


# ---------------------------------------------------------------------------
# Internal notification email — the SHOP hearing about work, not the client.
# These deliberately skip the client suppression layers (recipients are staff)
# but honor the outbound master switch, and they never raise: a notification
# that cannot send must not break ticket intake or assignment.
# ---------------------------------------------------------------------------

def notification_admins():
    """Users who catch shop-level notifications when nothing narrower applies:
    staff, or anyone whose role can manage settings. Shared with the in-app
    tech-message fallback in views."""
    from django.db.models import Q
    from .models import User
    return list(User.objects.filter(
        Q(is_staff=True) | Q(role_obj__can_manage_settings=True)
    ).distinct())


def _new_ticket_recipients(site, exclude_user=None):
    """Who hears about a brand-new ticket (and about replies on unassigned
    tickets): the configured list, else every admin. The acting user is
    excluded — nobody needs an email about the ticket they just made."""
    users = list(site.notify_new_ticket_users.all()) or notification_admins()
    return [u for u in users
            if u.email and u.is_active and (exclude_user is None or u.pk != exclude_user.pk)]


def send_internal_email(site, subject, body, to_email, ticket=None, trigger='internal'):
    """One plain internal email to a staff address. Logged like every other
    send; no branding, no signature — this is a shop pager, not client mail."""
    from .models import EmailSendLog
    from django.utils.html import escape
    if not site.email_enabled:
        return EmailSendLog.objects.create(
            ticket=ticket, to_email=to_email or '', trigger=trigger,
            status='suppressed', reason='email_disabled')
    if not to_email:
        return EmailSendLog.objects.create(
            ticket=ticket, to_email='', trigger=trigger,
            status='suppressed', reason='no_address')
    html_body = '<p>' + escape(body).replace('\n', '<br>') + '</p>'
    status, reason, detail = _smtp_send(site, subject, body, html_body,
                                        None, None, to_email, label=trigger)
    return EmailSendLog.objects.create(
        ticket=ticket, to_email=to_email, trigger=trigger,
        status=status, reason=reason, detail=detail)


def _ticket_summary(ticket):
    who = ticket.client.name if ticket.client_id else 'Walk-in'
    if ticket.contact and (ticket.contact.first_name or ticket.contact.last_name):
        who = f'{ticket.contact.first_name} {ticket.contact.last_name}'.strip() + f' ({ticket.client.name})'
    return who


def notify_new_ticket(ticket, actor=None):
    """Email the new-ticket recipients that a ticket arrived. Never raises."""
    try:
        from .models import SiteSettings
        site = SiteSettings.get()
        if not site.notify_new_ticket:
            return
        subject = f'[{ticket.ticket_number}] New ticket: {ticket.subject}'
        body = (f'A new ticket has arrived.\n\n'
                f'Ticket:  {ticket.ticket_number}\n'
                f'From:    {_ticket_summary(ticket)}\n'
                f'Subject: {ticket.subject}\n\n'
                f'{(ticket.description or "")[:500]}')
        for user in _new_ticket_recipients(site, exclude_user=actor):
            send_internal_email(site, subject, body, user.email,
                                ticket=ticket, trigger='notify:new_ticket')
    except Exception:
        logger.exception('New-ticket notification failed for %s.',
                         getattr(ticket, 'ticket_number', '?'))


def notify_ticket_reply(ticket, snippet=''):
    """Email the assigned tech that a customer replied; an unassigned ticket's
    reply goes to the new-ticket recipients instead. Never raises."""
    try:
        from .models import SiteSettings
        site = SiteSettings.get()
        if not snippet:
            latest = ticket.replies.filter(
                reply_type='customer_visible', created_by__isnull=True,
            ).order_by('-created_at').first()
            snippet = latest.content if latest else ''
        subject = f'[{ticket.ticket_number}] Customer reply: {ticket.subject}'
        body = (f'The customer replied on a ticket.\n\n'
                f'Ticket:  {ticket.ticket_number}\n'
                f'From:    {_ticket_summary(ticket)}\n'
                f'Subject: {ticket.subject}\n\n'
                f'{(snippet or "")[:500]}')
        tech = ticket.assigned_to
        if tech is not None and tech.email and tech.is_active:
            send_internal_email(site, subject, body, tech.email,
                                ticket=ticket, trigger='notify:reply')
        else:
            for user in _new_ticket_recipients(site):
                send_internal_email(site, subject, body, user.email,
                                    ticket=ticket, trigger='notify:reply')
    except Exception:
        logger.exception('Reply notification failed for %s.',
                         getattr(ticket, 'ticket_number', '?'))


def notify_ticket_assigned(ticket, actor=None):
    """Email the assigned tech that a ticket was handed to them. Silent when
    they assigned it to themselves. Never raises."""
    try:
        from .models import SiteSettings
        tech = ticket.assigned_to
        if tech is None or (actor is not None and tech.pk == actor.pk):
            return
        if not tech.email or not tech.is_active:
            return
        site = SiteSettings.get()
        by = (actor.get_full_name() or actor.username) if actor else 'Murphy\'s Bench'
        subject = f'[{ticket.ticket_number}] Assigned to you: {ticket.subject}'
        body = (f'{by} assigned a ticket to you.\n\n'
                f'Ticket:  {ticket.ticket_number}\n'
                f'From:    {_ticket_summary(ticket)}\n'
                f'Subject: {ticket.subject}')
        send_internal_email(site, subject, body, tech.email,
                            ticket=ticket, trigger='notify:assigned')
    except Exception:
        logger.exception('Assignment notification failed for %s.',
                         getattr(ticket, 'ticket_number', '?'))
