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
# again. Run as the app user; the only privileged step is the service
# restart (already passwordless for this unit).
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

# 0) PRE-FLIGHT: never begin an update we cannot finish OR undo.
#
# Both the finish (step 7, restart) and the undo (rollback → restore.sh, which
# stops and starts the service) need root. If that isn't available without a
# password, the failure does not land at step 7 where it looks survivable — it
# lands twice: the restart fails, auto-rollback starts, and then restore.sh
# cannot stop the service either. The box is left on the OLD code with the NEW
# migrations already applied to its database, printing MANUAL RECOVERY NEEDED at
# someone who did nothing but click a button. A tester hit exactly this.
#
# Checked here, before the snapshot and before a single change, where the only
# cost of saying no is an update that didn't start. -n means "fail rather than
# prompt", which is the condition the systemd one-shot behind the in-app Update
# button always runs under.
# One exception, at the bottom: a human running this by hand from a terminal CAN
# supply a password. We take it once, up front, so the rollback path never stalls
# on a prompt halfway through a restore.
#
# The path is pinned, not taken from PATH: it is compared against the sudoers grant
# scripts/install.sh wrote, and a PATH-derived path would not match it.
SYSTEMCTL=/usr/bin/systemctl
[ -x "$SYSTEMCTL" ] || SYSTEMCTL=/bin/systemctl

# ⚠ DO NOT decide this by matching sudo's refusal text. Round 1 of this fix used
# `sudo -n -l`, which is a false pass (it answers "permitted", true even when a
# password is required). Round 2 read sudo's stderr prose, which an outside review
# correctly called fragile: the wording varies by sudo version and is localized, so
# a non-English box would silently pass.
#
# This asks a question with a machine-checkable answer instead. sudo writes its
# refusal to STDERR, so if it refuses, stdout is empty. `systemctl is-active`
# prints a fixed, unlocalized enum. Seeing one of those words means the command
# actually ran with privilege; anything else means it did not. No prose is parsed.
sudo_can_control_service() {
    local out
    out="$(LC_ALL=C sudo -n "$SYSTEMCTL" is-active murphys-bench 2>/dev/null || true)"
    # Deliberately NOT an enum of systemd states. Round 3 of this review pointed
    # out that an enumeration is a false-FAIL waiting to happen: systemd can add a
    # state word (it has before) and a box running it would be told, wrongly, that
    # it has no privilege. What actually matters is only whether systemctl ran at
    # all. sudo writes its refusal to stderr, so a refusal leaves stdout EMPTY;
    # any single lowercase word means the command executed with privilege.
    case "$out" in
        '') return 1 ;;
        *[!a-z-]*) return 1 ;;
        *) return 0 ;;
    esac
}

# Each verb the ROLLBACK needs, not just the one the update needs.
#
# Grepping a flattened `sudo -l` could match a verb from one rule against the
# NOPASSWD of another, so this matches within a single line and against the full
# resolved binary path. It is a secondary check: the authority for stop/start is
# the rollback drill, which actually runs them.
missing_verbs=""
policy="$(LC_ALL=C sudo -n -l 2>/dev/null || true)"
for verb in restart stop start; do
    printf '%s\n' "$policy" | grep -F 'NOPASSWD' | grep -qF "$SYSTEMCTL $verb murphys-bench" \
        || missing_verbs="$missing_verbs $verb"
done

if ! sudo_can_control_service || [ -n "$missing_verbs" ]; then
    if [ -t 0 ]; then
        log "this box lacks the passwordless service-control rule (missing:${missing_verbs:- none});
       asking for your password once so the update (and any rollback) can control
       the service without stalling.
       Run 'scripts/install.sh' to install the rule and stop being asked."
        sudo -v || fail "could not authenticate — nothing has been changed"
    else
        fail "this box cannot control the Murphy's Bench service without a password,
  so an update could neither finish nor safely roll back. NOTHING HAS BEEN CHANGED.
  Missing verbs:${missing_verbs:- (the rule appears absent entirely)}

  Fix it once, then update again:
    cd $APP && scripts/install.sh

  (That writes /etc/sudoers.d/murphys-bench, granting this user passwordless
  restart/stop/start of this one service and nothing else. It will ask for your password
  once, which is why it must be run from a terminal rather than the in-app
  Update button.)"
    fi
