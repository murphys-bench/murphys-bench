#!/usr/bin/env bash
# Decide whether THIS install is complete, and record the answer where both a
# terminal user and the app can see it.
#
# Why this is its own script, and not a function inside update.sh:
#
#   update.sh was the only thing that ever wrote or deleted
#   logs/update-incomplete. The marker tells the operator to run
#   install_units.sh — and install_units.sh did not touch the marker. So the
#   documented fix repaired the server and could not clear the warning: the box
#   was whole and the Updates page kept saying it was broken until the NEXT
#   update happened to run. Mike hit exactly this on prod, 2026-08-05: ran the
#   command the banner asked for, and nothing changed on screen.
#
#   The answer is not for each script to delete the file — that puts a second
#   (and then a third) definition of "complete" in the tree, which is the drift
#   deploy/manifest.sh exists to prevent. There is one check, here, and update.sh,
#   install_units.sh and install.sh all call it when they finish.
#
# Usage: scripts/check_install.sh [--no-static-probe]
#
#   --no-static-probe   re-check the SERVICES only, and carry any existing
#                       stylesheet warning through untouched. install_units.sh
#                       passes this: it installs units and cannot change static
#                       file permissions, so it has no business reporting on
#                       them in either direction — neither clearing a real
#                       warning nor inventing one on a --skip-web box that has
#                       no web server to probe.
#
# ⚠ MUST NOT FAIL ITS CALLER. install.sh and update.sh run under `set -e`, and
# update.sh calls this AFTER migrate, css and collectstatic have all succeeded.
# A non-zero exit here would report a failed update on a box that updated
# perfectly — which is exactly the defect the clean-room gate caught on
# 2026-08-04. Hence `set -uo pipefail` without `-e`, and `exit 0` at the end.
set -uo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="$APP/logs/update-incomplete"

# The stylesheet warning's opening words, written ONCE and used by both the code
# that emits it and the code that carries it through a units-only repair. They
# used to be two copies of the same sentence, so a copy edit to one would have
# made install_units.sh silently discard a real "your site is unstyled" warning
# while repairing units. Caught in review.
#
# ⚠ core/update_ops.py keeps its own prefix for the app-side parser
# (_STATIC_HEADER_PREFIX). It must stay a prefix of this line;
# test_static_warning_prefix_is_the_same_string_everywhere fails if it drifts.
STATIC_HEADER="The web server cannot read this install's stylesheets"

STATIC_PROBE=1
for a in "$@"; do
    case "$a" in
        --no-static-probe) STATIC_PROBE=0 ;;
        *) echo "check_install: unknown option: $a" >&2; exit 0 ;;
    esac
done

# The units this release expects, in order of how much the source can be trusted.
#
# ⚠ MUST NOT FAIL. The tree this reads may have ALREADY been checked out to an
# older target by a rollback, so on any release older than the manifest the file
# is simply not there. A bare subshell returns non-zero then, which under `set -e`
# killed the whole update after every real step had succeeded: the box was
# updated and healthy and the UI reported a failure. Caught by the clean-room
# gate, 2026-08-04. Returning empty-and-zero lets the fallback do its job.
expected_units() {
    local units

    # 1. Current shape: deploy/manifest.sh, sourced in a SUBSHELL so it cannot
    #    touch this script's variables.
    units="$( ( . "$APP/deploy/manifest.sh" 2>/dev/null && printf '%s\n' "${MB_UNITS[@]:-}" ) 2>/dev/null || true )"
    if [ -n "$units" ]; then
        printf '%s\n' "$units"
        return 0
    fi

    # 2. Pre-manifest target: read THAT release's own literal UNITS block with the
    #    same reader v0.11.1 used. Without this the manifest-less path fell all the
    #    way through to the three-unit fallback below, so a rollback to v0.11.1 —
    #    which declares 14 — checked neither the restore units nor inbound-email nor
    #    SLA, and could report clean while they were missing. Under-reporting on the
    #    recovery path is the failure this whole check exists to prevent.
    #    `|| true` because that pipeline ends in grep, which exits 1 on empty.
    awk '/^UNITS=\(/{f=1;next} f&&/^\)/{exit} f{print}' "$APP/scripts/install_units.sh" 2>/dev/null \
      | sed -e 's/#.*//' -e "s/['\"]//g" -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
      | grep -v '^$' || true
}

