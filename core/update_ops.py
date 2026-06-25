"""In-app admin update operations.

A web request cannot restart its own gunicorn: ``scripts/update.sh`` ends with
``sudo systemctl restart murphys-bench``, which would kill the worker serving the
request. So the UI does NOT run the update in-process. Instead it drops a small
*trigger file* that a systemd ``.path`` unit watches, which launches a detached
one-shot service that runs ``scripts/update.sh`` (see
``deploy/murphys-bench-update.{path,service}`` and ``scripts/run_update.sh``).

This module only does READ-ONLY git inspection and writes the trigger/status
files. No sudo, no shell — git runs via list-form ``subprocess`` args with
``cwd=BASE_DIR``.
"""
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

# A run is "in progress" from the moment the UI queues it until the one-shot
# service writes a terminal state. Both states block a second trigger.
IN_PROGRESS_STATES = {'queued', 'running'}


def _logs_dir() -> Path:
    return Path(settings.BASE_DIR) / 'logs'


def trigger_path() -> Path:
    return _logs_dir() / 'update-trigger'


def status_path() -> Path:
    return _logs_dir() / 'update-status.json'


def _git(*args) -> str:
    """Run a read-only git command in the repo; return stripped stdout, or '' on
    any failure (missing git, not a repo, etc.). Never raises."""
    try:
        out = subprocess.run(
            ['git', *args],
            cwd=str(settings.BASE_DIR),
            capture_output=True, text=True, timeout=30,
        )
        if out.returncode != 0:
            return ''
        return out.stdout.strip()
    except Exception:
        return ''


def _vkey(tag: str):
    """Sort key for a 'vX.Y.Z' tag. Unparseable tags sort lowest."""
    core = tag.lstrip('v').split('-', 1)[0]
    parts = core.split('.')
    try:
        return tuple(int(p) for p in parts)
    except ValueError:
        return ()


def current_version() -> str:
    """Human version of the deployed commit, e.g. 'v0.1.1' or 'v0.1.1-3-gabc123'."""
    return _git('describe', '--tags', '--always') or 'unknown'


def current_tag() -> str:
    """The most recent release tag at or before HEAD ('' if none)."""
    return _git('describe', '--tags', '--abbrev=0', '--match', 'v*')


def available_version() -> str:
    """Newest local release tag ('' if no tags). Reflects the last fetch."""
    out = _git('tag', '-l', 'v*', '--sort=-v:refname')
    return out.splitlines()[0].strip() if out else ''


def fetch_tags() -> bool:
    """Fetch tags from origin so available_version() is fresh. Returns success."""
    # _git returns '' on success too (no stdout); distinguish via returncode.
    try:
        out = subprocess.run(
            ['git', 'fetch', '--tags', '--quiet'],
            cwd=str(settings.BASE_DIR),
            capture_output=True, text=True, timeout=60,
        )
        return out.returncode == 0
    except Exception:
        return False


def is_update_available() -> bool:
    """True when a newer release tag exists than the one currently deployed."""
    latest = available_version()
    if not latest:
        return False
    here = current_tag()
    if not here:
        # No tag at HEAD but tags exist upstream → an update is offerable.
        return True
    return latest != here and _vkey(latest) > _vkey(here)


def read_status() -> dict:
    """Last-known update status. Returns {'state': 'idle'} when absent/corrupt."""
    try:
        data = json.loads(status_path().read_text())
        if isinstance(data, dict) and data.get('state'):
            return data
    except Exception:
        pass
    return {'state': 'idle'}


def is_running() -> bool:
    return read_status().get('state') in IN_PROGRESS_STATES


def request_update() -> bool:
    """Queue an update to the latest release. Writes a status marker and the empty
    trigger file the systemd .path unit watches. Refuses (returns False) if a run
    is already queued or running — prevents a double-trigger."""
    if is_running():
        return False
    logs = _logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    status_path().write_text(json.dumps({
        'state': 'queued',
        'target': available_version() or 'latest',
        'from_version': current_version(),
        'started_at': datetime.now(timezone.utc).isoformat(),
    }))
    # Empty file: "deploy latest tag" (update.sh with no arg). No arbitrary input.
    trigger_path().write_text('')
    return True
