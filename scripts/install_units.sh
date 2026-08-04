#!/usr/bin/env bash
# Render and install every systemd unit Murphy's Bench needs.
#
# The unit files in deploy/ are TEMPLATES: they carry __APP__, __RUN_USER__ and
# __RUN_GROUP__ placeholders rather than baked-in paths. This script substitutes
# the real values for THIS install and writes the results to /etc/systemd/system,
# then enables them.
#
# Why this exists: the units used to hardcode /opt/murphys-bench and User=scs-tech
# — the author's own server. On any other box the in-app "Back up now" and
# "Update" buttons queued a job that nothing ever picked up, and the backup
# schedule set in the UI never fired. Silently, in both cases. Deriving both
# values here means there is no path or username to keep in sync by hand.
#
# Run by scripts/install.sh. Safe and useful to run by hand at any time — after
# moving the install, renaming the app user, or pulling a release that changes a
# unit file. Re-running just re-renders and reloads.
#
# Usage: scripts/install_units.sh [--with-disk-check]
#   --with-disk-check   also install the disk-space check. Off by default: it
#                       needs send_alert configured (Settings -> Notifications)
#                       or it just fails loudly. The failure-alert unit itself is
#                       now ALWAYS installed and wired to the scheduled jobs — a
#                       backup that fails silently is the thing it exists to stop.
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ⚠ RUN_USER is WHOEVER RUNS THIS SCRIPT. That is deliberate — the script is
# meant to run as the app user and `sudo` for each individual write — but it
# means running the whole script as root would render every unit User=root.
# Anything privileged that ever invokes this must pass the app user in
# explicitly rather than letting `id -un` answer.
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"
UNIT_DIR=/etc/systemd/system

# The unit list, the enable list and the alert-hook jobs are NOT written here.
# They live in deploy/manifest.sh, which install.sh, verify_install.sh and
# update.sh read as well, so there is exactly one description of what a correct
# install contains.
MANIFEST="$APP/deploy/manifest.sh"
[ -f "$MANIFEST" ] || { echo "UNIT INSTALL FAILED: missing $MANIFEST — is this a complete checkout?" >&2; exit 1; }
# shellcheck source=../deploy/manifest.sh
. "$MANIFEST"

WITH_DISK_CHECK=0
for a in "$@"; do
  case "$a" in
    --with-disk-check) WITH_DISK_CHECK=1 ;;
    *) echo "install_units: unknown arg '$a'" >&2; exit 2 ;;
  esac
done

log()  { echo "$(date '+%F %T') units: $*"; }
fail() { echo "UNIT INSTALL FAILED: $*" >&2; exit 1; }

command -v systemctl >/dev/null || fail "this host has no systemd; \
Murphy's Bench's scheduled backups and in-app update need it. Wire up the \
equivalents for your init system using deploy/ as the reference."

# ⚠ LEGACY-COMPATIBILITY BLOCK — DO NOT "TIDY THIS AWAY", AND DO NOT EDIT BY HAND.
#
# Releases at or before v0.11.1 derive their expected unit list by awk-parsing
# THIS FILE for a literal `UNITS=(` ... `)` block. Their parser is already shipped
# and cannot be changed. When such a box updates forward to a release carrying
# deploy/manifest.sh, it reads this file with that parser, so the shape below is
# the only thing it will ever see.
#
# Verified on a real 24.04 box 2026-08-04, both ways:
#   - With no literal block, the parser found no `)` to stop at, swallowed the
#     rest of the script and wrote 269 lines of shell fragments into
#     logs/update-incomplete — which the Updates card renders verbatim as
#     "This install is incomplete" on a box that is perfectly healthy.
#   - An EMPTY literal block is WORSE, not better: that parser ends in
#     `grep -v '^$'`, which exits 1 on empty input, and the old update.sh runs
#     under `set -euo pipefail`. The update would FAIL outright.
#
# So it must be non-empty AND correct. `test_legacy_unit_block_matches_the_manifest`
# fails if this list and MB_UNITS ever disagree, so it cannot drift. Delete both
# the block and that test once no supported install predates the manifest.
UNITS=(
    murphys-bench.service
    'murphys-bench-alert@.service'
    murphys-bench-update.path
    murphys-bench-update.service
    murphys-bench-backup-now.path
    murphys-bench-backup-now.service
    murphys-bench-restore.path
    murphys-bench-restore.service
    murphys-bench-backup.timer
    murphys-bench-backup.service
    murphys-bench-fetch-email.timer
    murphys-bench-fetch-email.service
    murphys-bench-sla-check.timer
    murphys-bench-sla-check.service
)

