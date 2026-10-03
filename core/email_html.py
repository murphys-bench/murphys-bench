"""HTML email bodies.

The editor (Trix, static/js/mb-email-editor.js) stores template and signature
bodies as a small HTML subset: div/br paragraphs, strong/em/del, links,
lists, and <mb-button> / <mb-button-center> / <mb-button-right> (a link the
email renders as a colored button; the tag carries the button's position).
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
sanitizer so filter arguments like |date:"M j" survive. Only allowlisted
constructs stay live (_token_allowed); guard_plain() applies the same rules
to subject lines. What a variable can REACH is limited separately, by the
plain-values context in email_utils (TEMPLATE_VARIABLES).
"""
import re
import secrets
import unicodedata

from django.template import Context
from django.utils.html import escape

#: The most email text MB will take: a template subject or body, a signature,
#: an edited compose body (Mike, Oct 3 2026: 100 KB). Over it the text is
#: refused, never cut short. bleach's cost grows faster than its input on
#: hostile text (a 2.5 MB POST of '<' ran past 40 s in review), so the check
#: comes before any parsing, at save AND at send.
MAX_EMAIL_TEXT_BYTES = 100 * 1024

#: The most a body may grow to once its variables are filled in, checked
#: before the post-render bleach pass. A customer's reply is escaped text,
#: which bleach handles quickly; this bounds what a template's own markup,
#: repeated by a loop, can hand it.
MAX_RENDERED_BYTES = 4 * MAX_EMAIL_TEXT_BYTES


class EmailTextTooLarge(ValueError):
    """Email text over a size limit. The message is fit to show a person."""


def check_email_text_size(text, what='Email text', limit=MAX_EMAIL_TEXT_BYTES):
    if text and (len(text) > limit or len(text.encode('utf-8')) > limit):
        raise EmailTextTooLarge(f'{what} is over the {limit // 1024} KB limit for email text.')


class Fields(dict):
    """A record's allowed values in a template context. A name it does not
    hold reads as another empty Fields, so a lookup outside the allowed list
    renders blank wherever it appears, a filter argument included (Django
    raises on a missing filter argument rather than blanking it). Prints as
    nothing, so {{ ticket }} alone is blank, not a dump of the fields."""

    def __missing__(self, key):
        return Fields()

    def __str__(self):
        return ''


class Text(str):
    """An allowed value. A dotted name after it ({{ ticket.subject.x }})
    reads as an empty Fields (blank), for the same reason as
    Fields.__missing__. Done through item lookup, which Django tries first,
    and NOT through attributes: round 2 found that answering every attribute
    made Python's own attribute checks lie, and the date filter crashed.
    Cost (Mike, Oct 3 2026): string methods written with a dot
    ({{ ticket.subject.upper }}) are blank too; the |upper filter and the
    rest of the allowed filters work as before."""

    def __getitem__(self, key):
        if isinstance(key, str):
            return Fields()
        return super().__getitem__(key)


class BlankingContext(Context):
    """A Context in which an unknown top-level name is blank, not an error."""

    def __getitem__(self, key):
        try:
            return super().__getitem__(key)
        except KeyError:
            return Fields()

#: What the editor can produce and email can render. Anything else is
#: stripped on save and again at send.
ALLOWED_TAGS = ['div', 'p', 'br', 'strong', 'em', 'del', 'a', 'ul', 'ol',
                'li', 'mb-button', 'mb-button-center', 'mb-button-right']

#: Button markers and the position each one carries. <mb-button> is the
#: original marker (bodies saved before positions existed) and stays left.
BUTTON_TAGS = {'mb-button': 'left', 'mb-button-center': 'center',
               'mb-button-right': 'right'}
_BUTTON_TAG = r'(mb-button(?:-center|-right)?)'
ALLOWED_ATTRS = {'a': ['href']}
ALLOWED_PROTOCOLS = ['http', 'https', 'mailto']

