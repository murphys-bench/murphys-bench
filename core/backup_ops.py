"""Backup destination configuration + status, for Settings → Maintenance → Backups.

The backup itself runs OUTSIDE Django (``scripts/mb_backup.sh`` fired by
``scripts/backup_scheduler.sh`` on a systemd tick) so it survives even when the
web app is down. This module is the seam between the admin UI and those shell
scripts: on save, Django renders two plain files the *dumb* scripts read —

  * ``backup-config.env``  — sourced by the scripts (which destinations, retention, schedule)
  * ``.rclone.conf``       — rclone remotes: ``[mbbackup]`` (S3, offsite) and/or
                             ``[mbonsite]`` (SMB, a NAS/network share)

— and reads back ``logs/backup-status.json`` (written by the script on each run)
for the read-only status panel. Same "Django writes a file, a shell script reads
it" pattern as ``core/update_ops.py`` / ``HEALTHCHECKS_URL``. No sudo, no shell
from the web process (the one exception is the admin-triggered ``test_destination``
probe, a quick read mirroring the Invoice Ninja "Test Connection" button).

Onsite is reached over SMB via rclone, exactly like offsite is reached via S3 —
MB never mounts anything at the OS level, so there is no sudo/fstab step on any
box, ever.

Secret-bearing files (``.rclone.conf`` holds the S3 secret + the onsite password)
are written 0600.
"""
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

# rclone remote names rendered into .rclone.conf and referenced from the
# manifest. Fixed — there is exactly one configurable onsite + one offsite dest.
RCLONE_REMOTE_NAME = 'mbbackup'          # S3 (offsite)
RCLONE_ONSITE_REMOTE_NAME = 'mbonsite'   # SMB (onsite)

# A run is "in progress" from the moment the UI queues it until the one-shot
# service (or mb_backup.sh) writes a terminal state. Blocks a second trigger.
IN_PROGRESS_STATES = {'queued', 'running'}


def _base_dir() -> Path:
    return Path(settings.BASE_DIR)


def _logs_dir() -> Path:
    return _base_dir() / 'logs'


def rclone_conf_path() -> Path:
    # scripts/mb_backup.sh already expects the rclone config at $APP/.rclone.conf.
    return _base_dir() / '.rclone.conf'


def manifest_path() -> Path:
    return _base_dir() / 'backup-config.env'


def status_path() -> Path:
    return _logs_dir() / 'backup-status.json'


def trigger_path() -> Path:
    return _logs_dir() / 'backup-trigger'


def rclone_bin() -> Path:
    return _base_dir() / 'bin' / 'rclone'


def _write_600(path: Path, text: str) -> None:
    """Write a (possibly secret-bearing) file with owner-only permissions."""
    path.write_text(text)
    try:
        os.chmod(path, 0o600)
    except OSError:
        # Non-POSIX or permission quirk — content is written; perms are best-effort.
        pass


def rclone_remote_target(site) -> str:
    """The ``remote:bucket/path`` string for the S3 case ('' otherwise)."""
    if not site.backup_offsite_enabled or not site.backup_s3_bucket:
        return ''
    target = f'{RCLONE_REMOTE_NAME}:{site.backup_s3_bucket}'
    prefix = (site.backup_s3_path or '').strip('/')
    if prefix:
        target = f'{target}/{prefix}'
    return target


def onsite_remote_target(site) -> str:
    """The ``remote:share/folder`` string for the SMB case ('' otherwise)."""
    if not site.backup_onsite_enabled or not site.backup_onsite_share:
        return ''
    target = f'{RCLONE_ONSITE_REMOTE_NAME}:{site.backup_onsite_share}'
    prefix = (site.backup_onsite_folder or '').strip('/')
    if prefix:
        target = f'{target}/{prefix}'
    return target


class BackupConfigError(Exception):
    """Raised when a destination's config can't be safely rendered — e.g. a
    password was supplied but rclone couldn't obscure it. Callers must not
    write a config file in this state (a blank password would silently
    replace a real one, turning a setup problem into a later auth failure)."""


def _obscure(binary: Path, plaintext: str) -> str:
    """rclone's SMB/FTP-family backends need the password in rclone's own
    reversible obfuscation format in the config file (unlike S3's plain
    secret_access_key). Shell out to the vendored binary to produce it.

    Fails loud (raises) rather than returning '' on failure — a password was
    supplied, so silently writing a blank one is never correct."""
    if not plaintext:
        return ''
    try:
        out = subprocess.run(
            [str(binary), 'obscure', plaintext],
            capture_output=True, text=True, timeout=10,
        )
    except Exception as exc:
        raise BackupConfigError(f'rclone obscure failed to run ({binary}): {exc}') from exc
    if out.returncode != 0:
        raise BackupConfigError(
            f'rclone obscure exited {out.returncode}: {out.stderr.strip() or "no error output"}'
        )
    return out.stdout.strip()


