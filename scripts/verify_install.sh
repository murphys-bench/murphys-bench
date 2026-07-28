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
login_html="$(curl -s http://127.0.0.1/accounts/login/ 2>/dev/null || true)"
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

head_ "In-app 'Back up now' actually produces a backup"
# Drive the real mechanism the button uses: drop the trigger file and let the
# .path unit take it from there. Nothing else in the test suite exercises this.
before="$(ls -1 "$APP"/backups/*.tar.gz 2>/dev/null | wc -l | tr -d ' ')"
rm -f "$APP/logs/backup-status.json"
touch "$APP/logs/backup-trigger"
for _ in $(seq 1 60); do
    state="$("$APP/venv/bin/python" -c "import json;print(json.load(open('$APP/logs/backup-status.json')).get('state',''))" 2>/dev/null || true)"
    case "$state" in succeeded|failed) break ;; esac
    sleep 2
done
after="$(ls -1 "$APP"/backups/*.tar.gz 2>/dev/null | wc -l | tr -d ' ')"
case "${state:-}" in
    succeeded) if [ "$after" -gt "$before" ]; then
                   ok "backup ran on demand and wrote an archive"
               else
                   bad "backup reported success but produced no archive"
               fi ;;
    failed)    bad "on-demand backup ran and FAILED — see logs/backup.log" ;;
    *)         bad "on-demand backup never started (still '${state:-queued}' after 120s)
        This is the tester-reported bug: the trigger file is written and nothing
        consumes it. Check: systemctl status murphys-bench-backup-now.path" ;;
esac
rm -f "$APP/logs/backup-trigger"

head_ "In-app Update is wired (not executed)"
# Deliberately not run — an update on a verification box proves little and
# rewrites the checkout under us. Assert the machinery exists and is armed.
if systemctl is-active --quiet murphys-bench-update.path \
   && [ -x "$APP/scripts/run_update.sh" ] \
   && systemctl cat murphys-bench-update.service 2>/dev/null | grep -q "ExecStart=$APP/scripts/run_update.sh"; then
    ok "update path unit armed and pointing at this install"
else
    bad "in-app Update would spin forever — path unit or run_update.sh not wired"
fi

printf '\n== Result: %d passed, %d failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf '\nDO NOT RELEASE. A failure here is a defect an outside user hits on install.\n'
    exit 1
fi
printf '\nClean-room install verified.\n'
