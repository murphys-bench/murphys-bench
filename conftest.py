"""Pytest fixtures shared across the suite."""
import atexit
import os
import shutil
import tempfile

import pytest

# Send the application log somewhere disposable for the duration of this run.
#
# Why: the log path is fixed per install, so a full run appended ~15 records to
# logs/murphys_bench.log, several indistinguishable from genuine failures ("Outbound email
# test failed for host smtp.example.com: 535 ... auth failed"). Running the suite on a real
# box is normal here, and mb-test's log had eight such lines. That file is what the product
# tells an operator to read when outbound email breaks, so test fiction in it is the product
# lying to whoever reads it.
#
# Where this has to happen: pytest-django calls django.setup() from pytest_load_initial_conftests,
# which runs BEFORE this file is imported, and Django applies LOGGING once via dictConfig at
# setup. So neither an env var set here at import time nor a per-test `settings` override can
# move the handler; by then it is already open on the real file. pytest_configure runs after
# setup, so that is where the handler gets rebuilt.
_TEST_LOG_DIR = tempfile.mkdtemp(prefix='mb-test-logs-')
_TEST_LOG_FILE = os.path.join(_TEST_LOG_DIR, 'murphys_bench.log')
atexit.register(shutil.rmtree, _TEST_LOG_DIR, True)


def pytest_configure(config):
    import logging.config

    from django.conf import settings

    # Also exported so anything the suite shells out to (manage.py subprocesses) logs to the
    # temp file too rather than inheriting the install's real path.
    os.environ['MB_LOG_FILE'] = _TEST_LOG_FILE

    settings.LOG_FILE = _TEST_LOG_FILE
    settings.LOGGING['handlers']['file']['filename'] = _TEST_LOG_FILE
    logging.config.dictConfig(settings.LOGGING)


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