def render_config(site) -> None:
    """Render backup-config.env (always) and .rclone.conf (per enabled remote)
    from settings.

    Called after the Backups settings form saves and on deploy. Safe to call
    repeatedly — it fully rewrites both files from the current SiteSettings state.
    The MB VM is never a destination; these files describe the onsite/offsite
    destinations + retention + schedule that the shell scripts consume.
    """
    _logs_dir().mkdir(parents=True, exist_ok=True)

    stanzas = []
    if site.backup_offsite_enabled:
        stanzas.append(
            f'[{RCLONE_REMOTE_NAME}]\n'
            'type = s3\n'
            'provider = Other\n'
            f'access_key_id = {site.backup_s3_access_key}\n'
            f'secret_access_key = {site.backup_s3_secret_key}\n'
            f'endpoint = {site.backup_s3_endpoint}\n'
            f'region = {site.backup_s3_region}\n'
        )
    offsite_target = rclone_remote_target(site)

    if site.backup_onsite_enabled:
        obscured = _obscure(rclone_bin(), site.backup_onsite_password)
        stanzas.append(
            f'[{RCLONE_ONSITE_REMOTE_NAME}]\n'
            'type = smb\n'
            f'host = {site.backup_onsite_host}\n'
            f'user = {site.backup_onsite_username}\n'
            f'pass = {obscured}\n'
        )
    onsite_target = onsite_remote_target(site)

    if stanzas:
        _write_600(rclone_conf_path(), '\n'.join(stanzas))
    else:
        # Don't leave a stale remote+secret lying around when both are off.
        try:
            rclone_conf_path().unlink()
        except FileNotFoundError:
            pass

    manifest = (
        '# Generated by Murphy\'s Bench (Settings → Maintenance → Backups). Do not edit by hand.\n'
        f'BACKUP_ONSITE_ENABLED="{1 if site.backup_onsite_enabled else 0}"\n'
        f'BACKUP_ONSITE_RCLONE_REMOTE="{onsite_target}"\n'
        f'BACKUP_ONSITE_RETENTION_MODE="{site.backup_onsite_retention_mode}"\n'
        f'BACKUP_ONSITE_RETENTION_VALUE="{int(site.backup_onsite_retention_value)}"\n'
        f'BACKUP_ONSITE_SCHEDULE_DAYS="{site.backup_onsite_schedule_days or "daily"}"\n'
        f'BACKUP_ONSITE_SCHEDULE_TIMES="{site.backup_onsite_schedule_times or "02:00"}"\n'
        f'BACKUP_OFFSITE_ENABLED="{1 if site.backup_offsite_enabled else 0}"\n'
        f'BACKUP_RCLONE_REMOTE="{offsite_target}"\n'
        f'BACKUP_OFFSITE_RETENTION_MODE="{site.backup_offsite_retention_mode}"\n'
        f'BACKUP_OFFSITE_RETENTION_VALUE="{int(site.backup_offsite_retention_value)}"\n'
        f'BACKUP_OFFSITE_SCHEDULE_DAYS="{site.backup_offsite_schedule_days or "daily"}"\n'
        f'BACKUP_OFFSITE_SCHEDULE_TIMES="{site.backup_offsite_schedule_times or "02:00"}"\n'
    )
    # The manifest itself carries no secrets, but keep it owner-only for consistency.
    _write_600(manifest_path(), manifest)


def read_status() -> dict:
    """Last backup run status written by mb_backup.sh. {'state': 'never'} if none."""
    try:
        data = json.loads(status_path().read_text())
        if isinstance(data, dict) and data.get('state'):
            return data
    except Exception:
        pass
    return {'state': 'never'}


def is_running() -> bool:
    return read_status().get('state') in IN_PROGRESS_STATES


def request_backup_now() -> bool:
    """Queue an out-of-band backup run. Writes a 'queued' status marker and the
    empty trigger file the systemd .path unit watches (a web request must not run
    the long backup in-process). Refuses (returns False) if a run is already going."""
    if is_running():
        return False
    logs = _logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    status_path().write_text(json.dumps({
        'state': 'queued',
        'started_at': datetime.now(timezone.utc).isoformat(),
    }))
    trigger_path().write_text('')
    return True


def _rclone_probe(remote: str, label: str):
    """Shared rclone reachability probe (onsite SMB and offsite S3 are both
    plain rclone remotes now). Returns (ok, message)."""
    binary = rclone_bin()
    if not binary.exists():
        return False, (
            'rclone is not installed on the server (expected at bin/rclone) — '
            'the destination is saved but cannot be tested from here.'
        )
    try:
        out = subprocess.run(
            [str(binary), '--config', str(rclone_conf_path()), 'lsd', remote],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as exc:  # subprocess failure, timeout, etc.
        return False, f'Could not run rclone: {exc}'
    if out.returncode == 0:
        return True, f'{label} is reachable.'
    detail = (out.stderr or out.stdout or '').strip().splitlines()
    msg = detail[-1] if detail else f'exit code {out.returncode}'
    return False, f'{label} test failed: {msg}'


def test_destination(site, which):
    """Probe a configured destination. `which` in {'onsite','offsite'}.
    Returns (ok: bool, message: str). Mirrors the Invoice Ninja "Test Connection"
    button — a quick read from the web process. Re-renders config first so the
    probe uses exactly what a real run would.
    """
    if which == 'onsite':
        if not site.backup_onsite_enabled:
            return False, 'Onsite backup is not enabled.'
        if not (site.backup_onsite_host and site.backup_onsite_share and site.backup_onsite_username):
            return False, 'Host, share, and username are all required.'
        render_config(site)
        # Probe the SHARE root, not the folder-inclusive target — the folder
        # doesn't need to pre-exist (rclone creates it on the first real copy).
        remote = f'{RCLONE_ONSITE_REMOTE_NAME}:{site.backup_onsite_share}'
        return _rclone_probe(remote, f'Onsite share "{site.backup_onsite_share}" on {site.backup_onsite_host}')

    if which == 'offsite':
        if not site.backup_offsite_enabled:
            return False, 'Offsite backup is not enabled.'
        if not site.backup_s3_bucket:
            return False, 'No S3 bucket is set.'
        render_config(site)
        remote = f'{RCLONE_REMOTE_NAME}:{site.backup_s3_bucket}'
        return _rclone_probe(remote, f'S3 bucket "{site.backup_s3_bucket}"')

    return False, f'Unknown destination: {which}'
