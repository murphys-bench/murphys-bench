#!/usr/bin/env bash
# Murphy's Bench backup: consistent SQLite snapshot + attachments/media + .env
# -> dated tar.gz, fail-loud, local retention, optional offsite to B2 via rclone.
set -euo pipefail

APP=/opt/murphys-bench
DB="$APP/db.sqlite3"
STAGE="$APP/backups"
LOG="$APP/logs/backup.log"
STATUS="$APP/logs/backup-status.json"
TS="$(date +%Y%m%d-%H%M%S)"
SNAP="$STAGE/db-$TS.sqlite3"
ARCHIVE="$STAGE/mb-backup-$TS.tar.gz"

# rclone binary + config. The rclone remote + offsite destination are configured
# in the app (Settings → Maintenance → Backups), which renders backup-config.env
# and .rclone.conf. This script is deliberately dumb — it just reads them.
RCLONE_BIN="$APP/bin/rclone"
RCLONE_CONF="$APP/.rclone.conf"

# App-rendered config (Settings → Maintenance → Backups). Absent on an
# un-configured box → sensible defaults (local retention only, no offsite).
BACKUP_OFFSITE_TYPE=""
BACKUP_LOCAL_PATH=""
BACKUP_RCLONE_REMOTE=""
BACKUP_RETENTION_LOCAL=14
# shellcheck disable=SC1090
[ -f "$APP/backup-config.env" ] && . "$APP/backup-config.env"
KEEP_LOCAL="${BACKUP_RETENTION_LOCAL:-14}"

# healthchecks.io dead-man's-switch (set HEALTHCHECKS_URL in .env to enable; unset = no-op)
HC_URL="$(grep -E '^HEALTHCHECKS_URL=' "$APP/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)"
hc_ping(){ [ -n "${HC_URL:-}" ] && curl -fsS -m 10 --retry 3 "${HC_URL}${1:-}" >/dev/null 2>&1 || true; }

# Status panel data (read back by the app's Settings → Maintenance → Backups).
# Written via the venv python for correct JSON escaping (mirrors run_update.sh).
DEST_DESC="local only"
emit_status(){  # $1=state
    local size="" ; [ -f "$ARCHIVE" ] && size="$(du -h "$ARCHIVE" | cut -f1)"
    STATE="$1" SIZE="$size" DEST="$DEST_DESC" LOGFILE="$LOG" STATUS_OUT="$STATUS" \
    "$APP/venv/bin/python" - <<'PY' 2>/dev/null || true
import json, os
from datetime import datetime, timezone
state = os.environ["STATE"]
out = {"state": state, "finished_at": datetime.now(timezone.utc).isoformat(),
       "size": os.environ.get("SIZE",""), "destination": os.environ.get("DEST","")}
try:
    with open(os.environ["LOGFILE"]) as f:
        out["log_tail"] = "".join(f.readlines()[-40:])
except Exception:
    pass
with open(os.environ["STATUS_OUT"], "w") as f:
    json.dump(out, f)
PY
}

log(){ echo "$(date "+%F %T") $*" | tee -a "$LOG"; }
fail(){ log "BACKUP FAILED: $*"; emit_status failed; hc_ping /fail; exit 1; }

mkdir -p "$STAGE"
log "=== backup start $TS ==="

# 1) Consistent online snapshot via Python stdlib (safe while app is live)
"$APP/venv/bin/python" - "$DB" "$SNAP" <<"PY" || fail "snapshot step errored"
import sqlite3, sys
src_path, snap_path = sys.argv[1], sys.argv[2]
src = sqlite3.connect(src_path, timeout=30)
dst = sqlite3.connect(snap_path)
with dst:
    src.backup(dst)
ok = src.execute("PRAGMA integrity_check").fetchone()[0]
ntab = dst.execute("SELECT count(*) FROM sqlite_master WHERE type=\"table\"").fetchone()[0]
src.close(); dst.close()
print(f"integrity={ok} tables={ntab}")
if ok != "ok": sys.exit("integrity_check failed: %s" % ok)
if ntab < 50: sys.exit("table count too low: %d" % ntab)
PY

[ -s "$SNAP" ] || fail "snapshot missing/empty"
log "snapshot ok: $(du -h "$SNAP" | cut -f1)"

# 2) Bundle snapshot + attachments + media + .env (turnkey restore)
tar -czf "$ARCHIVE" \
    -C "$APP" .env protected media \
    -C "$STAGE" "db-$TS.sqlite3" || fail "tar failed"
tar -tzf "$ARCHIVE" >/dev/null 2>&1 || fail "archive verify (tar -t) failed"
ASZ=$(stat -c %s "$ARCHIVE")
[ "$ASZ" -gt 102400 ] || fail "archive suspiciously small: $ASZ bytes"
rm -f "$SNAP"
log "archive ok: $(du -h "$ARCHIVE" | cut -f1) ($ARCHIVE)"

# 3) Offsite copy (per Settings → Maintenance → Backups)
case "${BACKUP_OFFSITE_TYPE:-}" in
    s3)
        DEST_DESC="S3: ${BACKUP_RCLONE_REMOTE}"
        [ -n "$BACKUP_RCLONE_REMOTE" ] || fail "S3 offsite selected but no remote configured"
        [ -x "$RCLONE_BIN" ] || fail "S3 offsite selected but rclone not found at $RCLONE_BIN"
        [ -f "$RCLONE_CONF" ] || fail "S3 offsite selected but $RCLONE_CONF missing"
        "$RCLONE_BIN" --config "$RCLONE_CONF" copy "$ARCHIVE" "$BACKUP_RCLONE_REMOTE" \
            && log "offsite ok -> $BACKUP_RCLONE_REMOTE" || fail "offsite rclone copy failed"
        ;;
    local)
        DEST_DESC="local drive: ${BACKUP_LOCAL_PATH}"
        [ -n "$BACKUP_LOCAL_PATH" ] || fail "local offsite selected but no path configured"
        mkdir -p "$BACKUP_LOCAL_PATH" || fail "cannot create local offsite path $BACKUP_LOCAL_PATH"
        cp "$ARCHIVE" "$BACKUP_LOCAL_PATH/" \
            && log "offsite ok -> $BACKUP_LOCAL_PATH" || fail "offsite copy to $BACKUP_LOCAL_PATH failed"
        ;;
    *)
        DEST_DESC="local retention only"
        log "offsite disabled (local retention only)"
        ;;
esac

# 4) Local retention
ls -1t "$STAGE"/mb-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP_LOCAL+1)) | xargs -r rm -f
log "=== backup done; local archives kept: $(ls -1 "$STAGE"/mb-backup-*.tar.gz 2>/dev/null | wc -l) ==="

emit_status succeeded
hc_ping   # signal success to the healthchecks.io dead-man's-switch
