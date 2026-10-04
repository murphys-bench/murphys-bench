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
import logging
import os
import secrets
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings

logger = logging.getLogger('core')

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


def _discard(tmps) -> list:
    """Remove staged copies, best effort: every one is tried, and one that
    cannot be removed is logged and returned by name, never raised, so a
    cleanup failure cannot replace the error that explains the disk state
    (review round 2 of PR #109). A leftover is owner-only and holds no more
    than the file it was staged for."""
    left = []
    for tmp in tmps:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            logger.error('Could not remove staged backup file %s: %s', tmp.name, type(exc).__name__)
            left.append(tmp.name)
    return left


def _left_note(left) -> str:
    return f'. A temporary copy could not be removed: {", ".join(left)}' if left else ''


def _stage_600(path: Path, text: str) -> Path:
    """Write text to a new file beside path, readable by its owner only from
    creation (0600; umask can only remove bits), and return that file's path.
    Nothing at `path` is touched."""
    tmp = path.with_name(f'.{path.name}.{secrets.token_hex(6)}.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as f:
            os.fchmod(f.fileno(), 0o600)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _discard([tmp])
        raise
    return tmp


def _publish_600(files: dict) -> None:
    """Replace the backup files together, each owner-only ({path: text}; text
    None removes the file). Every replacement is staged and locked before any
    working file is touched, then swapped in with os.replace (which replaces
    a link rather than following it). So a write or lock failure leaves every
    old file exactly as it was, and the error says nothing on disk changed
    (review of PR #109: the first version truncated the working file, then
    failed to lock it, while the page said the last good config was kept).

    A failure while swapping (a rename in the same directory) is the one case
    that can leave the pair half updated; the error says so."""
    staged = {}
    try:
        for path, text in files.items():
            if text is not None:
                staged[path] = _stage_600(path, text)
    except OSError as exc:
        left = _discard(staged.values())
        raise BackupConfigError(
            f'could not write {path.name} with owner-only permissions ({exc}). '
            f'Nothing on disk was changed{_left_note(left)}') from exc
    try:
        for path, text in files.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                os.replace(staged[path], path)
                del staged[path]  # only once it is in place; a failed swap still cleans up
    except OSError as exc:
        left = _discard(staged.values())
        raise BackupConfigError(
            f'could not put {path.name} in place ({exc}). The backup config on disk may be '
            f'partly updated; save the Backups settings again{_left_note(left)}') from exc


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
        raise BackupConfigError(f'rclone obscure failed to run ({binary}): {exc}. '
                                f'Nothing on disk was changed') from exc
    if out.returncode != 0:
        raise BackupConfigError(
            f'rclone obscure exited {out.returncode}: {out.stderr.strip() or "no error output"}. '
            f'Nothing on disk was changed'
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
            # Murphy's Bench is not in the bucket-provisioning business. The
            # Backups settings form asks for a bucket that already exists, and
            # the ship target is always <bucket>/<prefix>. Without this, rclone
            # preflights the bucket, which a least-privilege key is often not
            # allowed to do: a Backblaze S3 key restricted to one bucket lacks
            # listAllBucketNames, so every copy fails on a permission check for
            # something MB never needed. Unconditional rather than a setting
            # (Mike's call, 2026-08-05) because it is an rclone implementation
            # detail, not a decision a shop owner should have to understand.
            # The cost is weaker early detection of a mistyped bucket, which is
            # acceptable: the Test destination button and the real backup copy
            # both exercise the configured target, which is the authority.
            #
            # ⚠ This shipped for six weeks as an UNCOMMITTED one-line edit on
            # the production box (2026-07-23), where a rollback would have
            # discarded it silently and any release touching this file would
            # have failed the update outright.
            'no_check_bucket = true\n'
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

    # With both destinations off, the remote file is removed rather than left
    # holding a stale secret (None below).
    rclone_conf = '\n'.join(stanzas) if stanzas else None

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
    # The manifest itself carries no secrets, but keep it owner-only for
    # consistency. Both replacements are staged before either is published;
    # see _publish_600 for the one case that can leave them mixed.
    _publish_600({rclone_conf_path(): rclone_conf, manifest_path(): manifest})


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
