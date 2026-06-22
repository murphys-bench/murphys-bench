#!/usr/bin/env bash
# Murphy's Bench backup: consistent SQLite snapshot + attachments/media + .env
# -> dated tar.gz, fail-loud, local retention, optional offsite to B2 via rclone.
set -euo pipefail

APP=/opt/murphys-bench
DB="$APP/db.sqlite3"
STAGE="$APP/backups"
LOG="$APP/logs/backup.log"
KEEP_LOCAL=14
TS="$(date +%Y%m%d-%H%M%S)"
SNAP="$STAGE/db-$TS.sqlite3"
ARCHIVE="$STAGE/mb-backup-$TS.tar.gz"

# rclone offsite config (dormant until set):
RCLONE_BIN="$APP/bin/rclone"
RCLONE_CONF="$APP/.rclone.conf"
RCLONE_REMOTE="b2:scs-mb-backups"   # e.g. b2:scs-mb-backups  -- filled in once B2 key exists

log(){ echo "$(date "+%F %T") $*" | tee -a "$LOG"; }
fail(){ log "BACKUP FAILED: $*"; exit 1; }

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

# 3) Offsite to B2 (only if configured)
if [ -n "$RCLONE_REMOTE" ] && [ -x "$RCLONE_BIN" ] && [ -f "$RCLONE_CONF" ]; then
    "$RCLONE_BIN" --config "$RCLONE_CONF" copy "$ARCHIVE" "$RCLONE_REMOTE" \
        && log "offsite ok -> $RCLONE_REMOTE" || fail "offsite rclone copy failed"
else
    log "offsite SKIPPED (B2 not configured yet)"
fi

# 4) Local retention
ls -1t "$STAGE"/mb-backup-*.tar.gz 2>/dev/null | tail -n +$((KEEP_LOCAL+1)) | xargs -r rm -f
log "=== backup done; local archives kept: $(ls -1 "$STAGE"/mb-backup-*.tar.gz 2>/dev/null | wc -l) ==="