_TOKEN_CLOSERS = {'{{': '}}', '{%': '%}', '{#': '#}'}

#: A token longer than this is refused (shown as literal text). Real ones are
#: a variable and a filter or two; the cap keeps every per-token parse small.
MAX_TOKEN_LENGTH = 300

#: A brace that opens template syntax ({{, {%, {#) outside any token. Left in
#: place it is harmless as output but not as input: Django's lexer scans from
#: every unclosed opener to the end of the line, so a body that is one long run
#: of them takes hours to compile (CodeQL polynomial-redos, Oct 3 2026; a
#: 40k-character run measured 3 seconds, a full-size POST would be hours).
_STRAY_OPENER = re.compile(r'{(?=[{%#])')


def _split_tokens(text):
    """Split text into [text, token, text, token, ..., text], a token being
    {{ ... }}, {% ... %} or a {# comment #} up to its FIRST closer (what a
    non-greedy regex over the three would match). In linear time: once an opener kind has no closer left
    in the text, no later opener of that kind can have one either, so the
    search for it stops instead of rescanning the rest of the text from
    every opener."""
    out, last, i, n = [], 0, 0, len(text)
    exhausted = set()
    while True:
        p = text.find('{', i)
        if p == -1 or p + 1 >= n:
            break
        opener = text[p:p + 2]
        if opener in _TOKEN_CLOSERS and opener not in exhausted:
            q = text.find(_TOKEN_CLOSERS[opener], p + 2)
            if q == -1:
                exhausted.add(opener)
            else:
                out.append(text[last:p])
                out.append(text[p:q + 2])
                last = i = q + 2
                continue
        i = p + 1
    out.append(text[last:])
    return out

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

