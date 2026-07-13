#!/usr/bin/env bash
# One-shot launched by murphys-bench-backup.path when the admin UI drops the
# trigger file logs/backup-trigger (core/backup_ops.request_backup_now). It marks
# the run "running" for the Settings → Maintenance → Backups panel to poll, runs
# scripts/mb_backup.sh (which writes the terminal succeeded/failed status itself),
# then removes the trigger so the .path unit re-arms for next time.
#
# Runs as the app user (scs-tech) under systemd, outside gunicorn's cgroup — a web
# request must not run the long backup in-process. mb_backup.sh stays the single
# source of backup logic; this wrapper only marks "running" and clears the trigger.
set -uo pipefail

APP=/opt/murphys-bench
cd "$APP"
PY="$APP/venv/bin/python"
STATUS="$APP/logs/backup-status.json"
TRIGGER="$APP/logs/backup-trigger"

# Mark running (mb_backup.sh overwrites this with succeeded/failed on completion).
PY_STATUS="$STATUS" "$PY" - <<'PYEOF' 2>/dev/null || true
import json, os, datetime
with open(os.environ['PY_STATUS'], 'w') as f:
    json.dump({'state': 'running',
               'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}, f)
PYEOF

"$APP/scripts/mb_backup.sh"
rc=$?

rm -f "$TRIGGER"
exit "$rc"
