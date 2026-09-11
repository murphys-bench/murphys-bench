"""Server-side PDF generation.

A single choke point so every customer-facing document (repair reports now,
quotes next) renders the same way. WeasyPrint needs system libraries
(pango / cairo / glib) that are installed out-of-band — apt on the Ubuntu
boxes, Homebrew on macOS dev (see INSTALL.md and scripts/install.sh).

WeasyPrint is imported *lazily* inside render_pdf, never at module load: if the
system libs are missing, importing it raises at import time and would otherwise
take down the whole app on boot. Keeping the import local means only the PDF
path fails (loudly), not the entire process.
"""
import logging

logger = logging.getLogger('core')

# 1x1 transparent PNG — substituted for a referenced-but-missing local asset so
# a missing logo can't take down an otherwise-complete document.
_TRANSPARENT_PNG = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06'
    b'\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05'
    b'\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


class _LocalAssetFetcher:
    """The only ways a PDF can load an asset: a MEDIA_URL or STATIC_URL
    reference, served straight from this box's media/static folders, or an
    inline PNG (a ``data:image/png;base64,`` URL, which is how report charts
    travel from the browser into the PDF). Nothing else is ever fetched, not
    the network, not file:// paths, not any other data: type. WeasyPrint's
    default fetcher can reach local files and any host the server can, and a
    product other shops run cannot lean on "the templates are ours" to stay
    safe: refusing everything else means a future template mistake, an SVG
    logo, or a hand-edited body has no route to the filesystem or the LAN
    through PDF rendering. MB's own PDF templates use only those three forms,
    so this costs no feature.

    A class, not a function: WeasyPrint 70 dropped the callable-fetcher API and
    expects an instance of its URLFetcher (a fetch() returning URLFetcherResponse).
    The base class is imported lazily (same reason as render_pdf), so this is a
    factory that builds the subclass on first use.
    """
    _cls = None

    @classmethod
    def build(cls):
        if cls._cls is None:
            from weasyprint.urls import URLFetcher, URLFetcherResponse

            class LocalAssetFetcher(URLFetcher):
                def fetch(self, url, headers=None):
                    body, mime = _local_asset(url)   # raises for anything not ours
                    return URLFetcherResponse(
                        url, body, {'Content-Type': mime} if mime else None)

            cls._cls = LocalAssetFetcher
        return cls._cls()


class PDFAssetRefused(ValueError):
    """A PDF asset reference that is not a file under MEDIA_ROOT or STATIC_ROOT.
    WeasyPrint catches it, logs a warning, and renders without that asset."""


# Inline images: PNG only, base64 only, and no bigger than a report chart is
# allowed to be (the same cap the reports view applies when the browser posts
# the chart pictures). Nothing is read or fetched to serve one; the bytes are
# already in the URL.
_INLINE_PNG_PREFIX = 'data:image/png;base64,'
_INLINE_PNG_MAX_BYTES = 2 * 1024 * 1024
_PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def _inline_png(url):
    """(file object, 'image/png') for a data:image/png;base64 URL, or raise."""
    import base64
    import binascii
    import io
    if not url.startswith(_INLINE_PNG_PREFIX):
        logger.warning('PDF asset refused, inline data must be a base64 PNG: %s', url[:40])
        raise PDFAssetRefused(url[:40])
    b64 = url[len(_INLINE_PNG_PREFIX):]
    if len(b64) > _INLINE_PNG_MAX_BYTES * 4 // 3 + 4:
        logger.warning('PDF asset refused, inline PNG too large (%d chars)', len(b64))
        raise PDFAssetRefused('inline PNG too large')
    try:
        raw = base64.b64decode(b64, validate=True)
    except (binascii.Error, ValueError):
        logger.warning('PDF asset refused, inline PNG is not valid base64')
        raise PDFAssetRefused('inline PNG bad base64')
    if len(raw) > _INLINE_PNG_MAX_BYTES or not raw.startswith(_PNG_MAGIC):
        logger.warning('PDF asset refused, inline data is not a PNG')
        raise PDFAssetRefused('inline data not a PNG')
    return io.BytesIO(raw), 'image/png'


def _local_asset(url):
    """(file object, mime type) for a MEDIA_URL / STATIC_URL reference or an
    inline base64 PNG.

    Raises PDFAssetRefused for any other URL, and for a reference that escapes
    its folder (``/media/../secrets``): the resolved path must stay inside the
    folder's real location, symlinks followed. A reference inside a folder
    whose file is missing (e.g. a logo path in the DB whose file isn't on this
    box) must NOT crash the whole document: it is replaced by a transparent
    1px PNG and logged loudly.
    """
    import io
    import os
    import mimetypes
    from urllib.parse import urlparse, unquote
    from django.conf import settings

    if url.startswith('data:'):
        return _inline_png(url)
    parsed = urlparse(url)
    path = unquote(parsed.path)
    for prefix, root in (
        (settings.MEDIA_URL, settings.MEDIA_ROOT),
        (settings.STATIC_URL, getattr(settings, 'STATIC_ROOT', None)),
    ):
        if not (prefix and root and path.startswith(prefix)):
            continue
        root_real = os.path.realpath(str(root))
        file_path = os.path.realpath(os.path.join(root_real, path[len(prefix):]))
        if os.path.commonpath([root_real, file_path]) != root_real:
            logger.warning('PDF asset refused, escapes %s: %s', prefix, url)
            raise PDFAssetRefused(url)
        if os.path.isfile(file_path):
            return open(file_path, 'rb'), mimetypes.guess_type(file_path)[0]
        logger.warning('PDF asset not found on disk, skipping: %s', file_path)
        return io.BytesIO(_TRANSPARENT_PNG), 'image/png'
    logger.warning('PDF asset refused, not under MEDIA_URL or STATIC_URL: %s', url)
    raise PDFAssetRefused(url)


# Deliberately a non-file scheme: WeasyPrint reads file:// refs directly with
# pathlib and would bypass our url_fetcher, so a `/media/...` logo would be read
# from the filesystem root and crash if missing. An http(s) base keeps refs in
# URL-space → every asset routes through `_LocalAssetFetcher`, which serves
# media/static from disk, refuses everything else, and gracefully skips a
# referenced-but-missing file.
_PDF_BASE_URL = 'https://murphys-bench.local/'


def render_pdf(html_string, base_url=None):
    """Render an HTML string to PDF bytes.

    Renders with WeasyPrint's default `print` media type, so a template's
    `@media print` rules apply (screen-only controls hide, print footer shows).
    Local media/static assets resolve via `_LocalAssetFetcher`. Raises on
    failure — fail loud; callers decide how to surface it.
    """
    from weasyprint import HTML
    return HTML(
        string=html_string, base_url=base_url or _PDF_BASE_URL,
        url_fetcher=_LocalAssetFetcher.build(),
    ).write_pdf()