fi

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

# 1) Snapshot the current state to a LOCAL rollback tarball BEFORE anything is
#    touched. This is a same-box safety net for auto-rollback and is independent
#    of the off-box backup destinations (which mb_backup.sh ships to and then
#    deletes the local copy). --staging-only builds a verified local tarball and
#    keeps it, needing no configured destination. If this fails, nothing changed.
log "snapshotting before update (rollback point)..."
BACKUP_TARBALL="$APP/backups/preupdate-$(date +%Y%m%d-%H%M%S).tar.gz"
"$APP/scripts/mb_backup.sh" --staging-only "$BACKUP_TARBALL" \
    || fail "pre-update snapshot failed — aborting, nothing was changed"
[ -f "$BACKUP_TARBALL" ] || fail "could not create the pre-update rollback tarball — aborting before any change"
log "rollback point: $BACKUP_TARBALL"
# Keep only the last 3 pre-update rollback tarballs (this is not the off-box backup).
ls -1t "$APP/backups"/preupdate-*.tar.gz 2>/dev/null | tail -n +4 | xargs -r rm -f

# 2) Remember where we are (commit + human version), for rollback + reporting.
PREV="$(git rev-parse --short HEAD)"
PREV_VER="$(git describe --tags --always 2>/dev/null || echo "$PREV")"

# 3) Fetch and resolve the target: no arg = latest release tag; arg = that ref.
git fetch --all --tags --quiet || fail "git fetch failed"
if [ -n "$REF" ]; then
    # A BRANCH name must deploy the freshly-fetched REMOTE tip, not the box's stale
    # local branch ref. `git checkout main` lands on the LOCAL `main`, which fetch does
    # NOT fast-forward — so a plain checkout could silently deploy an OLD commit (it once
    # downgraded mb-test). When the ref resolves to a remote branch, check out the
    # remote-tracking ref (detached) so we always land on origin/<branch>. Tags and SHAs
    # are absolute refs — they resolve to the same commit locally or remote — so they're
    # checked out exactly as given.
    if git show-ref --verify --quiet "refs/remotes/origin/$REF"; then
        TARGET="origin/$REF"
    else
        TARGET="$REF"
    fi
else
    # ONLY strict vX.Y.Z release tags. Both `sort -V` and git's own `-v:refname`
    # rank a prerelease ABOVE the release it precedes: with v0.10.0 and
    # v0.10.0-rc1 both present, each of them picks v0.10.0-rc1. Since this is what
    # the in-app Update button deploys, pushing a single release-candidate tag
    # would hand every install in the field a prerelease as "the latest release",
    # and a pushed tag cannot be withdrawn once boxes have seen it.
    # `|| true` is load-bearing. This script runs under `set -euo pipefail`, and
    # grep exits 1 when nothing matches — which is exactly the "only prerelease
    # tags exist" case. Without it the script dies HERE, silently, and the
    # explanatory failure on the next line never runs. It still fails closed
    # either way; the cost is the diagnostic, which is the whole reason that line
    # exists.
    TARGET="$(git tag -l 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -1 || true)"
    [ -n "$TARGET" ] || fail "no release tags exist yet. Create one with scripts/release.sh \
(on your dev machine, after CI is green), or pass an explicit ref to deploy untagged \
code, e.g.: scripts/update.sh main"
fi

# Checkout is the boundary: it's atomic (a failure leaves the tree at PREV), so a
# plain fail here is safe — nothing has been mutated yet. `--detach` keeps branch
# deploys on the exact origin/<branch> commit (we never track a local branch on the box).
git checkout --quiet --detach "$TARGET" || fail "could not check out '$TARGET' (local changes on the box? resolve them, then re-run)"
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
# collectstatic writes new files with the app user's umask; nginx serves them as
# www-data. Without this, an update can leave freshly-added assets unreadable and
# the UI renders unstyled — the same failure install.sh guards against at install
# time, reintroduced one release later.
chmod -R o+rX "$APP/staticfiles" 2>/dev/null || true

