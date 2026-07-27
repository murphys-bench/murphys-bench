"""Pytest fixtures shared across the suite."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    """Point both media roots at a per-test temp dir so tests that save files
    never write into the repo's media/ or protected/ directories."""
    settings.MEDIA_ROOT = tmp_path / 'media'
    settings.PRIVATE_MEDIA_ROOT = tmp_path / 'protected'
    # Per-test inbound fetch lock. The real job locks one fixed path per install so two
    # fetches can never overlap; that same fixed path made the dedup test fail whenever
    # ANY other process on the host held it (a second test run, or a live fetch timer).
    settings.INBOUND_FETCH_LOCK_PATH = tmp_path / 'inbound_fetch.lock'


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    """Don't make the test suite depend on `collectstatic` having been run.

    Production runs DEBUG=False, which selects ManifestStaticFilesStorage for
    cache-busting. That backend raises on any {% static %} whose file isn't in the
    built manifest — so on a fresh manual install that hasn't run build_css.sh +
    collectstatic yet, 120 tests failed with "Missing staticfiles manifest entry
    for 'css/app.css'". That reads like broken code; it's a skipped build step.
    (Found on a 26.04 install shakeout, Jul 2026.)

    The manifest path is still covered:
      - broken refs INSIDE collected css/js -> collectstatic post-processing, in CI
      - bad {% static %} refs in TEMPLATES  -> test_static_refs_resolve_under_manifest_storage
    """
    settings.STORAGES = {
        **settings.STORAGES,
        'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
    }
