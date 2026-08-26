"""Template tags for the Tabler frame (base_tabler.html and pages built on it).

Icons come from a vendored SUBSET of Tabler Icons (static/vendor/tabler-icons/
<version>/mb-sprite.svg). Add a name to ICONS, run scripts/build_icon_sprite.py,
and the symbol lands in the sprite. A name that is not in ICONS renders nothing
and logs a warning; the test suite asserts every name used in templates is in
the sprite so that cannot reach a box silently.

The legacy {% icon %} tag left with the Tailwind frame in v0.14.0; mb_icons.py
keeps only the status helpers and text filters.
"""
import logging
import re

from django import template
from django.forms import widgets as fw
from django.utils import timezone
from django.templatetags.static import static
from django.utils.safestring import mark_safe

register = template.Library()
log = logging.getLogger('core')

SPRITE_VERSION = '3.46.0'
SPRITE_PATH = f'vendor/tabler-icons/{SPRITE_VERSION}/mb-sprite.svg'

# Every Tabler icon MB uses. Keep sorted; the sprite build reads this.
ICONS = (
    'alert-triangle',
    'arrow-down',
    'arrow-up',
    'bell',
    'book',
    'building',
    'calculator',
    'cash-register',
    'chart-bar',
    'check',
    'chevron-down',
    'chevron-right',
    'chevron-up',
    'circle-check',
    'clipboard-check',
    'clock',
    'contract',
    'device-desktop',
    'device-laptop',
    'device-mobile',
    'device-tablet',
    'download',
    'external-link',
    'eye',
    'file-text',
    'filter',
    'heart-handshake',
    'help',
    'history',
    'home',
    'key',
    'layout-sidebar-left-collapse',
    'layout-sidebar-left-expand',
    'link',
    'lock',
    'logout',
    'mail',
    'map-pin',
    'menu-2',
    'message-circle',
    'moon',
    'notes',
    'paperclip',
    'pencil',
    'phone',
    'player-pause',
    'player-play',
    'plus',
    'printer',
    'qrcode',
    'refresh',
    'send',
    'server',
    'settings',
    'shield-lock',
    'sun',
    'tag',
    'ticket',
    'tool',
    'trash',
    'upload',
    'user',
    'user-plus',
    'users',
    'wallet',
    'wifi',
    'x',
)

_HEX = re.compile(r'^#?([0-9a-fA-F]{6})$')


@register.simple_tag
def ticon(name, extra_class=''):
    """Render a Tabler icon from the vendored sprite: {% ticon 'home' 'icon-sm' %}."""
    if name not in ICONS:
        log.warning('ticon: %r is not in mb_tabler.ICONS; rendering nothing', name)
        return ''
    classes = f'icon {extra_class}'.strip()
    href = f'{static(SPRITE_PATH)}#tabler-{name}'
    return mark_safe(
        f'<svg class="{classes}" width="24" height="24" aria-hidden="true">'
        f'<use href="{href}"/></svg>'
    )


@register.filter
def hex_rgb(value, default='#206bc4'):
    """'#2fb344' -> '47, 179, 68' for Tabler's --tblr-*-rgb variables.

    Tabler derives hover tints and focus rings from the RGB triple, so setting
    --tblr-primary alone leaves those on the stock blue. Bad input falls back
    to the default rather than emitting broken CSS.
    """
    m = _HEX.match(str(value or '')) or _HEX.match(default)
    h = m.group(1)
    return f'{int(h[0:2], 16)}, {int(h[2:4], 16)}, {int(h[4:6], 16)}'


