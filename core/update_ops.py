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
import re
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


def incomplete_path() -> Path:
    return _logs_dir() / 'update-incomplete'


# A Murphy's Bench systemd unit as update.sh would name one. Used to decide
# whether a marker claiming missing services is telling the truth.
_UNIT_NAME_RE = re.compile(r'^murphys-bench[-@.a-z0-9]*\.(service|timer|path)$')

# The section headers scripts/update.sh writes. It puts BOTH problems in one
# file, so this must be sanitized per section: discarding the whole marker
# because the services list is garbage would also throw away a real
# "your site is rendering unstyled" warning sitting underneath it.
_MISSING_SERVICES_HEADER = 'These system services are not installed:'
_STATIC_HEADER_PREFIX = 'The web server cannot read this install'
_FIX_PREFIX = 'FIX:'

# There are 16 units in the largest possible install, so a services block much
# longer than that is a parser accident, not a broken box.
_MAX_SERVICE_LINES = 24


def _sanitize_incomplete(text: str) -> str:
    """Drop only the missing-services block, and only when it cannot be believed.

    ``update.sh`` writes up to two independent problems plus a shared FIX line
    into one file (scripts/update.sh:293-311). An all-or-nothing check therefore
    threw away a real "your site renders unstyled" warning whenever the services
    list happened to be garbage — trading noise for silence, which is the worse
    of the two. Caught in review, 2026-08-04.

    So this KEEPS BY DEFAULT. Every line survives unless it belongs to a
    missing-services block that names no plausible unit or is absurdly long. If
    the wording of anything else ever changes, the failure is that the card shows
    too much, never that it silently shows nothing.

    The services block is exactly what update.sh emits: the header, an indented
    run of unit names, then its own unindented prose, ending at a blank line or
    at whichever section comes next.
    """
    lines = text.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() != _MISSING_SERVICES_HEADER:
            out.append(lines[i])
            i += 1
            continue

        # The indented run is the unit list.
        j = i + 1
        named = []
        while j < len(lines) and lines[j].startswith('  ') and lines[j].strip():
            named.append(lines[j])
            j += 1
        # Then this block's own prose, up to a blank line or the next section.
        k = j
        while k < len(lines) and lines[k].strip() and not (
            lines[k].strip().startswith(_STATIC_HEADER_PREFIX)
            or lines[k].strip().startswith(_FIX_PREFIX)
        ):
            k += 1

        believable = (
            bool(named)
            and len(named) <= _MAX_SERVICE_LINES
            and all(_UNIT_NAME_RE.match(ln.strip()) for ln in named)
        )
        if believable:
            out.extend(lines[i:k])
        i = k

    kept = '\n'.join(out).strip()
    # Nothing but advice left means the only problem we had was the one we just
    # decided was not real. Advice for a non-problem is its own false alarm.
    substantive = [
        ln for ln in kept.splitlines()
        if ln.strip() and not ln.strip().startswith(_FIX_PREFIX)
        and not ln.strip().startswith('Run it in a terminal')
        and not ln.strip().startswith('Update button cannot do it')
    ]
    return kept if substantive else ''


def read_incomplete() -> str:
    """What is missing from this install's DEPLOYMENT layer, or '' if it is whole.

    ``scripts/update.sh`` writes this file when it finds units or static
    permissions that an update cannot repair, because the app user deliberately
    holds no privilege to install them, and deletes it once the install is whole.

    It is read on every render rather than carried in the update status, because
    the condition outlives the update that discovered it: the install stays
    incomplete until someone runs the fix, and a status entry would go stale or
    scroll away. The consequence has to keep being visible for as long as it is
    true.

    ⚠ The content is NOT trusted. This file is written by whichever version of
    update.sh happens to be on the box, including versions that predate whatever
    is running now. Verified on a real box 2026-08-04: v0.11.1's update.sh, when
    it reads a newer install_units.sh, mis-parses it and writes 269 lines of
    shell fragments here — which this card then rendered verbatim as "This
    install is incomplete" on a perfectly healthy install. A warning nobody can
    act on is worse than no warning, because it teaches people to ignore the one
    that matters. So a marker that claims services are missing but names no
    plausible unit, or that is absurdly long, is discarded rather than shown.
    """
    try:
        text = incomplete_path().read_text(errors='replace').strip()
    except Exception:
        return ''
    if not text:
        return ''
    return _sanitize_incomplete(text)


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


