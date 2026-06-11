from .models import SiteSettings


def site_settings(request):
    user = getattr(request, 'user', None)
    is_admin = False
    if user is not None and user.is_authenticated:
        is_admin = bool(
            user.is_staff
            or (getattr(user, 'role_obj', None) and user.role_obj.can_manage_settings)
        )
    return {'site_settings': SiteSettings.get(), 'is_admin': is_admin}
