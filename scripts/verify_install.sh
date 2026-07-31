#!/usr/bin/env bash
# Clean-room install verification — the release gate.
#
# WHY THIS EXISTS
# ---------------
# Every automated check Murphy's Bench had before this script verified the CODE:
# pytest runs Django in-process and knows nothing about systemd, nginx, file
# permissions, or where the app is installed. Everything past that boundary was
# only ever validated by hand, on boxes that had been set up by hand — so a whole
# class of defect was invisible from the inside and hit outside testers first.
# It did, in July 2026: on any install that wasn't the author's own server, the
# in-app Back up now and Update buttons spun forever, scheduled backups never
# ran, and the login page rendered unstyled. All silently.
#
# This script asserts BEHAVIOUR on a box built only by scripts/install.sh — that
# the features the UI offers actually do something. A green pytest run plus a
# green run of this is the release gate.
#
# HOW TO RUN IT
# -------------
# On a THROWAWAY VM (fresh Ubuntu, no MB history), deliberately NOT at
# /opt/murphys-bench — a path under the login user's home is the better test,
# because that is the layout that broke:
#
#   git clone <REPO_URL> ~/murphys-bench
#   cd ~/murphys-bench && scripts/install.sh --noinput
#   scripts/verify_install.sh
#
# Exit 0 = safe to tag. Any failure = do not release.
set -uo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP"

PASS=0; FAIL=0
ok()   { printf '  \033[32mPASS\033[0m  %s\n' "$1"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAIL=$((FAIL+1)); }
head_() { printf '\n== %s\n' "$1"; }

head_ "Install location"
case "$APP" in
    /opt/murphys-bench) echo "  NOTE: verifying at /opt/murphys-bench — the one path that always
        worked even when the scripts were broken. Re-run this on a VM with the
        app somewhere else (e.g. ~/murphys-bench) for the check that has teeth." ;;
    *) ok "installed at a non-default path ($APP) — portability is under test" ;;
esac

head_ "No hardcoded paths or usernames left in the deployment layer"
# Comment lines are excluded — several deliberately mention the old hardcoded
# values to explain why they're gone. The strip has to account for grep's
# "file:line:" prefix, or every explanatory comment reads as a violation.
# release.sh (prints example commands for a human) and this script (which greps
# for the strings as its own check) are exempt — the strings are their subject
# matter, not their configuration. Mirrors the exemption in core/tests.py.
offenders="$(grep -rn '/opt/murphys-bench\|scs-tech' scripts/*.sh \
     | grep -vE '^scripts/(release|verify_install)\.sh:' \
     | grep -vE '^[^:]+:[0-9]+:[[:space:]]*#' || true)"
if [ -n "$offenders" ]; then
    bad "a script still hardcodes /opt/murphys-bench or scs-tech in executable code:"
    printf '%s\n' "$offenders" | sed 's/^/        /'
else
    ok "no script hardcodes the author's install path or app user"
fi

head_ "systemd units installed and running"
UNITS_ENABLED=(
    murphys-bench.service
    murphys-bench-update.path
    murphys-bench-backup-now.path
    murphys-bench-backup.timer
    murphys-bench-fetch-email.timer
    murphys-bench-sla-check.timer
)
for u in "${UNITS_ENABLED[@]}"; do
    if ! systemctl cat "$u" >/dev/null 2>&1; then
        bad "$u is not installed — the feature behind it silently does nothing"
    elif systemctl is-active --quiet "$u"; then
        ok "$u active"
    else
        bad "$u installed but not active ($(systemctl is-active "$u" 2>&1))"
    fi
done

# A unit that still carries a template placeholder loads fine and fails only when
# it fires — the silent shape this whole change exists to eliminate.
if systemctl cat 'murphys-bench-*' 2>/dev/null | grep -q '__APP__\|__RUN_USER__'; then
    bad "an installed unit still contains an unsubstituted __APP__/__RUN_USER__ placeholder"
else
    ok "no unsubstituted placeholders in installed units"
fi