# What a literal filter argument may not carry: markup characters, or an
# entity-shaped sequence (&lt; / &#60;) that decoding could turn back into
# markup. A bare & is ordinary text ("R&D") and allowed.
_UNSAFE_LITERAL_ARG = re.compile(r'[<>]|&#?\w+;')

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
    the resolved filter FUNCTIONS against the allowlist. Literal string
    arguments may carry no HTML metacharacters: Django marks literals SAFE,
    so |default:"<img …>" would emit raw markup the sanitizer never saw
    (review round 3 finding — the post-render clean in render_body is the
    guarantee; this refusal is the visible-to-the-author layer)."""
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
    for func, args in nodelist[0].filter_expression.filters:
        if func not in allowed_funcs:
            return False
        for is_variable, value in args:
            # < and > are markup; an entity-shaped sequence could rebuild
            # markup through decoding. A bare ampersand ("R&D") is ordinary
            # copy and stays allowed — the post-render bleach pass in
            # render_body is the security boundary (review round 4 note).
            if not is_variable and _UNSAFE_LITERAL_ARG.search(str(value)):
                return False
    return True


def _token_allowed(token):
    if token.startswith('{#'):
        # A one-line comment is one Django hides, at any length (its lexer
        # matches it in one pass). Hidden before this guard existed, so hidden
        # still: a comment can hold an internal note never meant for the
        # customer. One spanning lines is not a comment to Django and always
        # showed as text; refusing it shows the same text.
        return '\n' not in token
    if len(token) > MAX_TOKEN_LENGTH:
        return False
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
    check_email_text_size(html)
    tokens = []

    neutralized_any = False
    # A marker that does not occur in this text, so literal text shaped like
    # a marker is never mistaken for one (review round 1: a fixed marker
    # made 'MBTOKEN0NEKOTBM' typed in a body an IndexError).
    marker = 'MBTOKEN' + secrets.token_hex(8)
    while marker in (html or ''):
        marker = 'MBTOKEN' + secrets.token_hex(8)

    def _stash(token):
        nonlocal neutralized_any
        if not _token_allowed(token):
            neutralized_any = True
            return _neutralize(token)
        tokens.append(token)
        return f'{marker}x{len(tokens) - 1}x'

    parts = _split_tokens(html or '')
    protected = ''.join(_stash(part) if i % 2 else _STRAY_OPENER.sub('&#123;', part)
                        for i, part in enumerate(parts))
    cleaned = _bleach_clean(protected)
    # One pass, not one replace() per token: a body of many small tokens
    # would otherwise cost tokens x length.
    cleaned = re.sub(marker + r'x(\d+)x', lambda m: tokens[int(m.group(1))], cleaned)
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
            cleaned = ''.join(_neutralize(part) if i % 2 and part.startswith('{%') else part
                              for i, part in enumerate(_split_tokens(cleaned)))
    return cleaned


def refused_tokens(text):
    """The tokens in text that _token_allowed refuses, in order, for telling
    the author what will not run."""
    return [part for i, part in enumerate(_split_tokens(text or '')) if i % 2 and not _token_allowed(part)]


def guard_plain(text):
    """The sanitize() token rules for text that is not HTML: a subject line,
    or a legacy plain-text body. Allowed tokens stay live; a refused token,
    and any stray opener, renders as the literal characters typed (through
    {% templatetag %}, since entities would show as entities in plain text).
    Applied at render, so a value saved before this guard existed is covered."""
    check_email_text_size(text)

    def literal(s):
        return ''.join('{% templatetag openbrace %}' if c == '{'
                       else '{% templatetag closebrace %}' if c == '}' else c for c in s)

    def stray(s):
        return _STRAY_OPENER.sub('{% templatetag openbrace %}', s)

    parts = _split_tokens(text or '')
    refused = any(i % 2 and not _token_allowed(part) for i, part in enumerate(parts))
    guarded = ''.join((part if _token_allowed(part) else literal(part)) if i % 2 else stray(part)
                      for i, part in enumerate(parts))
    if refused:
        # Same dangling-partner rule as sanitize(): if what is left no longer
        # compiles, every block tag becomes literal text.
        from django.template import Template, TemplateSyntaxError
        try:
            Template(guarded)
        except TemplateSyntaxError:
            guarded = ''.join(
                (part if part.startswith(('{{', '{#')) and _token_allowed(part) else literal(part)) if i % 2
                else stray(part) for i, part in enumerate(parts))
    return guarded


def _neutralize(token):
    """Visible literal text the template engine can never parse. HTML-escaping
    alone is not enough — {{ x|safe }} contains nothing escape() rewrites and
    would still be live syntax — so the braces themselves become entities."""
    return escape(token).replace('{', '&#123;').replace('}', '&#125;')


def _bleach_clean(html):
    import bleach
    return bleach.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRS,
        protocols=ALLOWED_PROTOCOLS,
        strip=True,
    )


def text_to_html(text):
    """A legacy plain-text body as equivalent HTML: literal text escaped,
    newlines as <br>, template tokens untouched (escaping a filter's quotes
    would break the token at render time). Newlines inside a token stay
    newlines — a <br> inside {% for %} would break the tag."""
    if not text:
        return ''
    parts = _split_tokens(text.replace('\r\n', '\n'))
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
    from django.template import Template
    rendered = Template(sanitize(body_html)).render(BlankingContext(ctx, autoescape=True))
    check_email_text_size(rendered, 'The email, with its values filled in,', MAX_RENDERED_BYTES)
    # The guarantee (review round 3): rendering can INTRODUCE markup the
    # pre-render pass never saw — Django marks literal filter arguments
    # safe, so |default:"<img …>" emits its argument raw. Cleaning the
    # RENDERED output closes that whole class, present and future, not just
    # the filters anyone has thought of. Escaped customer text passes
    # through unchanged (bleach re-serializes entities as entities).
    rendered = _bleach_clean(rendered)
    return rendered.replace('\r\n', '\n').replace('\n', '<br>')


def _build_button(href, label, site, align='left'):
    from .email_utils import _email_header_color, _contrast_text_color
    color = _email_header_color(site)
    text_color = _contrast_text_color(color)
    if align not in ('left', 'center', 'right'):
        align = 'left'
    return (f'<div style="margin:14px 0;text-align:{align};">'
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


# Inside one anchor: text before the first marker (no anchor tags, no marker),
# the marker and its label (no anchor tags; nested formatting is fine), text
# after (no anchor tags). Post-bleach an anchor carries only href.
_NOT_ANCHOR = r'(?:(?!<a\b|</a>).)'
# Only an authored link matches: post-bleach an anchor carries href and nothing
# else, and a button this pass has already built carries target and style, so
# the patterns never re-match their own output.
_ANCHOR_WITH_MARKER = re.compile(
    r'<a href="([^"]*)">((?:(?!<a\b|</a>|<mb-button).)*?)'
    r'<' + _BUTTON_TAG + r'>(' + _NOT_ANCHOR + r'*?)</\3>(' + _NOT_ANCHOR + r'*?)</a>', re.S)
_MARKER_AROUND_ANCHOR = re.compile(
    r'<' + _BUTTON_TAG + r'>\s*<a href="([^"]*)">(' + _NOT_ANCHOR + r'*?)</a>\s*</\1>', re.S)


_TAG = re.compile(r'<(/?)([a-z][a-z0-9-]*)[^>]*>')
_FORMATTING = ('strong', 'em', 'del')


def _open_tags(fragment):
    """Tags opened in ``fragment`` and still open at its end, in order.
    ``br`` is void and skipped. Post-bleach the anchor's content is a well
    formed tree, so this is the marker's ancestor chain inside the anchor:
    usually formatting (strong, em, del), but a hand-written body can put a
    list or a div inside a link and bleach keeps it."""
    stack = []
    for closing, name in _TAG.findall(fragment):
        if name == 'br':
            continue
        if not closing:
            stack.append(name)
        elif name in stack:
            del stack[len(stack) - 1 - stack[::-1].index(name):]
    return stack


def _visible(ch):
    """A character a reader would see: not a space of any kind (Unicode
    category Z), not a format character (Cf: zero-width joiner, soft hyphen,
    word joiner, direction marks), not a control (Cc), and not a combining
    mark on its own (M: a bare variation selector or accent has nothing to
    sit on; with a base character present, the base is what counts)."""
    cat = unicodedata.category(ch)
    return not (cat[0] in 'ZM' or cat in ('Cf', 'Cc'))


def _has_text(fragment):
    """Whether anything a reader would see is in ``fragment``: tags are not
    text, and neither is any whitespace or invisible character, whether raw
    or as an entity (``&nbsp;``, ``&#160;``, ``&ensp;``, ``&#8204;``)."""
    import html as html_mod
    return any(_visible(ch) for ch in html_mod.unescape(_TAG.sub('', fragment)))


_BLOCK_TAG = re.compile(r'</?(?:div|p|ul|ol|li)\b[^>]*>')


def _button_label(label):
    """The text a button carries, or '' when there is none. A block inside the
    label (a hand-written list, say) cannot sit inside a button: its tags
    become spaces and the words stay."""
    label = _drop_empty_pairs(_BLOCK_TAG.sub(' ', label))
    label = re.sub(r'[ \t]{2,}', ' ', label).strip()
    return label if _has_text(label) else ''


def _split_anchor(m, site):
    """One anchor holding a marker becomes: plain link (text before), button,
    plain link (text after). Tags open at the marker are closed before the
    split and reopened after it, so every piece is well formed on its own;
    formatting among them (strong, em, del) is applied inside the button
    label too, a block (a list item, say) is not. A marker with no text in it
    is not a button: the marker is dropped and the link left whole, or dropped
    too when nothing else is in it (a link with nothing to click)."""
    href, pre, tag, label, post = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
    label = _button_label(label)
    if not label:
        rest = _drop_empty_pairs(pre + post)
        return f'<a href="{href}">{rest}</a>' if _has_text(rest) else ''
    open_at_marker = _open_tags(pre)
    close = ''.join(f'</{t}>' for t in reversed(open_at_marker))
    reopen = ''.join(f'<{t}>' for t in open_at_marker)
    formatting = [t for t in open_at_marker if t in _FORMATTING]
    label = ''.join(f'<{t}>' for t in formatting) + label + ''.join(f'</{t}>' for t in reversed(formatting))
    out = ''
    if _has_text(pre):
        out += f'<a href="{href}">{_drop_empty_pairs(pre + close)}</a>'
    out += _build_button(href, label, site, BUTTON_TAGS[tag])
    if _has_text(post):
        out += f'<a href="{href}">{_drop_empty_pairs(reopen + post)}</a>'
    return out


def _wrapped_anchor(m, site):
    """The other nesting, marker around the link: same label rules."""
    tag, href, label = m.group(1), m.group(2), m.group(3)
    text = _button_label(label)
    if not text:
        return ''   # nothing to click: no link at all, not an empty one
    return _build_button(href, text, site, BUTTON_TAGS[tag])


_EMPTY_PAIR = re.compile(r'<([a-z][a-z0-9-]*)>\s*</\1>')


def _drop_empty_pairs(fragment):
    """``<strong></strong>``, ``<li></li>`` and the like, left behind when a
    split closes and reopens tags at a piece's edge; harmless, but no reason
    to send them. A pair that held only whitespace, or an empty block (which
    separated lines), leaves one space so the words on either side stay
    apart. Repeats so an emptied parent goes too."""
    while True:
        cleaned = _EMPTY_PAIR.sub(
            lambda m: ' ' if (re.search(r'>\s+<', m.group(0)) or _BLOCK_TAG.match(m.group(0))) else '',
            fragment)
        if cleaned == fragment:
            return fragment
        fragment = cleaned


def _email_safe(html, site):
    """Inline the styling mail apps actually respect."""
    # Buttons: the marker + link collapse into one styled anchor, whichever
    # way the editor nested them. Neither pattern may run past the end of an
    # anchor: a lazy group that could cross </a> would swallow everything up
    # to the next same-position button in the body and send it as one.
    # A marker on part of a link's text (the editor groups same-link runs
    # under one <a>) splits that link: text before and after stays a plain
    # link, the marked part becomes the button. Repeat until stable so a
    # second marker in the same anchor gets its own pass.
    while True:
        new_html = _ANCHOR_WITH_MARKER.sub(lambda m: _split_anchor(m, site), html)
        if new_html == html:
            break
        html = new_html
    html = _MARKER_AROUND_ANCHOR.sub(lambda m: _wrapped_anchor(m, site), html)
    # A stray marker with no link inside renders as plain text.
    html = re.sub(r'</?' + _BUTTON_TAG + r'>', '', html)
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
        lambda m: (m.group(2) if _label_text(m.group(2)) == m.group(1).strip()
                   else f'{_label_text(m.group(2))}: {m.group(1)}'),
        text, flags=re.S)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</div>\s*<div[^>]*>', '\n', text)
    # A block opening right after text, or after the closing tag of a bold or
    # italic word, starts a new line; otherwise a button that follows the
    # plain part of a split link, or a word the author just made bold, runs
    # straight into the button label.
    text = re.sub(r'(?<=[^\s>])((?:</?(?:strong|em|del)>)*)<(?:div|p|ul|ol)\b[^>]*>', r'\1\n', text)
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


def _label_text(html):
    """A link's text for the plain twin: a line break or a block boundary
    inside it (Enter in the middle of a button label; a hand-written list in
    a link) is a space, not two words glued."""
    text = re.sub(r'<br\s*/?>', ' ', html or '')
    text = _BLOCK_TAG.sub(' ', text)
    return re.sub(r'\s+', ' ', _strip_tags(text)).strip()


def finish_for_email(rendered_html, site):
    """(html for the email, plain-text twin) from a rendered body."""
    email_html = _email_safe(rendered_html, site)
    return email_html, to_plain(email_html)
