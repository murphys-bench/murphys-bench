"""Logging handler that turns an unhandled server error into a System Alert ticket.

Attached to the `django.request` logger at ERROR (i.e. 500s) in settings.LOGGING,
filtered to DEBUG=False so it only fires in production. Relies on
create_system_alert's subject-dedupe so a flapping view can't flood the queue.
Django's built-in mail_admins path is unusable here (the box has no system mail
and MB sends only via SiteSettings SMTP), so we write the alert straight into MB.
"""
import logging
import traceback


class SystemAlertHandler(logging.Handler):
    def emit(self, record):
        try:
            from core.system_alerts import create_system_alert
            subject = (record.getMessage() or 'Server error')[:200]
            if record.exc_info:
                body = ''.join(traceback.format_exception(*record.exc_info))
            else:
                body = self.format(record)
            create_system_alert(f'500: {subject}', body)
        except Exception:
            # A logging handler must never raise (and must never recurse into
            # itself). The console/file handlers still captured the original error.
            pass