@register.filter
def age_short(dt):
    """Elapsed time as the queue reads it: '15m', '3h', '2d', '5w'. Age is the
    Board's native metric (not a timestamp)."""
    if not dt:
        return ''
    secs = (timezone.now() - dt).total_seconds()
    if secs < 3600:
        return f'{max(int(secs // 60), 0)}m'
    if secs < 86400:
        return f'{int(secs // 3600)}h'
    days = int(secs // 86400)
    if days < 14:
        return f'{days}d'
    return f'{days // 7}w'


@register.filter
def status_word(slug, entity_type):
    """The shop's label for a status slug (from StatusDefinition), for dot + word."""
    from core.templatetags.mb_icons import status_label
    return status_label(slug, entity_type)


@register.simple_tag(takes_context=True)
def url_replace(context, **kwargs):
    """Current query string with some keys replaced: {% url_replace page=2 %}.
    Keeps every other filter (tab, search, status...) so paging and tab links
    never drop the user's filters, the defect the old ticket list shipped with."""
    q = context['request'].GET.copy()
    for k, v in kwargs.items():
        if v in (None, ''):
            q.pop(k, None)
        else:
            q[k] = v
    return '?' + q.urlencode() if q else '?'


@register.filter
def checklist_pair(pre, post):
    """Zip the two check columns for one loop body: [('pre_check', pre), ('post_check', post)]."""
    return [('pre_check', pre), ('post_check', post)]


@register.filter
def tabler_input(bound_field, extra=''):
    """Render a form field with Tabler's input classes, whatever the form's own
    widget attrs say. One filter instead of re-declaring every widget."""
    if not hasattr(bound_field, 'field'):
        # A form variant that dropped this field (restrict_for) resolves to ''.
        return bound_field
    w = bound_field.field.widget
    if isinstance(w, fw.CheckboxInput):
        base = 'form-check-input'
    elif isinstance(w, (fw.RadioSelect, fw.CheckboxSelectMultiple)):
        base = ''
    elif isinstance(w, (fw.Select, fw.SelectMultiple)):
        base = 'form-select'
    else:
        base = 'form-control'
    attrs = {'class': f'{base} {extra}'.strip()} if base or extra else {}
    return bound_field.as_widget(attrs=attrs)


_DEVICE_ICONS = {
    'laptop': 'device-laptop', 'desktop': 'device-desktop', 'server': 'server',
    'mobile': 'device-mobile', 'tablet': 'device-tablet', 'printer': 'printer',
    'wifi': 'wifi', 'question': 'help',
}


@register.filter
def device_icon(heroicon_name):
    """DeviceType.icon holds the old Heroicon name; map it to the Tabler sprite."""
    return _DEVICE_ICONS.get(heroicon_name, 'device-laptop')


@register.filter
def get_item(mapping, key):
    """dict lookup by a variable key (Django templates cannot do d[var])."""
    try:
        return mapping.get(key)
    except AttributeError:
        return None


# ── One-click follow-up buttons ──────────────────────────────────────────────

# Statuses whose work is finished — the moment a follow-up makes sense.
_QUICK_SEND_TICKET_STATUSES = ('resolved', 'closed')
_QUICK_SEND_WO_STATUSES = ('completed', 'closed')


def _quick_send_templates(context):
    """The active quick-send templates, fetched once per request (list rows
    call this per row)."""
    request = context.get('request')
    cached = getattr(request, '_mb_quick_send_templates', None) if request else None
    if cached is None:
        from core.models import EmailTemplate
        cached = list(EmailTemplate.objects.filter(
            trigger__isnull=True, is_active=True, quick_send=True).order_by('name'))
        if request is not None:
            request._mb_quick_send_templates = cached
    return cached


@register.inclusion_tag('core/partials/quick_send_buttons.html', takes_context=True)
def quick_send_buttons(context, ticket=None, work_order=None, compact=False):
    """One button per quick-send template on a finished ticket/work order:
    click sends it to the customer and records the follow-up. A template
    already sent for this record renders as an inert sent-state instead, so a
    stray second click cannot double-email anyone."""
    templates = _quick_send_templates(context)
    record = work_order or ticket
    client = getattr(record, 'client', None) if record else None
    eligible = bool(templates) and client is not None and not client.is_unsorted
    if eligible:
        if work_order is not None:
            eligible = work_order.status in _QUICK_SEND_WO_STATUSES
        else:
            eligible = ticket.status in _QUICK_SEND_TICKET_STATUSES
    sent_ids = set()
    if eligible:
        # follow_ups is prefetched on the list pages; one small query elsewhere.
        sent_ids = {fu.template_id for fu in record.follow_ups.all()
                    if fu.done_at is not None and fu.template_id}
    request = context.get('request')
    return {
        'eligible': eligible, 'templates': templates, 'sent_ids': sent_ids,
        'ticket': ticket if work_order is None else None, 'work_order': work_order,
        'compact': compact,
        'next': request.get_full_path() if request else '',
        'csrf_token': context.get('csrf_token'),
    }