# Units point at THIS install, not some other checkout left on the box.
if systemctl cat murphys-bench-backup-now.service 2>/dev/null | grep -q "ExecStart=$APP/"; then
    ok "units point at this install ($APP)"
else
    bad "units do not point at $APP — they were rendered for a different checkout"
fi

head_ "Web server serves the app and its static files"
code_root="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/ 2>/dev/null || echo 000)"
if [ "$code_root" = "200" ] || [ "$code_root" = "302" ]; then
    ok "app reachable on port 80 (HTTP $code_root)"
else
    bad "app not reachable on port 80 (HTTP $code_root)"
fi

css="$(ls "$APP"/staticfiles/css/*.css 2>/dev/null | head -1 || true)"
if [ -z "$css" ]; then
    bad "no CSS in staticfiles/css — the UI would render unstyled"
else
    code_css="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1/static/css/$(basename "$css")" 2>/dev/null || echo 000)"
    if [ "$code_css" = "200" ]; then
        ok "stylesheet served by nginx (HTTP 200)"
    else
        bad "stylesheet returns HTTP $code_css — login page would render as bare HTML"
    fi
fi

# The login page must actually reference a stylesheet that resolves. This is the
# check that maps directly to what the tester saw: a readable page, no styling.
# Reached by following the redirect from /, rather than by naming a login URL —
# the URL is two_factor's and has moved before, and a hardcoded path here fails
# as a 404 that looks exactly like the styling bug it is meant to detect.
login_html="$(curl -sL http://127.0.0.1/ 2>/dev/null || true)"
ref="$(printf '%s' "$login_html" | grep -o '/static/[^"'"'"']*\.css' | head -1 || true)"
if [ -z "$ref" ]; then
    bad "login page references no stylesheet"
else
    code_ref="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1$ref" 2>/dev/null || echo 000)"
    if [ "$code_ref" = "200" ]; then
        ok "the stylesheet the login page asks for resolves (HTTP 200)"
    else
        bad "login page asks for $ref which returns HTTP $code_ref — it would render unstyled"
    fi
fi

head_ "In-app 'Back up now' reaches the backup script"
# Drive the real mechanism the button uses: drop the trigger file and let the
# .path unit take it from there. Nothing else in the test suite exercises this.
#
# What is under test is that SOMETHING CONSUMES THE TRIGGER. That is the defect
# this release fixes: the file was written and no unit existed to act on it, so
# the UI spun forever. Whether the backup then succeeds depends on the admin
# having configured a destination, which a fresh box has not — mb_backup.sh
# refusing with "no backup destination configured" is correct fail-loud
# behaviour and must not be reported as an install failure. Only a run that
# never starts, or one that fails for any OTHER reason, is a defect here.
before="$(ls -1 "$APP"/backups/*.tar.gz 2>/dev/null | wc -l | tr -d ' ')"
rm -f "$APP/logs/backup-status.json"
touch "$APP/logs/backup-trigger"
for _ in $(seq 1 60); do
    state="$("$APP/venv/bin/python" -c "import json;print(json.load(open('$APP/logs/backup-status.json')).get('state',''))" 2>/dev/null || true)"
    case "$state" in succeeded|failed) break ;; esac
    sleep 2
done
after="$(ls -1 "$APP"/backups/*.tar.gz 2>/dev/null | wc -l | tr -d ' ')"
log_tail="$(tail -5 "$APP/logs/backup.log" 2>/dev/null || true)"
case "${state:-}" in
    succeeded)
        if [ "$after" -gt "$before" ]; then
            ok "backup ran on demand and wrote an archive"
        else
            bad "backup reported success but produced no archive"
        fi ;;
    failed)
        # Distinguish the one expected failure on an unconfigured box from a real one.
        if printf '%s' "$log_tail" | grep -q 'no backup destination configured'; then
            ok "trigger consumed and mb_backup.sh ran (correctly refused: no destination configured yet)"
        else
            bad "on-demand backup ran and FAILED for a real reason:
$(printf '%s' "$log_tail" | sed 's/^/        /')"
        fi ;;
    *)
        bad "on-demand backup never started (still '${state:-queued}' after 120s)
        This is the tester-reported bug: the trigger file is written and nothing
        consumes it. Check: systemctl status murphys-bench-backup-now.path" ;;
