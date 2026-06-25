#!/usr/bin/env bash
# One-shot launched by murphys-bench-update.path when the admin UI drops the
# trigger file logs/update-trigger. It runs scripts/update.sh (latest release
# tag), records progress to logs/update-status.json for the Settings → Updates
# page to poll, then removes the trigger so the .path unit re-arms for next time.
#
# Runs as the app user (scs-tech) under systemd — OUTSIDE gunicorn's cgroup — so
# update.sh's `sudo systemctl restart murphys-bench` doesn't kill this process.
# update.sh stays pure; all status bookkeeping lives here.
#
# NOTE: not `set -e` — we must capture update.sh's exit code, not abort on it.
set -uo pipefail

APP=/opt/murphys-bench
cd "$APP"
PY="$APP/venv/bin/python"
STATUS="$APP/logs/update-status.json"
LOG="$APP/logs/update.log"
TRIGGER="$APP/logs/update-trigger"

FROM="$(git describe --tags --always 2>/dev/null || echo unknown)"
TO="$(git tag -l 'v*' --sort=-v:refname 2>/dev/null | head -1)"
STARTED="$(date -u +%FT%T%z)"

# emit_status STATE — writes update-status.json (JSON-safe via python, tails the
# log on terminal states for the UI to display).
emit_status() {
    PY_STATE="$1" PY_STATUS="$STATUS" PY_LOG="$LOG" \
    PY_FROM="$FROM" PY_TO="$TO" PY_STARTED="$STARTED" \
    "$PY" - <<'PYEOF'
import json, os, datetime
state = os.environ['PY_STATE']
data = {
    'state': state,
    'from_version': os.environ.get('PY_FROM', ''),
    'target': os.environ.get('PY_TO', ''),
    'started_at': os.environ.get('PY_STARTED', ''),
}
if state in ('succeeded', 'failed'):
    data['finished_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    log = os.environ.get('PY_LOG', '')
    if log and os.path.exists(log):
        with open(log, errors='replace') as f:
            data['log_tail'] = ''.join(f.readlines()[-40:])
with open(os.environ['PY_STATUS'], 'w') as f:
    json.dump(data, f)
PYEOF
}

emit_status running

# Run the real updater (no arg = latest release tag); capture all output to the
# log the UI tails, and update.sh's own exit code for the terminal status.
"$APP/scripts/update.sh" >"$LOG" 2>&1
rc=$?

if [ "$rc" -eq 0 ]; then
    emit_status succeeded
else
    emit_status failed
fi

rm -f "$TRIGGER"
exit "$rc"
