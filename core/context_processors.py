from .models import SiteSettings


def site_settings(request):
    user = getattr(request, 'user', None)
    is_admin = False
    can_view_prospects = False
    can_view_estimates = False
    can_view_sales = False
    if user is not None and user.is_authenticated:
        is_admin = bool(
            user.is_staff
            or (getattr(user, 'role_obj', None) and user.role_obj.can_manage_settings)
        )
        # Prospects/Estimates/Sales show for everyone unless a role turns the flag off.
        can_view_prospects = is_admin or user.has_perm_flag('can_view_prospects')
        can_view_estimates = is_admin or user.has_perm_flag('can_view_estimates')
        can_view_sales = is_admin or user.has_perm_flag('can_view_sales')
    return {
        'site_settings': SiteSettings.get(),
        'is_admin': is_admin,
        'can_view_prospects': can_view_prospects,
        'can_view_estimates': can_view_estimates,
        'can_view_sales': can_view_sales,
    }