esac
rm -f "$APP/logs/backup-trigger"

head_ "A failed job actually reports itself"
# MB's self-monitoring turns a failed job into a System Alert ticket. Installing
# the alert unit is not enough — a job only reports failure if it carries
# OnFailure=, and that wiring lived in a copy-paste block in deploy/README.md, so
# on every box but the author's the whole feature was silently absent. A nightly
# backup could fail forever and say nothing, which is the exact opposite of what
# it was built for.
#
# Two halves, both required: systemd must SHOW the hook on the real units, and
# the alert must actually produce a ticket when it runs.
missing_hook=()
for u in murphys-bench-backup murphys-bench-fetch-email murphys-bench-sla-check; do
    systemctl show -p OnFailure --value "$u.service" 2>/dev/null \
        | grep -q 'murphys-bench-alert@' || missing_hook+=("$u")
done
if [ "${#missing_hook[@]}" -eq 0 ]; then
    ok "scheduled jobs are wired to report their own failure (${#missing_hook[@]} missing)"
else
    bad "these jobs would fail SILENTLY — no OnFailure hook: ${missing_hook[*]}
        Nothing would open a ticket when a backup fails."
fi

# Fire the alert path for real and confirm a ticket lands. Uses a clearly-marked
# subject so it is obvious in the ticket list that this came from the verifier.
alert_subject="Install verification: alert delivery test"
"$APP/venv/bin/python" "$APP/manage.py" send_alert "$alert_subject" \
    "Written by scripts/verify_install.sh to prove failure alerts reach the app. Safe to close." \
    >/dev/null 2>&1 || true
if "$APP/venv/bin/python" "$APP/manage.py" shell -c "
from core.models import Ticket
import sys
sys.exit(0 if Ticket.objects.filter(subject='$alert_subject', source='system').exists() else 1)
" >/dev/null 2>&1; then
    ok "a fired alert reached the app and opened a System Alert ticket"
else
    bad "firing an alert produced NO ticket — a failed job would report nothing.
        Check: $APP/venv/bin/python manage.py send_alert 'test' 'test'"
fi

head_ "In-app Update is wired"
if systemctl is-active --quiet murphys-bench-update.path \
   && [ -x "$APP/scripts/run_update.sh" ] \
   && systemctl cat murphys-bench-update.service 2>/dev/null | grep -q "ExecStart=$APP/scripts/run_update.sh"; then
    ok "update path unit armed and pointing at this install"
else
    bad "in-app Update would spin forever — path unit or run_update.sh not wired"
fi

head_ "The app can restart itself without a terminal"
# The single check that would have caught the July 30 tester failure.
#
# update.sh ends in `sudo systemctl restart murphys-bench`, and rollback's
# restore.sh stops and starts the service too. Both run inside a systemd one-shot
# with NO TERMINAL when the update comes from the in-app button, so sudo cannot
# prompt. On every box where that rule had not been added by hand, the update
# failed at the restart AND the rollback failed at the stop, leaving old code on a
# migrated database.
#
# `sudo -k` first: this script may run right after install.sh, whose interactive
# sudo left a cached credential that would make this pass on a box where the rule
# is missing. That cache is the difference between testing the rule and testing
# nothing.
sudo -k
SYSTEMCTL=/usr/bin/systemctl
[ -x "$SYSTEMCTL" ] || SYSTEMCTL=/bin/systemctl

# ⚠ Two earlier versions of this check were unsound. `sudo -n -l` is a false pass
# (it answers "permitted", true even when a password is required). Matching sudo's
# stderr prose is locale- and version-dependent. This runs the granted command and
# looks for ITS output: sudo's refusal goes to stderr, so a refusal leaves stdout
# empty, and `systemctl is-active` prints a fixed unlocalized enum.
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

if sudo_can_control_service; then
    ok "the app user can control its own service without a password"
