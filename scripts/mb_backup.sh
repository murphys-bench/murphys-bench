#!/usr/bin/env bash
# Murphy's Bench backup: consistent SQLite snapshot + attachments/media + .env
# -> verified tar.gz, fail-loud, shipped off-box to the configured destination(s),
#    then the local staging copy is deleted (the MB VM is never a destination).
#
# Modes:
#   mb_backup.sh                       full run: build → ship to all ENABLED dests → prune → delete
#   mb_backup.sh --only onsite,offsite ship only to the listed enabled dest(s) (scheduler uses this
#                                      to honour each destination's independent schedule)
#   mb_backup.sh --staging-only OUT    build+verify a LOCAL tarball at OUT and stop.
#                                      No ship/prune/delete, no destination required, no
#                                      status/healthcheck. Used by update.sh as its
#                                      pre-migrate rollback point (a same-box safety net,
#                                      distinct from the off-box backup).
set -euo pipefail

APP=/opt/murphys-bench
DB="$APP/db.sqlite3"
STAGE="$APP/backups"
LOG="$APP/logs/backup.log"
STATUS="$APP/logs/backup-status.json"
TS="$(date +%Y%m%d-%H%M%S)"
SNAP="$STAGE/db-$TS.sqlite3"
ARCHIVE="$STAGE/mb-backup-$TS.tar.gz"

# Arg parsing: --staging-only OUT (local rollback tarball) or --only CSV (dest subset).
STAGING_ONLY=0
ONLY=""   # empty = all enabled destinations
if [ "${1:-}" = "--staging-only" ]; then
    STAGING_ONLY=1
    [ -n "${2:-}" ] || { echo "mb_backup.sh --staging-only requires an output path" >&2; exit 2; }
    ARCHIVE="$2"
elif [ "${1:-}" = "--only" ]; then
    [ -n "${2:-}" ] || { echo "mb_backup.sh --only requires a dest list (onsite,offsite)" >&2; exit 2; }
    ONLY=",${2},"   # e.g. ",onsite,offsite,"
fi
# want DEST → true if this destination should ship this run (enabled + in --only if given)
want(){ [ -z "$ONLY" ] || case "$ONLY" in *",$1,"*) return 0;; *) return 1;; esac; }

# rclone binary + config. The rclone remote + offsite destination are configured
# in the app (Settings → Maintenance → Backups), which renders backup-config.env
# and .rclone.conf. This script is deliberately dumb — it just reads them.
RCLONE_BIN="$APP/bin/rclone"
RCLONE_CONF="$APP/.rclone.conf"

# App-rendered config (Settings → Maintenance → Backups). Absent on an
# un-configured box → sensible defaults (no destination → the run fails loud).
# The MB VM is NEVER a destination — $STAGE is transient staging only, deleted
# after a successful ship to the configured onsite/offsite destination(s).
BACKUP_ONSITE_ENABLED=0
BACKUP_ONSITE_RCLONE_REMOTE=""
BACKUP_ONSITE_RETENTION_MODE="count"
BACKUP_ONSITE_RETENTION_VALUE=14
BACKUP_OFFSITE_ENABLED=0
BACKUP_RCLONE_REMOTE=""
BACKUP_OFFSITE_RETENTION_MODE="age"
BACKUP_OFFSITE_RETENTION_VALUE=30
# shellcheck disable=SC1090
[ -f "$APP/backup-config.env" ] && . "$APP/backup-config.env"

# healthchecks.io dead-man's-switch (set HEALTHCHECKS_URL in .env to enable; unset = no-op)
HC_URL="$(grep -E '^HEALTHCHECKS_URL=' "$APP/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)"
hc_ping(){ [ -n "${HC_URL:-}" ] && curl -fsS -m 10 --retry 3 "${HC_URL}${1:-}" >/dev/null 2>&1 || true; }

# Status panel data (read back by the app's Settings → Maintenance → Backups).
# Written via the venv python for correct JSON escaping (mirrors run_update.sh).
DEST_DESC="(none)"
ARCHIVE_SIZE=""
emit_status(){  # $1=state
    local size="$ARCHIVE_SIZE"
    [ -z "$size" ] && [ -f "$ARCHIVE" ] && size="$(du -h "$ARCHIVE" | cut -f1)"
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
ARCHIVE_SIZE="$(du -h "$ARCHIVE" | cut -f1)"
log "archive ok: $ARCHIVE_SIZE ($ARCHIVE)"

# --staging-only: hand the verified local tarball back to the caller and stop.
# No off-box ship, no prune, no delete, no status/healthcheck.
if [ "$STAGING_ONLY" = "1" ]; then
    log "staging-only: kept local rollback tarball at $ARCHIVE"
    exit 0
fi

# 3) Ship to the configured destination(s). Both onsite (SMB) and offsite (S3)
#    are plain rclone remotes now — MB never mounts anything at the OS level.
#    At least one must be enabled, and the staged archive is deleted only after
#    every enabled destination succeeds. A failure keeps the staged file and
#    fails loud (never lose a run's data).
ship_and_prune(){  # $1=label $2=remote $3=mode $4=value
    local label="$1" remote="$2" mode="$3" value="$4"
    [ -n "$remote" ] || fail "$label enabled but no remote configured"
    [ -x "$RCLONE_BIN" ] || fail "$label enabled but rclone not found at $RCLONE_BIN"
    [ -f "$RCLONE_CONF" ] || fail "$label enabled but $RCLONE_CONF missing"
    "$RCLONE_BIN" --config "$RCLONE_CONF" copy "$ARCHIVE" "$remote" \
        && log "$label ok -> $remote" || fail "$label rclone copy failed"
    if [ "$mode" = "age" ]; then
        "$RCLONE_BIN" --config "$RCLONE_CONF" delete --min-age "${value}d" \
            --include 'mb-backup-*.tar.gz' "$remote" 2>/dev/null || true
    else
        # Keep newest N: list newest-first, delete the rest.
        "$RCLONE_BIN" --config "$RCLONE_CONF" lsf --files-only --include 'mb-backup-*.tar.gz' \
            "$remote" 2>/dev/null | sort -r \
            | tail -n +$((value+1)) \
            | while read -r old; do
                "$RCLONE_BIN" --config "$RCLONE_CONF" deletefile "$remote/$old" 2>/dev/null || true
              done
    fi
}

DESTS=()

if [ "${BACKUP_ONSITE_ENABLED:-0}" = "1" ] && want onsite; then
    ship_and_prune onsite "$BACKUP_ONSITE_RCLONE_REMOTE" "$BACKUP_ONSITE_RETENTION_MODE" "$BACKUP_ONSITE_RETENTION_VALUE"
    DESTS+=("onsite:$BACKUP_ONSITE_RCLONE_REMOTE")
fi

if [ "${BACKUP_OFFSITE_ENABLED:-0}" = "1" ] && want offsite; then
    ship_and_prune offsite "$BACKUP_RCLONE_REMOTE" "$BACKUP_OFFSITE_RETENTION_MODE" "$BACKUP_OFFSITE_RETENTION_VALUE"
    DESTS+=("offsite:$BACKUP_RCLONE_REMOTE")
fi

[ "${#DESTS[@]}" -gt 0 ] || fail "no backup destination configured (enable onsite and/or offsite in Settings → Maintenance)"
DEST_DESC="$(IFS=' + '; echo "${DESTS[*]}")"

# 4) All enabled destinations succeeded — the VM keeps no copy.
rm -f "$ARCHIVE"
log "=== backup done; shipped to: $DEST_DESC (no local copy retained) ==="

emit_status succeeded
hc_ping   # signal success to the healthchecks.io dead-man's-switch
