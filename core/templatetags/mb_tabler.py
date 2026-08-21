"""Template tags for the Tabler frame (base_tabler.html and pages built on it).

Icons come from a vendored SUBSET of Tabler Icons (static/vendor/tabler-icons/
<version>/mb-sprite.svg). Add a name to ICONS, run scripts/build_icon_sprite.py,
and the symbol lands in the sprite. A name that is not in ICONS renders nothing
and logs a warning; the test suite asserts every name used in templates is in
the sprite so that cannot reach a box silently.

The legacy {% icon %} tag in mb_icons.py keeps serving the Tailwind pages until
they are rebuilt; this module is the replacement, not an extension.
"""
import logging
import re

from django import template
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
    'bell',
    'book',
    'building',
    'cash-register',
    'chart-bar',
    'check',
    'chevron-down',
    'clock',
    'contract',
    'device-laptop',
    'file-text',
    'filter',
    'heart-handshake',
    'home',
    'layout-sidebar-left-collapse',
    'layout-sidebar-left-expand',
    'lock',
    'logout',
    'mail',
    'map-pin',
    'menu-2',
    'moon',
    'plus',
    'settings',
    'sun',
    'tag',
    'ticket',
    'tool',
    'user',
    'user-plus',
    'users',
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
