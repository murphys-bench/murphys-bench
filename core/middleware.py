from django.shortcuts import redirect

# URL prefixes that are always exempt from MFA enforcement
_EXEMPT_PREFIXES = (
    '/account/',       # all two_factor auth/setup URLs
    '/accounts/',      # logout
    '/admin/',         # Django admin handles its own auth
    '/static/',
    '/media/',
)


class MFAEnforcementMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._needs_mfa_redirect(request):
            from django_otp import devices_for_user
            has_device = bool(list(devices_for_user(request.user)))
            if not has_device:
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