else
    bad "NO passwordless service control for $(id -un). The in-app Update button
        cannot finish (restart fails) and cannot undo itself (rollback's restore
        cannot stop the service). Expected /etc/sudoers.d/murphys-bench from
        scripts/install.sh.  Check: sudo -l | grep murphys-bench"
fi

# Each verb rollback needs, not just the one the update needs. Granting restart
# alone lets an update succeed while leaving restore.sh unable to run — which is
# precisely the state the first version of this fix shipped in.
missing_verbs=""
policy="$(LC_ALL=C sudo -n -l 2>/dev/null || true)"
for verb in restart stop start; do
    # Matched within a SINGLE line and against the full resolved path, so a verb
    # from one rule cannot be paired with the NOPASSWD of another.
    printf '%s\n' "$policy" | grep -F 'NOPASSWD' | grep -qF "$SYSTEMCTL $verb murphys-bench" \
        || missing_verbs="$missing_verbs $verb"
done
if [ -z "$missing_verbs" ]; then
    ok "every verb the update AND its rollback need is granted (restart, stop, start)"
else
    bad "the sudoers rule is MISSING:${missing_verbs}
        An update could still finish, but a FAILED update could not roll back —
        leaving old code against an already-migrated database."
fi

head_ "Rollback can actually run with no terminal"
# The update path and the ROLLBACK path are different code with different
# privileges, and only one of them was ever tested. A green update proves nothing
# about what happens when one fails — which is the moment a user actually needs it
# to work, and the moment a tester hit.
#
# restore.sh IS the rollback: it stops the service, restores the database, starts
# it again and health-checks. Drive it exactly as the in-app one-shot would:
#   sudo -k   no cached password to lean on
#   setsid    no controlling terminal, so sudo cannot prompt
#   </dev/null nothing to read a password from
# If this passes, a failed update can undo itself on this box.
if [ "${SKIP_ROLLBACK_DRILL:-0}" = 1 ]; then
    echo "  SKIP  rollback drill disabled by SKIP_ROLLBACK_DRILL=1"
else
    drill_tarball="$APP/backups/verify-rollback-drill.tar.gz"
    rm -f "$drill_tarball"
    if "$APP/scripts/mb_backup.sh" --staging-only "$drill_tarball" >/dev/null 2>&1 \
       && [ -f "$drill_tarball" ]; then
        # A drill that only proves restore.sh EXITS 0 proves the privileges, not
        # the rollback. Real rollback runs after a failed migration has already
        # changed the database, and the thing that matters is that those changes
        # are gone afterwards. So: write a sentinel row AFTER the snapshot, and
        # require the restore to have removed it.
        sentinel="ROLLBACK-DRILL-SENTINEL-$$"
        "$APP/venv/bin/python" "$APP/manage.py" shell -c "
from core.models import Client
Client.objects.create(name='$sentinel')
" >/dev/null 2>&1 || bad "could not write the rollback sentinel — drill is inconclusive"
        sudo -k
        if setsid env RESTORE_YES=1 "$APP/scripts/restore.sh" "$drill_tarball" \
             </dev/null >"$APP/logs/rollback-drill.log" 2>&1; then
            ok "rollback (restore.sh) completed with no terminal and no cached password"
        else
            bad "ROLLBACK CANNOT RUN on this box. A failed update would leave old code
        against an already-migrated database. Last lines:
$(tail -12 "$APP/logs/rollback-drill.log" 2>/dev/null | sed 's/^/        /')"
        fi
        # The sentinel was created after the snapshot, so a real rollback must have
        # erased it. If it survived, restore.sh ran and reported success without
        # actually reverting the database — the failure mode that matters most.
        if "$APP/venv/bin/python" "$APP/manage.py" shell -c "
from core.models import Client
import sys
sys.exit(1 if Client.objects.filter(name='$sentinel').exists() else 0)
" >/dev/null 2>&1; then
            ok "rollback reverted a post-snapshot database change (sentinel gone)"
        else
            bad "ROLLBACK DID NOT REVERT THE DATABASE. A row written after the snapshot
        survived the restore, so a failed migration's changes would survive too.
        Leftover marker: $sentinel"
            # Don't leave the marker behind on a box someone keeps using.
            "$APP/venv/bin/python" "$APP/manage.py" shell -c "
