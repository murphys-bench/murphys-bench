#!/usr/bin/env bash
# One-command, fail-loud update for Murphy's Bench — with AUTO-ROLLBACK.
#
#   scripts/update.sh              # deploy the latest RELEASE TAG (vX.Y.Z)
#   scripts/update.sh v0.3.0       # deploy a specific tag
#   scripts/update.sh main         # deploy latest on a branch (staging/testing)
#   scripts/update.sh --no-rollback <ref>   # leave a failed update in place (debugging)
#
# It ALWAYS backs up first (snapshot-before-migrate). If anything after that goes
# wrong — bad deps, failed migration, broken restart — it AUTOMATICALLY rolls the
# code AND the database back to where it started and verifies the app is healthy
# again. Run as the app user (scs-tech); the only privileged step is the service
# restart (already passwordless for this unit).
set -euo pipefail

APP=/opt/murphys-bench
VENV="$APP/venv/bin"
cd "$APP"

log()  { echo "$(date '+%F %T') update: $*"; }
fail() { echo "UPDATE FAILED: $*" >&2; exit 1; }

# manual_abort: rollback itself failed — the worst case. Tell the human exactly
# how to recover by hand, with the backup that can restore the DB.
manual_abort() {
    echo "ROLLBACK FAILED: $1" >&2
    echo "  ⚠ MANUAL RECOVERY NEEDED — the app may be down." >&2
    echo "  Pre-update backup: ${BACKUP_TARBALL:-<none>}" >&2
    echo "  Recover by hand:" >&2
    echo "    cd $APP && git checkout --force $PREV && $VENV/pip install -r requirements.txt \\" >&2
    echo "      && scripts/build_css.sh && $VENV/python manage.py collectstatic --noinput \\" >&2
    echo "      && RESTORE_YES=1 scripts/restore.sh $BACKUP_TARBALL" >&2
    exit 2
}

# rollback: revert code + DB to the pre-update state and confirm health.
rollback() {
    local why="$1"
    if [ "$NO_ROLLBACK" = 1 ]; then
        echo "UPDATE FAILED: $why" >&2
        echo "  Auto-rollback DISABLED (--no-rollback) — the box may be in a broken state." >&2
        echo "  Pre-update backup: $BACKUP_TARBALL ; previous code: $PREV ($PREV_VER)" >&2
        echo "  Recover: cd $APP && git checkout --force $PREV && $VENV/pip install -r requirements.txt \\" >&2
        echo "    && scripts/build_css.sh && $VENV/python manage.py collectstatic --noinput \\" >&2
        echo "    && RESTORE_YES=1 scripts/restore.sh $BACKUP_TARBALL" >&2
        exit 1
    fi
    log "UPDATE FAILED ($why) — AUTO-ROLLING BACK to $PREV ($PREV_VER)..."
    git checkout --force --quiet "$PREV"                          || manual_abort "git checkout $PREV"
    "$VENV/pip" install -q -r requirements.txt                    || manual_abort "pip install"
    "$APP/scripts/build_css.sh"                                   || manual_abort "build_css"
    "$VENV/python" manage.py collectstatic --noinput >/dev/null   || manual_abort "collectstatic"
    # restore.sh restores the DB (+ protected/ + media/), restarts, and health-checks.
    RESTORE_YES=1 "$APP/scripts/restore.sh" "$BACKUP_TARBALL"     || manual_abort "DB restore"
    log "ROLLED BACK to $PREV ($PREV_VER) and verified healthy. Original failure: $why"
    exit 1
}

[ -f manage.py ] || fail "no manage.py in $APP — wrong directory?"
command -v git >/dev/null || fail "git not installed"

# Parse args: one optional ref + an optional --no-rollback flag, any order.
REF=""
NO_ROLLBACK=0
for a in "$@"; do
    case "$a" in
        --no-rollback) NO_ROLLBACK=1 ;;
        -*) fail "unknown flag '$a'" ;;
        *) if [ -z "$REF" ]; then REF="$a"; else fail "unexpected extra argument '$a'"; fi ;;
    esac
done

# 1) Back up FIRST (snapshot-before-migrate) and capture the exact tarball as the
#    rollback point — BEFORE anything is touched. If this fails, nothing changed.
log "backing up before update (snapshot-before-migrate)..."
"$APP/scripts/mb_backup.sh" || fail "pre-update backup failed — aborting, nothing was changed"
BACKUP_TARBALL="$(ls -1t "$APP/backups"/mb-backup-*.tar.gz 2>/dev/null | head -1)"
[ -f "$BACKUP_TARBALL" ] || fail "could not locate the pre-update backup tarball — aborting before any change"
log "rollback point: $BACKUP_TARBALL"

# 2) Remember where we are (commit + human version), for rollback + reporting.
PREV="$(git rev-parse --short HEAD)"
PREV_VER="$(git describe --tags --always 2>/dev/null || echo "$PREV")"

# 3) Fetch and resolve the target: no arg = latest release tag; arg = that ref.
git fetch --all --tags --quiet || fail "git fetch failed"
if [ -n "$REF" ]; then
    TARGET="$REF"
else
    TARGET="$(git tag -l 'v*' | sort -V | tail -1)"
    [ -n "$TARGET" ] || fail "no release tags exist yet. Create one with scripts/release.sh \
(on your dev machine, after CI is green), or pass an explicit ref to deploy untagged \
code, e.g.: scripts/update.sh main"
fi

# Checkout is the boundary: it's atomic (a failure leaves the tree at PREV), so a
# plain fail here is safe — nothing has been mutated yet.
git checkout --quiet "$TARGET" || fail "could not check out '$TARGET' (local changes on the box? resolve them, then re-run)"
NEW="$(git rev-parse --short HEAD)"
NEW_VER="$(git describe --tags --always 2>/dev/null || echo "$NEW")"
log "code: $PREV_VER ($PREV) -> $NEW_VER ($NEW)"

# ── From here on, any failure AUTO-ROLLS-BACK code + DB. ─────────────────────

# 4) Dependencies (fast no-op when unchanged).
"$VENV/pip" install -q -r requirements.txt || rollback "pip install failed"

# 5) Database migrations.
"$VENV/python" manage.py migrate --noinput || rollback "migrate failed"

# 6) Build the self-hosted Tailwind stylesheet (standalone CLI, no Node), then collect static.
"$APP/scripts/build_css.sh" || rollback "CSS build failed"
"$VENV/python" manage.py collectstatic --noinput >/dev/null || rollback "collectstatic failed"

# 7) Restart the app.
sudo systemctl restart murphys-bench || rollback "service restart failed"

# 8) Health check — poll until the app finishes warming up after the restart, then
#    confirm it answers. We probe nginx on :80 (works whether gunicorn is on a unix
#    socket or a TCP port — nginx fronts both); we do NOT assume a specific socket
#    path. A 2xx/3xx/4xx means the stack is alive (a 4xx is just ALLOWED_HOSTS
#    rejecting the bare-IP request — still healthy); a 5xx or no connection after
#    the grace window is a real failure.
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
    *)        rollback "app not healthy after restart (HTTP $code)" ;;
esac

log "DONE: $PREV_VER ($PREV) -> $NEW_VER ($NEW)."