# Post-update health of the DEPLOYMENT layer, not just the app. An install made
# before the July 2026 portability fix has no background-job units at all and may
# have an unreadable static directory; updating the code does not repair either,
# because both need sudo that this script deliberately does not have (prod grants
# it NOPASSWD for `systemctl restart` and nothing else). So: detect, and say so
# loudly with the exact command. Never silent, never fatal — a good update must
# not roll back over this.
#
# The expected unit list is READ from deploy/manifest.sh, the same file
# install_units.sh installs from and verify_install.sh asserts against, so this
# check cannot fall behind a release that adds a unit (in-app restore was exactly
# that: a button whose unit nothing installed and nothing checked for).
#
# Sourced in a SUBSHELL so the manifest cannot touch this script's own variables
# mid-update. Only MB_UNITS is read, never MB_UNITS_OPTIONAL, so the opt-in
# disk-check units never produce a false warning on a box that deliberately does
# not have them.
#
# ⚠ MUST NOT FAIL. This script runs under `set -e`, and the tree it reads has
# ALREADY been checked out to the target by this point — so on a rollback or a
# downgrade to any release older than the manifest, the file is simply not
# there. A bare subshell returns non-zero then, which killed the whole update at
# the assignment below, AFTER every real step had succeeded: the box was updated
# and healthy and the UI reported a failure. Caught by the clean-room gate,
# 2026-08-04. Returning empty-and-zero lets the fallback do its job instead.
expected_units() {
    ( . "$APP/deploy/manifest.sh" 2>/dev/null && printf '%s\n' "${MB_UNITS[@]:-}" ) 2>/dev/null || true
}

deploy_layer_warning() {
    local missing=() units
    units="$(expected_units)"
    # A parse that returns nothing would turn this whole check into a silent no-op,
    # which is the failure mode it exists to prevent. Fall back to the units the
    # in-app buttons depend on rather than passing by default.
    [ -z "$units" ] && units="murphys-bench-update.path
murphys-bench-backup-now.path
murphys-bench-backup.timer"
    for u in $units; do
        systemctl cat "$u" >/dev/null 2>&1 || missing+=("$u")
    done
    css="$(ls "$APP"/staticfiles/css/*.css 2>/dev/null | head -1 || true)"
    css_code=000
    [ -n "$css" ] && css_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
        "http://127.0.0.1/static/css/$(basename "$css")" 2>/dev/null || echo 000)"

    # The in-app Update button is the path most people use, and it renders a green
    # "succeeded" banner with the log COLLAPSED. Printing this warning to stderr
    # therefore told a terminal user and left everyone else with a tick and a
    # disclosure triangle nobody opens after being told it worked. So the state is
    # also written where the app can read it and say so in its own UI. Removed the
    # moment the install is whole again, so it can never outlive the problem.
    local marker="$APP/logs/update-incomplete"

    if [ "${#missing[@]}" -eq 0 ] && [ "$css_code" = "200" ]; then
        rm -f "$marker"
        return 0
    fi

    {
        if [ "${#missing[@]}" -gt 0 ]; then
            echo "These system services are not installed:"
            for u in "${missing[@]}"; do echo "  $u"; done
            echo "A missing .path unit means the matching button queues a job nothing"
            echo "picks up and spins forever. A missing .timer means that scheduled job"
            echo "never runs. Updating cannot install them, because Murphy's Bench runs"
            echo "as an unprivileged user on purpose."
        fi
        if [ "$css_code" != "200" ]; then
            echo "The web server cannot read this install's stylesheets (HTTP $css_code)."
            echo "Pages render as unstyled HTML with no logo."
        fi
        echo
        if [ "$css_code" = "200" ]; then
            echo "FIX: cd $APP && scripts/install_units.sh"
        else
            echo "FIX: cd $APP && scripts/install.sh"
        fi
        echo "Run it in a terminal. It needs a password, which is exactly why the"
        echo "Update button cannot do it for you. See UPDATING.md."
    } > "$marker" 2>/dev/null || true

    echo
    echo "⚠ THIS INSTALL IS INCOMPLETE. The update itself succeeded, but:" >&2
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "  • These system services are NOT installed:" >&2
        for u in "${missing[@]}"; do echo "      $u" >&2; done
        echo "    This update moved code onto the server, but it could not install" >&2
        echo "    system services, because Murphy's Bench runs unprivileged on purpose." >&2
        echo "    Whatever those services drive does not work: a missing .path unit" >&2
        echo "    means the matching in-app button queues a job nothing picks up and" >&2
        echo "    spins forever, and a missing .timer means that scheduled job never" >&2
        echo "    runs. Settings → Maintenance → Updates reports this too, and keeps" >&2
        echo "    reporting it until it is fixed." >&2
    fi
    if [ "$css_code" != "200" ]; then
        echo "  • The web server cannot read this install's stylesheets (HTTP $css_code)." >&2
        echo "    Pages render as unstyled HTML with no logo." >&2
    fi
    echo >&2
    # Units alone need only the unit installer. A static-permissions problem needs the
    # full installer, which install_units.sh does not touch.
    if [ "$css_code" = "200" ]; then
        echo "  Fix it once. Safe to re-run over an existing install:" >&2
        echo "    cd $APP && scripts/install_units.sh" >&2
    else
        echo "  Fix it once. Safe to re-run over an existing install:" >&2
        echo "    cd $APP && scripts/install.sh" >&2
    fi
    echo >&2
    echo "  Both need a password, so run them from a terminal. The in-app Update" >&2
    echo "  button cannot do this itself. See UPDATING.md." >&2
    echo >&2
}
deploy_layer_warning

