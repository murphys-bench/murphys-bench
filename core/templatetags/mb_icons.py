import time
from django import template
from django.utils.html import mark_safe

register = template.Library()

# Module-level cache for StatusDefinition lookups — avoids repeated DB hits on list pages.
_sd_cache = {}       # entity_type → {slug: StatusDefinition}
_sd_cache_ts = {}    # entity_type → float timestamp

_SD_CACHE_TTL = 120  # seconds


def _get_status_def(slug, entity_type):
    now = time.time()
    if entity_type not in _sd_cache or now - _sd_cache_ts.get(entity_type, 0) > _SD_CACHE_TTL:
        from core.models import StatusDefinition
        _sd_cache[entity_type] = {s.slug: s for s in StatusDefinition.objects.filter(entity_type=entity_type)}
        _sd_cache_ts[entity_type] = now
    return _sd_cache[entity_type].get(slug)


def _contrasting_color(hex_color):
    try:
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return '#1F2937' if (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.5 else '#F9FAFB'
    except Exception:
        return '#1F2937'


@register.simple_tag
def status_badge(slug, entity_type):
    """Render a colored status badge span from StatusDefinition."""
    sd = _get_status_def(slug, entity_type)
    if sd:
        color = sd.color
        label = sd.label
    else:
        color = '#E5E7EB'
        label = slug.replace('_', ' ').title()
    text = _contrasting_color(color)
    return mark_safe(
        f'<span class="px-2 py-1 text-xs font-semibold rounded-full inline-block whitespace-nowrap" '
        f'style="background-color:{color};color:{text};">{label}</span>'
    )


@register.simple_tag
def status_label(slug, entity_type):
    """Return the plain-text label for a status slug."""
    sd = _get_status_def(slug, entity_type)
    return sd.label if sd else slug.replace('_', ' ').title()


@register.simple_tag
def status_color(slug, entity_type):
    """Return the background hex color for a status slug (for inline style use)."""
    sd = _get_status_def(slug, entity_type)
    return sd.color if sd else '#E5E7EB'


def invalidate_status_cache():
    """Call after StatusDefinition changes to flush the in-process cache."""
    _sd_cache.clear()
    _sd_cache_ts.clear()

# Heroicons v1 outline (24x24) — paths from heroicons.com
ICON_PATHS = {
    'ticket': [
        'M15 5v2m0 4v2m0 4v2M5 5a2 2 0 00-2 2v3a2 2 0 110 4v3a2 2 0 002 2h14a2 2 0 002-2v-3a2 2 0 110-4V7a2 2 0 00-2-2H5z',
    ],
    'clock': [
        'M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z',
    ],
    'check': [
        'M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z',
    ],
    'alert': [
        'M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    ],
    'cog': [
        'M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z',
        'M15 12a3 3 0 11-6 0 3 3 0 016 0z',
    ],
    'list': [
        'M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01',
    ],
    'building': [
        'M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4',
    ],
    'computer': [
        'M9.75 17L9 20l-1 1h8l-1-1-.75-3',
        'M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z',
    ],
    # Device type icons (used in device form icon grid)
    'laptop': [
        'M3 3h18v11H3z',
        'M1 15h22v2H1z',
    ],
    'desktop': [
        'M9.75 17L9 20l-1 1h8l-1-1-.75-3',
        'M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z',
    ],
    'server': [
        'M5 12h14M5 12a2 2 0 01-2-2V6a2 2 0 012-2h14a2 2 0 012 2v4a2 2 0 01-2 2M5 12a2 2 0 00-2 2v4a2 2 0 002 2h14a2 2 0 002-2v-4a2 2 0 00-2-2m-2-4h.01M17 16h.01',
    ],
    'mobile': [
        'M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z',
    ],
    'tablet': [
        'M12 18h.01M7 21h10a2 2 0 002-2V5a2 2 0 00-2-2H7a2 2 0 00-2 2v14a2 2 0 002 2z',
    ],
    'printer': [
        'M17 17h2a2 2 0 002-2v-4a2 2 0 00-2-2H5a2 2 0 00-2 2v4a2 2 0 002 2h2m2 4h6a2 2 0 002-2v-4a2 2 0 00-2-2H9a2 2 0 00-2 2v4a2 2 0 002 2zm8-12V5a2 2 0 00-2-2H9a2 2 0 00-2 2v4h10z',
    ],
    'question': [
        'M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z',
    ],
    'x-mark': [
        'M6 18L18 6M6 6l12 12',
    ],
    'exclamation-triangle': [
        'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z',
    ],
    'lock-closed': [
        'M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z',
    ],
    'user': [
        'M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z',
    ],
    'key': [
        'M15 7a2 2 0 012 2m4 0a6 6 0 01-7.743 5.743L11 17H9v2H7v2H4a1 1 0 01-1-1v-2.586a1 1 0 01.293-.707l5.964-5.964A6 6 0 1121 9z',
    ],
    'document-text': [
        'M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z',
    ],
    'chevron-up': [
        'M5 15l7-7 7 7',
    ],
    'chevron-down': [
        'M19 9l-7 7-7-7',
    ],
    'chevron-right': [
        'M9 5l7 7-7 7',
    ],
    'arrow-down-tray': [
        'M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4',
    ],
    'eye': [
        'M15 12a3 3 0 11-6 0 3 3 0 016 0z',
        'M2.458 12C3.732 7.943 7.523 5 12 5c4.478 0 8.268 2.943 9.542 7-1.274 4.057-5.064 7-9.542 7-4.477 0-8.268-2.943-9.542-7z',
    ],
    # Left-nav sidebar icons
    'home': [
        'M3 12l2-2m0 0l7-7 7 7M5 10v10a1 1 0 001 1h3m10-11l2 2m-2-2v10a1 1 0 01-1 1h-3m-6 0a1 1 0 001-1v-4a1 1 0 011-1h2a1 1 0 011 1v4a1 1 0 001 1m-6 0h6',
    ],
    'map-pin': [
        'M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z',
        'M15 11a3 3 0 11-6 0 3 3 0 016 0z',
    ],
    'chart-bar': [
        'M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z',
    ],
    'funnel': [
        'M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z',
    ],
    'chevron-left': [
        'M15 19l-7-7 7-7',
    ],
    'book-open': [
        'M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253',
    ],
    'shield': [
        'M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z',
    ],
    'logout': [
        'M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1',
    ],
}


@register.simple_tag
def icon(name, size='6', extra_class=''):
    paths = ICON_PATHS.get(name, [])
    if not paths:
        return ''
    path_tags = ''.join(
        f'<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="{p}"/>'
        for p in paths
    )
    classes = f'w-{size} h-{size} {extra_class}'.strip()
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" class="{classes}" '
        f'fill="none" stroke="currentColor" viewBox="0 0 24 24">'
        f'{path_tags}'
        f'</svg>'
    )
    return mark_safe(svg)
