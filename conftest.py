"""Pytest fixtures shared across the suite."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_media(settings, tmp_path):
    """Point both media roots at a per-test temp dir so tests that save files
    never write into the repo's media/ or protected/ directories."""
    settings.MEDIA_ROOT = tmp_path / 'media'
    settings.PRIVATE_MEDIA_ROOT = tmp_path / 'protected'
