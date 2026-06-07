from fnmatch import fnmatch


def send_ticket_email(trigger, ticket, extra_context=None):
    """
    Send an automated email for a ticket event.
    Checks all three suppression layers before sending.
    Always writes an EmailSendLog entry for auditing.
    """
    from .models import SiteSettings, EmailTemplate, SuppressedAddress, EmailSendLog

    site = SiteSettings.get()

    if not site.email_enabled:
        return

    # Resolve recipient — primary contact first, then any contact, then client email
    contact = ticket.client.contacts.filter(is_primary=True, is_active=True).first()
    if not contact:
        contact = ticket.client.contacts.filter(is_active=True, email__gt='').first()
    to_email = (contact.email if contact else '') or ticket.client.email

    if not to_email:
        EmailSendLog.objects.create(
            ticket=ticket, to_email='', trigger=trigger,
            status='suppressed', reason='no_address',
        )
        return

    # Layer 1: per-client suppress flag
    if ticket.client.suppress_emails:
        EmailSendLog.objects.create(
            ticket=ticket, to_email=to_email, trigger=trigger,
            status='suppressed', reason='client_flag',
        )
        return

    # Layer 2: pattern blocklist
    patterns = [p.strip() for p in site.email_suppression_patterns.splitlines() if p.strip()]
    for pattern in patterns:
        if fnmatch(to_email.lower(), pattern.lower()):
            EmailSendLog.objects.create(
                ticket=ticket, to_email=to_email, trigger=trigger,
                status='suppressed', reason='pattern', detail=pattern,
            )
            return

    # Layer 3: exact address suppression list
    if SuppressedAddress.objects.filter(email__iexact=to_email).exists():
        EmailSendLog.objects.create(
            ticket=ticket, to_email=to_email, trigger=trigger,
            status='suppressed', reason='exact_address',
        )
        return

    # Get active template
    template = EmailTemplate.objects.filter(trigger=trigger, is_active=True).first()
    if not template:
        return  # No template → no email, no log entry (intentionally quiet)

    # Render subject and body as Django templates
    from django.template import Template, Context
    ctx = {
        'ticket': ticket,
        'client': ticket.client,
        'tech_name': ticket.created_by.get_full_name() if ticket.created_by else '',
        'status': ticket.get_status_display(),
        'site_name': "Murphy's Bench",
    }
    if extra_context:
        ctx.update(extra_context)
    context = Context(ctx)

    try:
        subject = Template(template.subject_template).render(context)
        body = Template(template.body_template).render(context)
    except Exception:
        return  # Bad template syntax — fail silently

    # Send via SMTP config from SiteSettings
    from django.core.mail import EmailMessage, get_connection
    from django.conf import settings as django_settings

    connection = get_connection(
        backend='django.core.mail.backends.smtp.EmailBackend',
        host=site.email_host or django_settings.EMAIL_HOST,
        port=site.email_port or django_settings.EMAIL_PORT,
        username=site.email_username or django_settings.EMAIL_HOST_USER,
        password=site.email_password or django_settings.EMAIL_HOST_PASSWORD,
        use_tls=site.email_use_tls,
        fail_silently=True,
    )

    from_email = site.email_from or django_settings.DEFAULT_FROM_EMAIL

    try:
        msg = EmailMessage(
            subject=subject.strip(),
            body=body,
            from_email=from_email,
            to=[to_email],
            connection=connection,
        )
        sent = msg.send(fail_silently=False)
        status = 'sent' if sent else 'failed'
        reason = '' if sent else 'send_error'
    except Exception as e:
        status = 'failed'
        reason = 'send_error'

    EmailSendLog.objects.create(
        ticket=ticket, to_email=to_email, trigger=trigger,
        status=status, reason=reason,
    )
