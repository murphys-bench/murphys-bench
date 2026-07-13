from django.conf import settings
from django.shortcuts import redirect

# URL prefixes that are always exempt from MFA enforcement
_EXEMPT_PREFIXES = (
    '/account/',       # all two_factor auth/setup URLs
    '/accounts/',      # logout
    '/admin/',         # Django admin handles its own auth
    '/static/',
    '/media/',
    '/csp-report/',    # browser-posted CSP violation reports (unauthenticated)
)


def mfa_setup_is_mandatory(user):
    """True when this user has nowhere safe to go but the setup wizard: MFA is
    required site-wide and they have no confirmed device yet. Shared with
    MFASetupView (core/views.py) so the "Cancel" link is hidden in exactly the
    case where clicking it would just bounce them back into the wizard —
    landing on `/` triggers this same middleware to redirect back to
    /account/two_factor/setup/ via a GET, which silently resets the wizard's
    stored secret (upstream two_factor/formtools behavior). Any code already
    scanned into an authenticator app then fails forever, with no explanation."""
    from django_otp import devices_for_user
    from .models import SiteSettings
    if not SiteSettings.get().require_mfa:
        return False
    return not bool(list(devices_for_user(user)))


class MFAEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._needs_mfa_redirect(request):
            if mfa_setup_is_mandatory(request.user):
                return redirect('/account/two_factor/setup/')
            # Has device but session not verified — send back through login
            return redirect('/account/login/')
        return self.get_response(request)

    def _needs_mfa_redirect(self, request):
        if not request.user.is_authenticated:
            return False
        if any(request.path.startswith(p) for p in _EXEMPT_PREFIXES):
            return False
        if request.user.is_verified():
            return False
        from .models import SiteSettings
        return SiteSettings.get().require_mfa


class ContentSecurityPolicyMiddleware:
    """Emit a Content-Security-Policy header built from settings.

    Reads ``CSP_POLICY`` (the directive string) and ``CSP_REPORT_ONLY`` (bool)
    on every response so the test client's ``override_settings`` takes effect:
      - ``CSP_REPORT_ONLY=True``  -> ``Content-Security-Policy-Report-Only``
        (the browser reports violations to ``/csp-report/`` but enforces nothing)
      - ``CSP_REPORT_ONLY=False`` -> ``Content-Security-Policy`` (enforced)

    If ``CSP_POLICY`` is empty the header is omitted entirely, so the policy can
    be defused in production via ``.env`` alone — no code change, instant rollback.
    A ``report-uri`` is appended in both modes so violations keep flowing to the
    logging endpoint for ongoing monitoring even after enforcement.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        policy = (getattr(settings, 'CSP_POLICY', '') or '').strip().rstrip(';')
        already_set = (
            'Content-Security-Policy' in response
            or 'Content-Security-Policy-Report-Only' in response
        )
        if policy and not already_set:
            header = (
                'Content-Security-Policy-Report-Only'
                if getattr(settings, 'CSP_REPORT_ONLY', True)
                else 'Content-Security-Policy'
            )
            response[header] = f'{policy}; report-uri /csp-report/'
        return response
