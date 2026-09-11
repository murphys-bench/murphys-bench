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
    """Map MEDIA_URL / STATIC_URL references onto local files so PDFs embed
    assets (e.g. the company logo) straight from disk — no HTTP round-trip, works
    on a LAN-only box with no public URL. Anything else falls through to
    WeasyPrint's default fetcher.

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
                    hit = _local_asset(url)
                    if hit is not None:
                        body, mime = hit
                        return URLFetcherResponse(
                            url, body, {'Content-Type': mime} if mime else None)
                    return super().fetch(url, headers)

            cls._cls = LocalAssetFetcher
        return cls._cls()


def _local_asset(url):
    """(file object, mime type) for a MEDIA_URL / STATIC_URL reference, or None
    when the URL is not one of ours (the caller then uses WeasyPrint's default).
    """
    import io
    import os
    import mimetypes
    from urllib.parse import urlparse, unquote
    from django.conf import settings

    path = unquote(urlparse(url).path)
    for prefix, root in (
        (settings.MEDIA_URL, settings.MEDIA_ROOT),
        (settings.STATIC_URL, getattr(settings, 'STATIC_ROOT', None)),
    ):
        if prefix and root and path.startswith(prefix):
            file_path = os.path.join(root, path[len(prefix):])
            if os.path.isfile(file_path):
                return open(file_path, 'rb'), mimetypes.guess_type(file_path)[0]
            # A local asset that's referenced but missing (e.g. a logo path in
            # the DB whose file isn't on this box) must NOT crash the whole
            # document — skip it with a transparent 1px PNG and log loudly.
            logger.warning('PDF asset not found on disk, skipping: %s', file_path)
            return io.BytesIO(_TRANSPARENT_PNG), 'image/png'
    return None


# Deliberately a non-file scheme: WeasyPrint reads file:// refs directly with
# pathlib and would bypass our url_fetcher, so a `/media/...` logo would be read
# from the filesystem root and crash if missing. An http(s) base keeps refs in
# URL-space → every asset routes through `_LocalAssetFetcher`, which serves
# media/static from disk (never touching the network) and gracefully skips a
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
