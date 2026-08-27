"""HTML email bodies.

The editor (Trix, static/js/mb-email-editor.js) stores template and signature
bodies as a small HTML subset: div/br paragraphs, strong/em/del, links,
lists, and <mb-button> (a link the email renders as a colored button).
Everything here treats that subset as the contract:

  sanitize()        — allowlist what the operator authored; strip the rest.
                      Applied on save AND again at send.
  text_to_html()    — one-time conversion of a legacy plain-text body
                      (migration 0118, and any stray plain body).
  render_body()     — fill Django template variables into an HTML body with
                      escaping ON, so variable content (a customer's reply)
                      is always words, never markup. Variable line breaks
                      become <br>.
  finish_for_email()— turn the rendered HTML into what mail apps respect
                      (styles inlined, buttons built, bare URLs linked) plus
                      the plain-text twin.

Django template tokens ({{ … }} / {% … %}) are protected through the
sanitizer so filter arguments like |date:"M j" survive; only staff author
bodies, so the tokens themselves are trusted the same way template text
always has been.
"""
import re

from django.utils.html import escape

#: What the editor can produce and email can render. Anything else is
#: stripped on save and again at send.
ALLOWED_TAGS = ['div', 'p', 'br', 'strong', 'em', 'del', 'a', 'ul', 'ol',
                'li', 'mb-button']
ALLOWED_ATTRS = {'a': ['href']}
ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']

_TEMPLATE_TOKEN = re.compile(r'({{.*?}}|{%.*?%})', re.S)

# The template constructs a body may use. Anything else — {% autoescape %},
# {% debug %}, {% include %}, a |safe or |safeseq filter, |json_script — is
# neutralized to visible literal text, because each of those can turn
# variable content into live markup or leak state. The escaping guarantee
# must hold against a careless (or compromised) settings account, not just a
# careful one. Review history that shaped this: round 1 found {{ x|safe }};
# a blacklist of that one filter was the fix-the-instance mistake, and round
# 2 rebuilt live markup with {{ x|safeseq|join:"" }} — so variable filters
# are now a POSITIVE allowlist of text-only built-ins, resolved through
# Django's own parser (a regex over the token can be fooled by quoting; the
# parser cannot), and block tags carry no filters at all, because a
# |safeseq inside an allowed {% for %} leaks raw characters one at a time.
_SAFE_BLOCK_TAGS = {'if', 'elif', 'else', 'endif', 'for', 'empty', 'endfor',
                    'comment', 'endcomment', 'now', 'templatetag'}

#: Text-only built-in filters a variable may use. Everything else, including
#: anything registered by an app, is refused — default closed.
ALLOWED_VAR_FILTERS = {
    'date', 'time', 'timesince', 'timeuntil',
    'default', 'default_if_none', 'yesno', 'pluralize',
    'upper', 'lower', 'title', 'capfirst',
    'truncatechars', 'truncatewords', 'wordcount', 'length',
    'floatformat', 'add', 'cut', 'first', 'last', 'join',
    'linebreaksbr',
}


def _variable_token_allowed(token):
    """True when every filter on a {{ … }} token is an allowed text-only
    built-in, decided by compiling the token with Django itself and comparing
    the resolved filter FUNCTIONS against the allowlist."""
    from django.template import Template, TemplateSyntaxError
    from django.template.defaultfilters import register as _builtin
    allowed_funcs = {_builtin.filters[name] for name in ALLOWED_VAR_FILTERS
                     if name in _builtin.filters}
    try:
        nodelist = Template(token).nodelist
    except TemplateSyntaxError:
        return False
    if len(nodelist) != 1 or not hasattr(nodelist[0], 'filter_expression'):
        return False
    return all(func in allowed_funcs
               for func, _args in nodelist[0].filter_expression.filters)