#: A release tag is exactly vX.Y.Z. Anything else — v0.10.0-rc1, v1.0.0-beta — is
#: NOT a release, and must never be offered as one. Git's version sort ranks a
#: prerelease ABOVE the release it precedes, so an unfiltered "newest tag" would
#: make one pushed RC tag look like the latest release to every install.
_RELEASE_TAG_RE = re.compile(r'^v\d+\.\d+\.\d+$')


def available_version() -> str:
    """Newest local RELEASE tag ('' if none). Reflects the last fetch.

    Prereleases are excluded deliberately — see ``_RELEASE_TAG_RE``. Kept in step
    with ``scripts/update.sh`` and ``scripts/run_update.sh``, which apply the same
    rule; if they disagree, the UI offers one version and the updater installs
    another.
    """
    out = _git('tag', '-l', 'v*', '--sort=-v:refname')
    for line in out.splitlines():
        tag = line.strip()
        if _RELEASE_TAG_RE.match(tag):
            return tag
    return ''


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
    """Last-known update status. Returns {'state': 'idle'} when absent/corrupt.

    Terminal results also carry ``stale``: True once the box has MOVED OFF the
    version that result describes. Nothing but ``run_update.sh`` ever writes this
    file, so a failed run's banner otherwise sits there for good — including on a
    box whose owner then updated by hand and is now, correctly, on the newer
    version. A tester hit exactly that: a red "last update failed, rolled back"
    banner on a box that was running the new release perfectly well. A status
    report that outlives the thing it reports on is worse than none.

    A genuine failure rolls back, so the box ends where it started and
    ``from_version`` still matches — those stay visible. Only a result the
    filesystem has since contradicted is marked stale.

    ``stranded`` is the case that assumption misses: the update failed AND the
    rollback failed too ("MANUAL RECOVERY NEEDED"), leaving the box sitting on
    the TARGET it never verified. That box has moved off ``from_version``, so the
    staleness rule fired and hid the failure banner on the one install that most
    needed to be told — a tester hit exactly that and reported seeing no log.
    A stranded result is never stale: it describes the box precisely.

    Stranded is keyed on ``exit_code``, which ``run_update.sh`` records: 2 is
    ``update.sh``'s manual_abort (the rollback failed), 1 is a failure that
    rolled back. It is NOT inferred from the version landing on the target,
    because that is indistinguishable from a box whose rollback worked and whose
    owner then updated by hand — the exact false alarm the staleness rule exists
    to prevent. Status files written before this release carry no exit code, so
    they fall back to the old staleness rule: precise stranded detection applies
    only to updates run on this release or later.
    """
    try:
        data = json.loads(status_path().read_text())
        if isinstance(data, dict) and data.get('state'):
            if data['state'] in ('succeeded', 'failed'):
                current = current_version()
                data['stranded'] = (data['state'] == 'failed'
                                    and data.get('exit_code') == 2)
                # Where the box should be if this result still describes it: a
                # success left it on the target, a failure rolled it back to
                # where it started.
                expected = (data.get('target') if data['state'] == 'succeeded'
                            else data.get('from_version')) or ''
                data['stale'] = (not data['stranded']
                                 and bool(expected) and expected != current)
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


def changelog_for_version(version: str) -> str:
    """The CHANGELOG.md section for one version tag (its '## vX.Y.Z ...' heading
    through the next '## ' heading), read from that tag's git blob — not the
    working tree — so it's accurate even if a newer, not-yet-deployed commit has
    since changed CHANGELOG.md. Empty string if the tag, file, or section isn't
    found (never raises — this is a read-only convenience view)."""
    if not version:
        return ''
    text = _git('show', f'{version}:CHANGELOG.md')
    if not text:
        return ''
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.startswith('## ') and version in line:
            start = i
            break
    if start is None:
        return ''
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith('## '):
            end = i
            break
    return '\n'.join(lines[start:end]).strip()