# The real assignment. Everything this script actually does uses the manifest.
UNITS=("${MB_UNITS[@]}")
ENABLE=("${MB_UNITS_ENABLE[@]}")
if [ "$WITH_DISK_CHECK" = 1 ]; then
    UNITS+=("${MB_UNITS_OPTIONAL[@]}")
    ENABLE+=("${MB_UNITS_OPTIONAL_ENABLE[@]}")
fi

log "rendering ${#UNITS[@]} units for APP=$APP USER=$RUN_USER (sudo)..."
for u in "${UNITS[@]}"; do
    src="$APP/deploy/$u"
    [ -f "$src" ] || fail "missing unit template $src — is this a complete checkout?"
    sed -e "s|__APP__|$APP|g" \
        -e "s|__RUN_USER__|$RUN_USER|g" \
        -e "s|__RUN_GROUP__|$RUN_GROUP|g" "$src" \
      | sudo tee "$UNIT_DIR/$u" >/dev/null || fail "could not write $UNIT_DIR/$u"
done

# Guard against shipping a template with a placeholder we forgot to substitute —
# systemd would accept the file and the unit would fail at run time, which is
# exactly the silent-failure shape this whole change exists to remove.
for u in "${UNITS[@]}"; do
    if sudo grep -q '__APP__\|__RUN_USER__\|__RUN_GROUP__' "$UNIT_DIR/$u"; then
        fail "$u still contains an unsubstituted placeholder after rendering"
    fi
done

# Log rotation is part of the deployment layer too, and was missing from it for
# the same reason the units were: deploy/README.md told a human to `sudo cp` a
# file that hardcoded one box's path and username. Every other install rotated
# nothing and grew a gunicorn access log forever. Rendered from the same template
# mechanism as the units, so it can never drift from this install's real path.
if command -v logrotate >/dev/null; then
    lr_src="$APP/deploy/logrotate-murphys-bench"
    if [ -f "$lr_src" ]; then
        sed -e "s|__APP__|$APP|g" \
            -e "s|__RUN_USER__|$RUN_USER|g" \
            -e "s|__RUN_GROUP__|$RUN_GROUP|g" "$lr_src" \
          | sudo tee /etc/logrotate.d/murphys-bench >/dev/null \
          || fail "could not write /etc/logrotate.d/murphys-bench"
        # logrotate SKIPS a config it considers unsafe, and does it quietly.
        sudo chmod 0644 /etc/logrotate.d/murphys-bench
        # Parse it now rather than discovering a syntax error weeks later, when a
        # log that should have rotated has quietly filled the disk instead.
        if sudo logrotate -d /etc/logrotate.d/murphys-bench >/dev/null 2>&1; then
            log "log rotation installed (/etc/logrotate.d/murphys-bench)"
        else
            fail "the rendered logrotate config is invalid — logs would never rotate.
  Inspect: sudo logrotate -d /etc/logrotate.d/murphys-bench"
        fi
    fi
else
    log "logrotate not installed — MB's gunicorn/backup/update logs will NOT be rotated.
       Install it (apt install logrotate) and re-run this script."
fi

# Failure reporting for the scheduled jobs.
#
# Installing the alert unit is not enough: a job only reports its own failure if
# it carries OnFailure=. That wiring was a copy-paste block in deploy/README.md,
# so on every box but the author's, MB's self-monitoring — the feature whose
# entire purpose is to make silent failures visible — was itself silently absent.
# A nightly backup could fail forever and say nothing.
#
# %N expands to the failed unit's name, so one template serves every job. The
# job list comes from the manifest — verify_install.sh checks the same names.
for u in "${MB_ALERT_HOOK_JOBS[@]}"; do
    sudo mkdir -p "$UNIT_DIR/$u.service.d" || fail "could not create $UNIT_DIR/$u.service.d"
    sudo tee "$UNIT_DIR/$u.service.d/onfailure.conf" >/dev/null <<'DROPIN' || fail "could not write the $u failure hook"
# Written by scripts/install_units.sh. A failure here opens a System Alert
# ticket (admin-visible only) rather than passing silently.
[Unit]
OnFailure=murphys-bench-alert@%N.service
DROPIN
done
log "failure alerts wired for the backup, inbound-email and SLA jobs"

sudo systemctl daemon-reload || fail "systemctl daemon-reload failed"

for u in "${ENABLE[@]}"; do
    sudo systemctl enable --now "$u" || fail "enabling $u failed — see: journalctl -u $u"
done

log "installed ${#UNITS[@]} units, enabled ${#ENABLE[@]}:"
for u in "${ENABLE[@]}"; do
    printf '  %-38s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null || true)"
done
if [ "$WITH_DISK_CHECK" = 0 ]; then
    log "disk-space check not installed (pass --with-disk-check once alerts are configured)"
fi