def _token_allowed(token):
    if token.startswith('{{'):
        return _variable_token_allowed(token)
    inner = token[2:-2].strip()
    if '|' in inner:
        # No filters in block tags: {% for c in x|safeseq %} would hand each
        # raw character to an innocent {{ c }}. A legitimate quoted pipe in
        # an argument is rare enough that neutralizing it (visibly) is the
        # right trade.
        return False
    name = inner.split(None, 1)[0] if inner else ''
    return name in _SAFE_BLOCK_TAGS

# A URL sitting in plain text. The text is already escaped, so a literal &
# appears as &amp; (allowed mid-URL for query strings) while &quot; / &lt; /
# &gt; mark where the customer's own markup was escaped — the URL stops
# there, so a link never swallows escaped-markup fragments into its href.
_BARE_URL = re.compile(r"\bhttps?://(?:&amp;|[^\s<>&\"'])+", re.I)

_LINK_STYLE = 'color:#1d6fd8;'


def sanitize(html):
    """Reduce operator-authored HTML to the allowed subset. Template tokens
    are stashed around the sanitizer so it cannot mangle quotes or comparison
    operators inside them; a token outside the allowed construct set (see
    _token_allowed) is escaped into visible literal text instead of stashed,
    so it can never execute."""
    import bleach
    tokens = []

    neutralized_any = False

    def _stash(m):
        nonlocal neutralized_any
        token = m.group(0)
        if not _token_allowed(token):
            neutralized_any = True
            return _neutralize(token)
        tokens.append(token)
        return f'MBTOKEN{len(tokens) - 1}NEKOTBM'

    protected = _TEMPLATE_TOKEN.sub(_stash, html or '')
    cleaned = bleach.clean(
        protected,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )
    for i, token in enumerate(tokens):
        cleaned = cleaned.replace(f'MBTOKEN{i}NEKOTBM', token)
    if neutralized_any:
        # A refused block tag can leave its partner dangling — an {% endfor %}
        # whose {% for %} was neutralized no longer compiles. When anything
        # was refused, prove the remainder still compiles; if not, neutralize
        # every block tag (variables stand alone, block structure does not).
        # An ordinary author typo with nothing refused is untouched and still
        # fails loud at render, exactly as before.
        from django.template import Template, TemplateSyntaxError
        try:
            Template(cleaned)
        except TemplateSyntaxError:
            cleaned = re.sub(r'{%.*?%}', lambda m: _neutralize(m.group(0)),
                             cleaned, flags=re.S)
    return cleaned


def _neutralize(token):
    """Visible literal text the template engine can never parse. HTML-escaping
    alone is not enough — {{ x|safe }} contains nothing escape() rewrites and
    would still be live syntax — so the braces themselves become entities."""
    return escape(token).replace('{', '&#123;').replace('}', '&#125;')


def text_to_html(text):
    """A legacy plain-text body as equivalent HTML: literal text escaped,
    newlines as <br>, template tokens untouched (escaping a filter's quotes
    would break the token at render time). Newlines inside a token stay
    newlines — a <br> inside {% for %} would break the tag."""
    if not text:
        return ''
    parts = _TEMPLATE_TOKEN.split(text.replace('\r\n', '\n'))
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            out.append(part)
        else:
            out.append(escape(part).replace('\n', '<br>'))
    return '<div>' + ''.join(out) + '</div>'


def render_body(body_html, ctx):
    """Fill template variables into an HTML body. Escaping is ON: variable
    content is words, never markup — this is the line that keeps a customer
    reply from smuggling HTML into the shop's branded email. Line breaks
    inside variable values come out as <br> (author-typed line structure is
    already <br>/<div> markup, so any literal newline left after rendering
    came from a variable or is insignificant whitespace)."""
    from django.template import Template, Context
    rendered = Template(sanitize(body_html)).render(Context(ctx, autoescape=True))
    return rendered.replace('\r\n', '\n').replace('\n', '<br>')


