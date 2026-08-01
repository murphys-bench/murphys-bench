#!/usr/bin/env bash
# One-shot launched by murphys-bench-restore.path when the admin UI drops the
# trigger file logs/restore-trigger (core/restore_ops.request_restore). It fetches
# the chosen archive if it lives on a backup destination, runs scripts/restore.sh,
# and writes logs/restore-status.json for the Settings → Maintenance panel to poll.
#
# Runs as the app user under systemd, outside gunicorn's cgroup, because
# restore.sh STOPS the service — a web request cannot stop the server running it.
# restore.sh stays the single source of restore logic; this wrapper only fetches,
# records status and clears the trigger.
#
# ⚠ The trigger names an archive chosen in a browser. The name is validated in
# core/restore_ops.py before it is written, and AGAIN here, because this is the
# script that turns it into a path. Anything that is not a bare backup filename
# is refused outright rather than sanitized — there is no legitimate request that
# needs a directory component.
set -uo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP"
PY="$APP/venv/bin/python"
STATUS="$APP/logs/restore-status.json"
TRIGGER="$APP/logs/restore-trigger"
RCLONE="$APP/bin/rclone"
RCLONE_CONF="$APP/.rclone.conf"

write_status() {  # state, message
    PY_STATUS="$STATUS" PY_STATE="$1" PY_MSG="${2:-}" "$PY" - <<'PYEOF' 2>/dev/null || true
import json, os, datetime
with open(os.environ['PY_STATUS'], 'w') as f:
    json.dump({'state': os.environ['PY_STATE'],
               'message': os.environ.get('PY_MSG', ''),
               'at': datetime.datetime.now(datetime.timezone.utc).isoformat()}, f)
PYEOF
}

finish() {  # state, message
    write_status "$1" "$2"
    rm -f "$TRIGGER"
    [ "$1" = "succeeded" ] && exit 0 || exit 1
}

[ -f "$TRIGGER" ] || exit 0

SOURCE="$("$PY" -c "import json,sys;print(json.load(open(sys.argv[1])).get('source',''))" "$TRIGGER" 2>/dev/null || true)"
NAME="$("$PY"   -c "import json,sys;print(json.load(open(sys.argv[1])).get('archive',''))" "$TRIGGER" 2>/dev/null || true)"

case "$SOURCE" in
    local|onsite|offsite) ;;
    *) finish failed "unreadable restore request (source: '${SOURCE}')" ;;
esac

# Re-validate independently of the caller. Same pattern as restore_ops.ARCHIVE_RE.
if ! printf '%s' "$NAME" | grep -Eq '^(mb-backup|preupdate)-[0-9]{8}-[0-9]{6}\.tar\.gz$'; then
    finish failed "refused: '${NAME}' is not a backup archive name"
fi

write_status running "preparing"

ARCHIVE="$APP/backups/$NAME"

if [ "$SOURCE" != "local" ]; then
    # Pull it back from the destination it was shipped to. A backup is shipped
    # off-box and the local copy deleted, so on a healthy box this is the normal
    # path, not the exception.
    REMOTE="$("$PY" -c "
import django, os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'murphys_bench.settings')
django.setup()
from core import backup_ops
from core.models import SiteSettings
site = SiteSettings.get()
which = sys.argv[1]
print(backup_ops.onsite_remote_target(site) if which == 'onsite'
      else backup_ops.rclone_remote_target(site))
" "$SOURCE" 2>/dev/null)"

    [ -n "$REMOTE" ] || finish failed "the $SOURCE destination is not configured"
    [ -x "$RCLONE" ] || finish failed "rclone is not installed at bin/rclone"

    write_status running "downloading $NAME from $SOURCE"
    mkdir -p "$APP/backups"
    if ! "$RCLONE" --config "$RCLONE_CONF" copyto "$REMOTE/$NAME" "$ARCHIVE" 2>>"$APP/logs/restore.log"; then
        rm -f "$ARCHIVE"
        finish failed "could not download $NAME from $SOURCE (see logs/restore.log)"
    fi
fi

[ -f "$ARCHIVE" ] || finish failed "archive not found: $NAME"

write_status running "restoring from $NAME"

# RESTORE_YES skips the interactive confirmation; the UI already gated it.
# restore.sh decides for itself whether the bundled .env is needed.
if RESTORE_YES=1 "$APP/scripts/restore.sh" "$ARCHIVE" >>"$APP/logs/restore.log" 2>&1; then
    finish succeeded "restored from $NAME"
else
    finish failed "restore failed — see logs/restore.log. The pre-restore copy is under backups/."
fi
