#!/usr/bin/env bash
# One-command restore for Murphy's Bench, from a backup tarball produced by mb_backup.sh.
#
#   scripts/restore.sh /opt/murphys-bench/backups/mb-backup-YYYYMMDD-HHMMSS.tar.gz
#   scripts/restore.sh <tarball> --with-env     # also restore the bundled .env (fresh-box DR)
#
# Restores the SQLite database + protected/ (attachments) + media/. By DEFAULT it does
# NOT touch your live .env, so a same-box rollback keeps the current secrets (incl. the
# FIELD_ENCRYPTION_KEY). Pass --with-env to also restore the bundled .env when rebuilding
# on a fresh machine.
#
# Before swapping anything in, it (1) integrity-checks the restored DB and (2) snapshots
# the CURRENT db/protected/media/.env to backups/pre-restore-<ts>/ — so the restore is
# itself reversible. Run as the app user (scs-tech). The only privileged step is the
# service stop/start (already passwordless for this unit).
#
# Automation hook: set RESTORE_YES=1 to skip the interactive confirmation (used by
# update.sh's rollback path).
set -euo pipefail

APP=/opt/murphys-bench
cd "$APP"

log()  { echo "$(date '+%F %T') restore: $*"; }
fail() { echo "RESTORE FAILED: $*" >&2; exit 1; }

ARCHIVE="${1:-}"
WITH_ENV=0
[ "${2:-}" = "--with-env" ] && WITH_ENV=1

[ -n "$ARCHIVE" ] || fail "usage: scripts/restore.sh <backup.tar.gz> [--with-env]"
[ -f "$ARCHIVE" ]  || fail "no such tarball: $ARCHIVE"
[ -f manage.py ]   || fail "no manage.py in $APP — wrong directory?"

# 1) Verify the archive is readable and locate the DB snapshot inside it.
log "verifying archive: $ARCHIVE"
tar -tzf "$ARCHIVE" >/dev/null 2>&1 || fail "archive is unreadable/corrupt (tar -t failed)"
SNAPNAME="$(tar -tzf "$ARCHIVE" | grep -E '^db-[0-9]+-[0-9]+\.sqlite3$' | head -n1 || true)"
[ -n "$SNAPNAME" ] || fail "no db-*.sqlite3 snapshot found inside the archive"
log "found DB snapshot: $SNAPNAME"

# 2) Extract to a temp staging dir and integrity-check the DB BEFORE touching anything live.
WORK="$(mktemp -d "$APP/backups/restore-stage-XXXXXX")"
trap 'rm -rf "$WORK"' EXIT
tar -xzf "$ARCHIVE" -C "$WORK" || fail "extract failed"
[ -s "$WORK/$SNAPNAME" ] || fail "extracted DB snapshot missing/empty"

"$APP/venv/bin/python" - "$WORK/$SNAPNAME" <<"PY" || fail "restored DB failed integrity check — aborting, nothing changed"
import sqlite3, sys
db = sys.argv[1]
con = sqlite3.connect(db)
ok = con.execute("PRAGMA integrity_check").fetchone()[0]
ntab = con.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
con.close()
print(f"integrity={ok} tables={ntab}")
if ok != "ok": sys.exit("integrity_check failed: %s" % ok)
if ntab < 50: sys.exit("table count too low: %d" % ntab)
PY
log "restored DB verified (integrity ok)"

# 3) Destructive-action gate. Skip with RESTORE_YES=1 (automation / update.sh rollback).
if [ "${RESTORE_YES:-0}" != "1" ]; then
    echo
    echo "This will REPLACE the live database, protected/ and media/ in $APP"
    [ "$WITH_ENV" = 1 ] && echo "and OVERWRITE .env with the bundled copy"
    echo "with the contents of: $ARCHIVE"
    echo "(the current state is saved to backups/pre-restore-<ts>/ first, so this is reversible)"
    read -r -p "Type RESTORE to proceed: " ans
    [ "$ans" = "RESTORE" ] || fail "cancelled by user"
fi

# 4) Stop the app so nothing writes during the swap.
log "stopping murphys-bench..."
sudo systemctl stop murphys-bench || fail "could not stop the service"

# 5) Preserve the CURRENT state so this restore is itself reversible.
TS="$(date +%Y%m%d-%H%M%S)"
SAFE="$APP/backups/pre-restore-$TS"
mkdir -p "$SAFE"
[ -f "$APP/db.sqlite3" ] && cp -p "$APP/db.sqlite3" "$SAFE/"
[ -d "$APP/protected" ]  && cp -a "$APP/protected"  "$SAFE/"
[ -d "$APP/media" ]      && cp -a "$APP/media"      "$SAFE/"
[ -f "$APP/.env" ]       && cp -p "$APP/.env"       "$SAFE/"
log "current state saved to $SAFE"

# 6) Swap in the restored data.
#    DB: drop stale WAL/SHM first so SQLite can't replay them over the restored file.
rm -f "$APP/db.sqlite3-wal" "$APP/db.sqlite3-shm"
cp -p "$WORK/$SNAPNAME" "$APP/db.sqlite3" || fail "DB copy-in failed (current state preserved in $SAFE)"
log "database restored"

#    Attachments + media: replace wholesale.
if [ -d "$WORK/protected" ]; then rm -rf "$APP/protected"; cp -a "$WORK/protected" "$APP/protected"; log "protected/ restored"; fi
if [ -d "$WORK/media" ];     then rm -rf "$APP/media";     cp -a "$WORK/media"     "$APP/media";     log "media/ restored";     fi

#    .env only on explicit request.
if [ "$WITH_ENV" = 1 ]; then
    [ -f "$WORK/.env" ] || fail "--with-env given but no .env in archive (current state preserved in $SAFE)"
    cp -p "$WORK/.env" "$APP/.env"
    chmod 600 "$APP/.env"
    log ".env restored (chmod 600)"
fi

# 7) Restart and health-check (same logic as update.sh: 2xx/3xx/4xx = alive).
log "starting murphys-bench..."
sudo systemctl start murphys-bench || fail "service start failed (current state preserved in $SAFE)"

code=000
for _ in $(seq 1 15); do
    if systemctl is-active --quiet murphys-bench; then
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/ || echo 000)"
        case "$code" in 2*|3*|4*) break ;; esac
    fi
    sleep 1
done
case "$code" in
    2*|3*|4*) log "app healthy (HTTP $code)" ;;
    *)        fail "app not healthy after restore (HTTP $code). Pre-restore state is in $SAFE — to revert: scripts/restore.sh of that copy, or copy it back by hand and restart." ;;
esac

# 8) FIELD_ENCRYPTION_KEY reminder — the one thing a DB restore can't carry on its own.
echo
log "DONE."
echo "  Restored from   : $ARCHIVE"
echo "  Pre-restore copy: $SAFE  (delete once you've confirmed everything is good)"
echo
echo "⚠ ENCRYPTION KEY CHECK"
echo "  Encrypted fields (device/org credentials, the Invoice Ninja token, mailbox"
echo "  passwords) only decrypt if FIELD_ENCRYPTION_KEY in .env MATCHES the key that was"
echo "  in use when this backup was taken."
if [ "$WITH_ENV" = 1 ]; then
    echo "  You restored the bundled .env, so the key matches this backup."
else
    echo "  You kept the live .env. If credentials now look garbled, restore the matching"
    echo "  key from Bitwarden, or re-run with --with-env to use the bundled .env."
fi