def _build_button(href, label, site):
    from .email_utils import _email_header_color, _contrast_text_color
    color = _email_header_color(site)
    text_color = _contrast_text_color(color)
    return (f'<div style="margin:14px 0;">'
            f'<a href="{href}" target="_blank" '
            f'style="display:inline-block;background-color:{color};color:{text_color};'
            f'padding:10px 22px;border-radius:6px;text-decoration:none;font-weight:bold;">'
            f'{label}</a></div>')


def _linkify_outside_anchors(html):
    """Wrap bare URLs in text with <a>, skipping anything already inside an
    anchor (or an attribute — only text between tags is touched)."""
    parts = re.split(r'(<[^>]+>)', html)
    depth = 0
    out = []
    for part in parts:
        if part.startswith('<'):
            tag = part.lower()
            if tag.startswith('<a ') or tag == '<a>':
                depth += 1
            elif tag.startswith('</a'):
                depth = max(0, depth - 1)
            out.append(part)
        elif depth == 0:
            out.append(_BARE_URL.sub(_autolink_one, part))
        else:
            out.append(part)
    return ''.join(out)


def _autolink_one(m):
    url = m.group(0)
    # Sentence punctuation hugging the URL is prose, not address.
    trailing = ''
    while url and url[-1] in '.,;:!?)':
        trailing = url[-1] + trailing
        url = url[:-1]
    return f'<a href="{url}" style="{_LINK_STYLE}">{url}</a>{trailing}'


def _email_safe(html, site):
    """Inline the styling mail apps actually respect."""
    # Buttons: the marker + link collapse into one styled anchor, whichever
    # way the editor nested them.
    html = re.sub(
        r'<a href="([^"]*)"[^>]*>\s*<mb-button>(.*?)</mb-button>\s*</a>',
        lambda m: _build_button(m.group(1), m.group(2), site), html, flags=re.S)
    html = re.sub(
        r'<mb-button>\s*<a href="([^"]*)"[^>]*>(.*?)</a>\s*</mb-button>',
        lambda m: _build_button(m.group(1), m.group(2), site), html, flags=re.S)
    # A stray marker with no link inside renders as plain text.
    html = html.replace('<mb-button>', '').replace('</mb-button>', '')
    # Ordinary links get a visible color even where the app's stylesheet is
    # ignored; ones already styled (the buttons above) are left alone.
    html = re.sub(r'<a href="([^"]*)">', rf'<a href="\1" style="{_LINK_STYLE}">', html)
    html = _linkify_outside_anchors(html)
    # Lists need explicit margins or Outlook squeezes them oddly.
    html = html.replace('<ul>', '<ul style="margin:8px 0;padding-left:24px;">')
    html = html.replace('<ol>', '<ol style="margin:8px 0;padding-left:24px;">')
    return html


def to_plain(html):
    """The plain-text twin of a rendered HTML body: links become
    "label: URL", lists become "- item", paragraphs become lines."""
    import html as html_mod
    text = html or ''
    text = re.sub(
        r'<a [^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        lambda m: (m.group(2) if _strip_tags(m.group(2)).strip() == m.group(1).strip()
                   else f'{_strip_tags(m.group(2)).strip()}: {m.group(1)}'),
        text, flags=re.S)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</div>\s*<div[^>]*>', '\n', text)
    text = re.sub(r'</(p|div|ul|ol)>', '\n', text)
    text = re.sub(r'<li[^>]*>', '- ', text)
    text = re.sub(r'</li>', '\n', text)
    text = _strip_tags(text)
    text = html_mod.unescape(text)
    # Collapse the blank-line noise tag removal leaves behind.
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _strip_tags(html):
    return re.sub(r'<[^>]+>', '', html or '')


def finish_for_email(rendered_html, site):
    """(html for the email, plain-text twin) from a rendered body."""
    email_html = _email_safe(rendered_html, site)
    return email_html, to_plain(email_html)
