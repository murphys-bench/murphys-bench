"""Status helpers (cached StatusDefinition lookups) and text filters shared by
the frame. The Heroicon {% icon %} tag and the Tailwind status badge left with
the old frame in v0.14.0; icons are {% ticon %} in mb_tabler."""
import re
import time
from django import template
from django.utils.html import escape, mark_safe

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


def all_status_defs():
    """Every StatusDefinition for both entity types, through the same 2-minute cache.

    The Tabler frame emits one CSS variable per status (--mb-status-<entity>-<slug>)
    on every page, so templates carry no hex. Inactive statuses are included on
    purpose: a record can still hold a retired status and must still show its color.
    """
    out = []
    for entity_type in ('ticket', 'workorder'):
        _get_status_def('', entity_type)          # warms/refreshes the cache
        out.extend(_sd_cache[entity_type].values())
    return out



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

@register.filter
def attr(obj, attribute):
    """Return getattr(obj, attribute) — used to read role permission flags dynamically."""
    return getattr(obj, attribute, False)


@register.filter
def getfield(form, field_name):
    """Return a bound form field by name — used to render checkbox fields in a loop."""
    return form[field_name]


# KB articles are staff-authored (can_manage_kb), so this is stored-XSS-by-a-
# trusted-writer rather than an open injection point — but a compromised staff
# account or a pasted-in hostile snippet still shouldn't get raw <script>/onX=
# straight into every reader's browser. Allowlist covers exactly what the
# 'tables'/'fenced_code'/'nl2br'/'sane_lists'/'toc' extensions below emit.
_MARKDOWN_ALLOWED_TAGS = [
    'p', 'br', 'hr', 'strong', 'em', 'del', 'code', 'pre', 'blockquote',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'ul', 'ol', 'li',
    'a', 'img',
    'table', 'thead', 'tbody', 'tr', 'th', 'td',
    'div', 'span',
]
_MARKDOWN_ALLOWED_ATTRS = {
    'a': ['href', 'title'],
    'img': ['src', 'alt', 'title'],
    '*': ['id', 'class'],  # toc anchors + fenced-code language classes
}


@register.filter
def markdownify(text):
    """Render Markdown text to safe HTML — sanitized through a bleach
    allowlist so a stored article can't carry raw <script>/onX= handlers."""
    import bleach
    import markdown as md
    html = md.markdown(
        text or '',
        extensions=['tables', 'fenced_code', 'nl2br', 'sane_lists', 'toc'],
    )
    clean_html = bleach.clean(
        html,
        tags=_MARKDOWN_ALLOWED_TAGS,
        attributes=_MARKDOWN_ALLOWED_ATTRS,
        protocols=['http', 'https', 'mailto'],
        strip=True,
    )
    return mark_safe(clean_html)


# Boundaries that mark where an email reply stops and the quoted history begins.
_QUOTE_BOUNDARY_PATTERNS = [
    re.compile(r'(?m)^On\b.{5,250}?\bwrote:\s*$'),          # "On <date>, <who> wrote:"
    re.compile(r'(?m)^>'),                                   # a quoted line
    re.compile(r'(?m)^-{2,}\s*Original Message\s*-{2,}', re.I),
    re.compile(r'(?m)^-{2,}\s*Forwarded message\s*-{2,}', re.I),
    re.compile(r'(?m)^_{5,}\s*$'),                           # Outlook underscore divider
]


def split_reply_quote(content):
    """Split a reply body into (new_text, quoted_text).

    The new text is what the person actually wrote this time; the quoted text is
    the email history they replied on top of. quoted_text is '' when no quote is
    detected. Pure string logic so it can be unit-tested without rendering.
    """
    text = (content or '').replace('\r\n', '\n').replace('\r', '\n')
    earliest = None
    for pat in _QUOTE_BOUNDARY_PATTERNS:
        m = pat.search(text)
        if m and (earliest is None or m.start() < earliest):
            earliest = m.start()
    if earliest is None:
        return text.strip(), ''
    return text[:earliest].rstrip(), text[earliest:].strip()


@register.filter
def reply_body(content):
    """Render a ticket reply: new text shown plainly (newlines preserved), the
    quoted email history folded into a collapsible, greyed blockquote — like a
    standard email client. Content is escaped before any markup is added.
    """
    new_text, quoted = split_reply_quote(content)
    html = escape(new_text).replace('\n', '<br>')
    if quoted:
        # Strip leading "> " markers per line for a clean blockquote.
        cleaned = '\n'.join(re.sub(r'^>\s?', '', ln) for ln in quoted.split('\n'))
        html += (
            '<details class="mt-2">'
            '<summary class="cursor-pointer text-xs text-gray-400 hover:text-gray-600 select-none">'
            '&#8943; Show quoted text</summary>'
            '<div class="mt-1 pl-3 border-l-2 border-gray-300 text-gray-500 text-xs whitespace-pre-line">'
            + escape(cleaned).replace('\n', '<br>')
            + '</div></details>'
        )
    return mark_safe(html)