missing=()
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

# The stylesheet half. A box can have every unit and still serve unstyled HTML
# with no logo, because nginx runs as www-data and cannot traverse a 750 home
# directory — which is what reached the first outside tester.
css_code=200
carried_static=""
if [ "$STATIC_PROBE" = 1 ]; then
    css="$(ls "$APP"/staticfiles/css/*.css 2>/dev/null | head -1 || true)"
    css_code=000
    [ -n "$css" ] && css_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
        "http://127.0.0.1/static/css/$(basename "$css")" 2>/dev/null || echo 000)"
else
    # Not probed, so not ours to clear. If the last full check found a stylesheet
    # problem, carry those exact lines through: repairing the units does not
    # repair the styling, and silently dropping the warning would be the same
    # class of lie as never showing it.
    carried_static="$(grep -A1 "^$STATIC_HEADER" "$MARKER" 2>/dev/null || true)"
fi

if [ "${#missing[@]}" -eq 0 ] && [ "$css_code" = "200" ] && [ -z "$carried_static" ]; then
    rm -f "$MARKER"
    exit 0
fi

# The in-app Update button is the path most people use, and it renders a green
# "succeeded" banner with the log COLLAPSED. Printing this warning to stderr
# alone therefore told a terminal user and left everyone else with a tick and a
# disclosure triangle nobody opens after being told it worked. So the state is
# also written where the app can read it and say so in its own UI. Removed the
# moment the install is whole again, so it can never outlive the problem.
{
    if [ "${#missing[@]}" -gt 0 ]; then
        echo "These system services are not installed:"
        for u in "${missing[@]}"; do echo "  $u"; done
        echo "A missing .path unit means the matching button queues a job nothing"
        echo "picks up and spins forever. A missing .timer means that scheduled job"
        echo "never runs. Updating cannot install them, because Murphy's Bench runs"
        echo "as an unprivileged user on purpose."
    fi
    if [ -n "$carried_static" ]; then
        printf '%s\n' "$carried_static"
    elif [ "$css_code" != "200" ]; then
        echo "$STATIC_HEADER (HTTP $css_code)."
        echo "Pages render as unstyled HTML with no logo."
    fi
    echo
    if [ "$css_code" = "200" ] && [ -z "$carried_static" ]; then
        echo "FIX: cd $APP && scripts/install_units.sh"
    else
        echo "FIX: cd $APP && scripts/install.sh"
    fi
    echo "Run it in a terminal. It needs a password, which is exactly why the"
    echo "Update button cannot do it for you. See UPDATING.md."
} > "$MARKER" 2>/dev/null || true

echo >&2
echo "⚠ THIS INSTALL IS INCOMPLETE:" >&2
if [ "${#missing[@]}" -gt 0 ]; then
    echo "  • These system services are NOT installed:" >&2
    for u in "${missing[@]}"; do echo "      $u" >&2; done
    echo "    Whatever those services drive does not work: a missing .path unit" >&2
    echo "    means the matching in-app button queues a job nothing picks up and" >&2
    echo "    spins forever, and a missing .timer means that scheduled job never" >&2
    echo "    runs. Settings → Maintenance → Updates reports this too, and keeps" >&2
    echo "    reporting it until it is fixed." >&2
fi
if [ -n "$carried_static" ]; then
    printf '  • %s\n' "$carried_static" >&2
elif [ "$css_code" != "200" ]; then
    echo "  • $STATIC_HEADER (HTTP $css_code)." >&2
    echo "    Pages render as unstyled HTML with no logo." >&2
fi
echo >&2
# Units alone need only the unit installer. A static-permissions problem needs the
# full installer, which install_units.sh does not touch.
echo "  Fix it once. Safe to re-run over an existing install:" >&2
if [ "$css_code" = "200" ] && [ -z "$carried_static" ]; then
    echo "    cd $APP && scripts/install_units.sh" >&2
else
    echo "    cd $APP && scripts/install.sh" >&2
fi
echo >&2
echo "  It needs a password, so run it from a terminal. The in-app Update" >&2
echo "  button cannot do this itself. See UPDATING.md." >&2
echo >&2

exit 0
