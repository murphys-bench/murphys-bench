"""Read/trigger helpers for the in-app restore.

Same shape as ``core.backup_ops`` and ``core.update_ops``: the web process may
NOT restore in-process. ``scripts/restore.sh`` stops and starts the service, and
a gunicorn worker cannot stop the server that is running it. So the view writes a
trigger file, a systemd ``.path`` unit notices it and launches a one-shot
(``scripts/run_restore.sh``) outside gunicorn's cgroup, and the page polls a
status file. No new sudo: the service stop/start is already in the passwordless
grant that ``update.sh`` uses, and the web process only writes a file.

``scripts/restore.sh`` stays the single source of restore logic — this module
never restores anything itself.

⚠ SECURITY: the trigger names an archive that a shell script then acts on, so the
name is validated HERE, at the boundary, and again in the wrapper. Only a bare
filename matching the backup naming pattern is ever accepted — no directories,
no traversal, no shell metacharacters. An admin who can reach this view can
already replace the database, but they must not be able to turn a filename into
an arbitrary path.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import backup_ops

# mb_backup.sh writes mb-backup-YYYYMMDD-HHMMSS.tar.gz; update.sh writes
# preupdate-<ts>.tar.gz; restore.sh's own safety copies live in a directory, not
# a tarball, so they are not offered here.
ARCHIVE_RE = re.compile(r'^(mb-backup|preupdate)-\d{8}-\d{6}\.tar\.gz$')

SOURCES = ('local', 'onsite', 'offsite')


def _base_dir() -> Path:
    return backup_ops._base_dir()


def backups_dir() -> Path:
    return _base_dir() / 'backups'


def trigger_path() -> Path:
    return backup_ops._logs_dir() / 'restore-trigger'


def status_path() -> Path:
    return backup_ops._logs_dir() / 'restore-status.json'


def is_valid_archive_name(name: str) -> bool:
    """True only for a bare backup filename. Everything else is rejected."""
    return bool(name) and '/' not in name and ARCHIVE_RE.match(name) is not None


def read_status() -> dict:
    try:
        return json.loads(status_path().read_text())
    except Exception:
        return {}


def is_running() -> bool:
    return read_status().get('state') == 'running' or trigger_path().exists()


def _remote_for(site, source: str) -> str:
    if source == 'onsite':
        return backup_ops.onsite_remote_target(site)
    if source == 'offsite':
        return backup_ops.rclone_remote_target(site)
    return ''


def _list_local() -> list[dict]:
    d = backups_dir()
    if not d.is_dir():
        return []
    out = []
    for p in d.iterdir():
        if p.is_file() and is_valid_archive_name(p.name):
            out.append({'source': 'local', 'name': p.name, 'size': p.stat().st_size})
    return out


def _list_remote(site, source: str) -> tuple[list[dict], str]:
    """(archives, error). An unreachable destination is reported, not raised —
    one dead remote must not hide the archives on the other one."""
    remote = _remote_for(site, source)
    if not remote:
        return [], ''
    binary = backup_ops.rclone_bin()
    if not binary.exists():
        return [], 'rclone is not installed on the server (expected at bin/rclone).'
    try:
        proc = subprocess.run(
            [str(binary), '--config', str(backup_ops.rclone_conf_path()),
             'lsjson', '--files-only', remote],
            capture_output=True, text=True, timeout=60,
        )
    except Exception as exc:
        return [], f'Could not list {source}: {exc}'
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or '').strip().splitlines()
        return [], f'Could not list {source}: {detail[-1] if detail else proc.returncode}'
    try:
        rows = json.loads(proc.stdout or '[]')
    except Exception:
        return [], f'Could not read the {source} listing.'
    out = []
    for row in rows:
        name = row.get('Name') or ''
        if is_valid_archive_name(name):
            out.append({'source': source, 'name': name, 'size': row.get('Size') or 0})
    return out, ''


def list_archives(site) -> tuple[list[dict], list[str]]:
    """Every restorable archive, newest first, plus any per-destination errors.

    Local staging copies are included because `update.sh` leaves a pre-update
    rollback tarball there, but they are usually absent: a real backup is shipped
    off-box and deleted, so the configured destination is where the archives
    actually live. Listing only local files would ship a restore screen that is
    empty on a healthy box.
    """
    archives = _list_local()
    errors = []
    for source in ('onsite', 'offsite'):
        found, err = _list_remote(site, source)
        archives.extend(found)
        if err:
            errors.append(err)
    # Names are timestamped, so a reverse name sort is newest-first.
    archives.sort(key=lambda a: a['name'], reverse=True)
    return archives, errors


def request_restore(source: str, name: str) -> None:
    """Write the trigger the .path unit is watching. Raises ValueError if the
    request is not something we are willing to hand to a shell script."""
    if source not in SOURCES:
        raise ValueError(f'unknown source: {source!r}')
    if not is_valid_archive_name(name):
        raise ValueError(f'not a backup archive name: {name!r}')
    if is_running():
        raise ValueError('a restore is already running')

    status_path().write_text(json.dumps({'state': 'requested',
                                         'source': source, 'archive': name}))
    # Written last: its appearance is what starts the one-shot.
    trigger_path().write_text(json.dumps({'source': source, 'archive': name}))
