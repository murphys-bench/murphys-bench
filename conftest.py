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
