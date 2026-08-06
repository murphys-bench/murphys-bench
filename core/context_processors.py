from .models import SiteSettings

# Role flags exposed to every template so a button the user cannot use is not
# drawn. These mirror the _can_* helpers in views.py one-for-one; the helpers are
# the real gate and these only decide what gets rendered. Keep the two in step —
# a flag added here without its server-side check is a button that 500s or, worse,
# quietly works.
_TICKET_WO_FLAGS = (
    'can_view_all_tickets',
    'can_create_ticket',
    'can_edit_ticket',
    'can_close_tickets',
    'can_delete_ticket',
    'can_assign_ticket',
    'can_reply_internal',
    'can_reply_customer',
    'can_create_workorder',
    'can_edit_workorder',
    'can_close_workorder',
)


def site_settings(request):
    user = getattr(request, 'user', None)
    is_admin = False
    can_view_prospects = False
    can_view_estimates = False
    can_view_sales = False
    can_process_payments = False
    flags = {name: False for name in _TICKET_WO_FLAGS}
    if user is not None and user.is_authenticated:
        is_admin = bool(
            user.is_staff
            or (getattr(user, 'role_obj', None) and user.role_obj.can_manage_settings)
        )
        # Prospects/Estimates/Sales show for everyone unless a role turns the flag off.
        can_view_prospects = is_admin or user.has_perm_flag('can_view_prospects')
        can_view_estimates = is_admin or user.has_perm_flag('can_view_estimates')
        can_view_sales = is_admin or user.has_perm_flag('can_view_sales')
        # Charging money is opt-in, NOT admin-by-default (unlike the flags above) —
        # gated on superuser or the dedicated flag, same bar as MFA reset.
        can_process_payments = user.is_superuser or user.has_perm_flag('can_process_payments')
        # ⚠ Must go through views._role_flag, not has_perm_flag, or the UI and the
        # server disagree for a user with no role_obj: the view would allow the
        # action (field default) while the button that triggers it stayed hidden.
        from .views import _role_flag
        flags = {
            name: is_admin or _role_flag(user, name)
            for name in _TICKET_WO_FLAGS
        }
    return {
        'site_settings': SiteSettings.get(),
        'is_admin': is_admin,
        'can_view_prospects': can_view_prospects,
        'can_view_estimates': can_view_estimates,
        'can_view_sales': can_view_sales,
        'can_process_payments': can_process_payments,
        **flags,
    }
