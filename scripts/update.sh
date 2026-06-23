#!/usr/bin/env bash
# One-command, fail-loud update for Murphy's Bench.
#
#   scripts/update.sh            # update to the latest on the current branch
#   scripts/update.sh v0.3       # update to a specific tag/branch/commit
#
# It ALWAYS backs up before touching anything, so a failed migrate leaves you a
# restore point. Run it as the app user (scs-tech). Nothing here needs a password
# except the service restart (already passwordless for this unit).
set -euo pipefail

APP=/opt/murphys-bench
cd "$APP"

log()  { echo "$(date '+%F %T') update: $*"; }
fail() { echo "UPDATE FAILED: $*" >&2; exit 1; }

[ -f manage.py ] || fail "no manage.py in $APP — wrong directory?"
command -v git >/dev/null || fail "git not installed"

REF="${1:-}"

# 1) Back up FIRST (snapshot-before-migrate). If this fails, we stop — nothing changed.
log "backing up before update (snapshot-before-migrate)..."
"$APP/scripts/mb_backup.sh" || fail "pre-update backup failed — aborting, nothing was changed"

# 2) Remember where we were, for rollback.
PREV="$(git rev-parse --short HEAD)"
log "current version: $PREV"

# 3) Get the new code.
git fetch --all --tags --quiet || fail "git fetch failed"
if [ -n "$REF" ]; then
    git checkout "$REF" || fail "could not check out '$REF'"
else
    git pull --ff-only || fail "git pull failed (local changes on the box? resolve them, then re-run)"
fi
NEW="$(git rev-parse --short HEAD)"
log "code: $PREV -> $NEW"

# 4) Dependencies (fast no-op when unchanged).
"$APP/venv/bin/pip" install -q -r requirements.txt || fail "pip install failed"

# 5) Database migrations.
"$APP/venv/bin/python" manage.py migrate --noinput \
    || fail "migrate failed — DB may be partially migrated. Restore the pre-update backup (scripts/restore.sh), then investigate."

# 6) Build the self-hosted Tailwind stylesheet (standalone CLI, no Node), then collect static.
"$APP/scripts/build_css.sh" || fail "CSS build failed"
"$APP/venv/bin/python" manage.py collectstatic --noinput >/dev/null || fail "collectstatic failed"

# 7) Restart the app.
sudo systemctl restart murphys-bench || fail "service restart failed"

# 8) Health check — poll until the app finishes warming up after the restart, then
#    confirm it answers. A 2xx/3xx/4xx means the stack is alive (a 4xx is just
#    ALLOWED_HOSTS rejecting the bare-IP request — still healthy); a 5xx or no
#    connection after the grace window is a real failure.
code=000
for _ in $(seq 1 10); do
    if systemctl is-active --quiet murphys-bench && [ -S "$APP/murphys.sock" ]; then
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1/ || echo 000)"
        case "$code" in 2*|3*|4*) break ;; esac
    fi
    sleep 1
done
case "$code" in
    2*|3*|4*) log "app healthy (HTTP $code)" ;;
    *)        fail "app not healthy after restart (HTTP $code). Roll back: git checkout $PREV && venv/bin/python manage.py migrate && sudo systemctl restart murphys-bench" ;;
esac

log "DONE: $PREV -> $NEW. To roll back: git checkout $PREV && venv/bin/python manage.py migrate && sudo systemctl restart murphys-bench"
