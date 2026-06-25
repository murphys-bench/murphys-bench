import logging
from fnmatch import fnmatch

logger = logging.getLogger('core')


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
    return getattr(site, 'email_header_color', '') or site.color_title_bar or '#1f2937'


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
    if client and client.suppress_emails:
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


def send_ticket_email(trigger, ticket, extra_context=None, cc=None, bcc=None):
    """
    Send an automated email for a ticket event.
    Checks all three suppression layers before sending.
    Always writes an EmailSendLog entry for auditing.
    """
    from .models import SiteSettings, EmailTemplate, EmailSignature, SuppressedAddress, EmailSendLog

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

    # Send via SMTP config from SiteSettings
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
            # and `cid:logo` resolves inline — otherwise clients show it as a
            # separate full-size attachment at the bottom of the message.
            msg.mixed_subtype = 'related'
            img = MIMEImage(logo_data, _subtype=logo_mime_type.split('/')[-1])
            img.add_header('Content-ID', '<logo>')
            img.add_header('Content-Disposition', 'inline', filename='logo')
            msg.attach(img)
        sent = msg.send(fail_silently=False)
        status = 'sent' if sent else 'failed'
        reason = '' if sent else 'send_error'
    except Exception:
        logger.exception(
            'SMTP send failed for trigger %s → %s (ticket %s).',
            trigger, to_email, getattr(ticket, 'ticket_number', '?'),
        )
        status = 'failed'
        reason = 'send_error'

    EmailSendLog.objects.create(
        ticket=ticket, to_email=to_email, trigger=trigger,
        status=status, reason=reason,
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
    except Exception:
        logger.exception('SMTP send failed for %s → %s.', trigger, to_email)
        status, reason = 'failed', 'send_error'

    return _log(status, reason)