from core.models import Client
Client.objects.filter(name='$sentinel').delete()
" >/dev/null 2>&1 || true
        fi

        # restore.sh restarts the service itself; confirm the app really came back.
        rb_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
                  -H "Host: $(grep '^ALLOWED_HOSTS=' "$APP/.env" | cut -d= -f2- | cut -d, -f1)" \
                  http://127.0.0.1/ || echo 000)"
        case "$rb_code" in
            2*|3*) ok "app healthy after the rollback drill (HTTP $rb_code)" ;;
            *)     bad "app returns HTTP $rb_code after the rollback drill" ;;
        esac
    else
        bad "could not build a drill snapshot, so rollback is UNVERIFIED on this box"
    fi
    rm -f "$drill_tarball"
fi

head_ "In-app Update actually runs, end to end"
# EXECUTED, not inspected. The previous version of this gate checked only that
# the wiring existed and said an update "proves little" here. It proved the one
# thing that mattered and we didn't run it: a box can have every unit armed and
# still be unable to complete an update. Assert the outcome, not the plumbing.
#
# Re-deploying the tag the box is already on is the right test: it runs the whole
# script — snapshot, pip, migrate, CSS, collectstatic, RESTART, health check —
# and lands where it started, so the checkout is not left somewhere unexpected.
if [ "${SKIP_UPDATE_RUN:-0}" = 1 ]; then
    echo "  SKIP  update run disabled by SKIP_UPDATE_RUN=1"
else
    rm -f "$APP/logs/update-status.json"
    before_head="$(git -C "$APP" rev-parse HEAD)"
    touch "$APP/logs/update-trigger"
    ustate=""
    for _ in $(seq 1 150); do
        ustate="$("$APP/venv/bin/python" -c "import json;print(json.load(open('$APP/logs/update-status.json')).get('state',''))" 2>/dev/null || true)"
        case "$ustate" in succeeded|failed) break ;; esac
        sleep 2
    done
    utail="$(tail -25 "$APP/logs/update.log" 2>/dev/null || true)"
    case "${ustate:-}" in
        succeeded)
            ok "in-app Update ran to completion and the app came back healthy"
            # A success that reported success is not enough: confirm the app is
            # actually answering after its own restart.
            ucode="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
                     -H "Host: $(grep '^ALLOWED_HOSTS=' "$APP/.env" | cut -d= -f2- | cut -d, -f1)" \
                     http://127.0.0.1/ || echo 000)"
            case "$ucode" in
                2*|3*) ok "app answering after the update's own restart (HTTP $ucode)" ;;
                *)     bad "update reported success but the app returns HTTP $ucode" ;;
            esac ;;
        failed)
            bad "in-app Update RAN AND FAILED. This is what a tester gets when they
        click the button. Last lines of $APP/logs/update.log:
$(printf '%s' "$utail" | sed 's/^/        /')" ;;
        *)
            bad "in-app Update never reached a terminal state after 300s (stuck at
        '${ustate:-queued}') — the button would spin forever.
        Check: systemctl status murphys-bench-update.path murphys-bench-update.service" ;;
    esac
    rm -f "$APP/logs/update-trigger"
    # An update deploys the latest RELEASE TAG. On a box checked out somewhere
    # else (a branch under test, an untagged commit) that legitimately moves HEAD,
    # and leaving the verifier's checkout silently moved is its own trap.
    after_head="$(git -C "$APP" rev-parse HEAD)"
    if [ "$before_head" != "$after_head" ]; then
        echo "  NOTE: the update moved this checkout from ${before_head:0:8} to ${after_head:0:8}
        (it deploys the latest release tag). That is the real behaviour, not a
        fault. Re-run install.sh if you need the box back on the earlier commit."
    fi
fi

printf '\n== Result: %d passed, %d failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf '\nDO NOT RELEASE. A failure here is a defect an outside user hits on install.\n'
    exit 1
fi
printf '\nClean-room install verified.\n'
