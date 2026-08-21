from .models import SiteSettings
from .templatetags.mb_icons import all_status_defs

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
    """Template context for the chrome: who this user is allowed to see and do.

    ⚠ EVERY value here is computed by CALLING the view helper that guards the
    corresponding server action. Nothing in this file re-states a permission rule,
    because every time it has, the UI and the server have drifted:

      - `is_admin` was hand-written as `is_staff or role_obj.can_manage_settings`,
        which silently dropped the legacy `role == 'admin'` fallback that
        has_perm_flag still honours. A legacy admin got 200 from /settings/ while
        the nav offered them no way in and the user list drew them the non-admin
        backlink. Found by a reviewer, on the fix for the previous instance of
        this same class.
      - `can_manage_users` used has_perm_flag where the server uses _role_flag,
        which differ for a user with no role at all.
      - the eleven ticket/work-order flags were rebuilt as
        `is_admin or _role_flag(user, name)`. That matched every helper's body, so
        nothing was wrong — but it was equivalence rather than delegation, which
        holds only until one helper differs from the others.

    The file already carried a warning about this for the ticket/work-order flags.
    The rule it describes is now applied to all of them: import the helper, call
    it, never paraphrase it.
    """
    user = getattr(request, 'user', None)
    context = {
        'site_settings': SiteSettings.get(),
        # Tabler frame: status colors as CSS variables, from the shop's status table.
        'mb_status_defs': all_status_defs(),
        'is_admin': False,
        'can_view_prospects': False,
        'can_view_estimates': False,
        'can_view_sales': False,
        'can_process_payments': False,
        'can_manage_users': False,
        **{name: False for name in _TICKET_WO_FLAGS},
    }
    if user is None or not user.is_authenticated:
        return context

    from django.core.exceptions import ImproperlyConfigured
    from . import views
    from .views import (
        _is_admin, _can_view_prospects, _can_view_estimates,
        _can_view_sales, _can_process_payments, _can_manage_users,
    )
    context.update({
        'is_admin': _is_admin(user),
        'can_view_prospects': _can_view_prospects(user),
        'can_view_estimates': _can_view_estimates(user),
        'can_view_sales': _can_view_sales(user),
        'can_process_payments': _can_process_payments(user),
        'can_manage_users': _can_manage_users(user),
    })
    # Each of these DOES have its own helper, so call it rather than rebuilding
    # what it happens to do today. This was written as
    # `is_admin or _role_flag(user, name)` — equivalent to every helper's current
    # body, and a reviewer confirmed the results matched. Equivalence is not
    # delegation: the moment one helper grows a condition the others lack (a
    # record-state check is the obvious candidate), the chrome silently stops
    # following it. Resolving by name keeps that impossible.
    for name in _TICKET_WO_FLAGS:
        helper = getattr(views, f'_{name}', None)
        if helper is None:  # fail loud rather than quietly falling back
            raise ImproperlyConfigured(
                f'context processor expects core.views._{name} to exist; a '
                f'template flag with no server helper cannot be kept in step'
            )
        context[name] = bool(helper(user))
    return context


def desk_counts(request):
    """Count-by-status pulse for the Desk nav: follow-ups due today or past."""
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {}
    try:
        from .models import FollowUp
        return {'follow_ups_due': FollowUp.objects.due().count()}
    except Exception:
        return {}