# 6b) Regenerate the backup destination files from SiteSettings, so a fresh box /
# post-restore never silently loses its configured offsite target. Non-fatal —
# an un-configured box just stays local-only.
"$VENV/python" manage.py render_backup_config >/dev/null 2>&1 || log "render_backup_config skipped (non-fatal)"

# 7) Restart the app.
# Absolute path, matching the sudoers grant — see the pre-flight note above.
sudo "$SYSTEMCTL" restart murphys-bench || rollback "service restart failed"

# 8) Health check — poll until the app finishes warming up after the restart, then
#    confirm it answers. We probe nginx on :80 (works whether gunicorn is on a unix
#    socket or a TCP port — nginx fronts both); we do NOT assume a specific socket
#    path. We connect to 127.0.0.1 (no dependency on LAN routing/DNS) but send the
#    real Host header from ALLOWED_HOSTS, so the probe exercises the same
#    Django ALLOWED_HOSTS check real traffic hits — previously this probed with
#    Host: 127.0.0.1, which prod's ALLOWED_HOSTS omitted, so it always got a 400
#    that was then excused as "still healthy." That masked the check rather than
#    passing it: a real ALLOWED_HOSTS misconfiguration would have reported
#    healthy too. Now a 2xx/3xx means genuinely healthy; a 4xx or 5xx (or no
#    connection) after the grace window is a real failure and triggers rollback.
PROBE_HOST="$(grep -E '^ALLOWED_HOSTS=' .env 2>/dev/null | cut -d= -f2- | cut -d, -f1)"
PROBE_HOST="${PROBE_HOST:-127.0.0.1}"
code=000
for _ in $(seq 1 15); do
    if systemctl is-active --quiet murphys-bench; then
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 -H "Host: $PROBE_HOST" http://127.0.0.1/ || echo 000)"
        case "$code" in 2*|3*) break ;; esac
    fi
    sleep 1
done
case "$code" in
    2*|3*) log "app healthy (HTTP $code, Host: $PROBE_HOST)" ;;
    *)     rollback "app not healthy after restart (HTTP $code, Host: $PROBE_HOST)" ;;
esac

log "DONE: $PREV_VER ($PREV) -> $NEW_VER ($NEW)."
