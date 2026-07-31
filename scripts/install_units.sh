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
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"
UNIT_DIR=/etc/systemd/system

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

# Units that are always installed. The two .path units are what make the in-app
# "Back up now" and "Update" buttons do anything at all — without them the UI
# spins forever. The three timers are inert until the matching feature is
# configured in the app, but must exist BEFORE that, because the web process
# deliberately has no privilege to enable a systemd unit itself.
UNITS=(
    murphys-bench.service              # gunicorn
    'murphys-bench-alert@.service'     # turns a failed job into a System Alert ticket
    murphys-bench-update.path          # in-app Update button
    murphys-bench-update.service
    murphys-bench-backup-now.path      # in-app "Back up now" button
    murphys-bench-backup-now.service
    murphys-bench-backup.timer         # scheduled backups (per in-app schedule)
    murphys-bench-backup.service
    murphys-bench-fetch-email.timer    # inbound email -> tickets
    murphys-bench-fetch-email.service
    murphys-bench-sla-check.timer      # overdue-SLA sweep
    murphys-bench-sla-check.service
)
[ "$WITH_DISK_CHECK" = 1 ] && UNITS+=(
    murphys-bench-disk-check.timer
    murphys-bench-disk-check.service
)

# Units systemd should actually start. Plain .service units named by a .path or
# .timer are launched by their trigger and must NOT be enabled themselves.
ENABLE=(
    murphys-bench.service
    murphys-bench-update.path
    murphys-bench-backup-now.path
    murphys-bench-backup.timer
    murphys-bench-fetch-email.timer
    murphys-bench-sla-check.timer
)
[ "$WITH_DISK_CHECK" = 1 ] && ENABLE+=(murphys-bench-disk-check.timer)

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
# %N expands to the failed unit's name, so one template serves every job.
for u in murphys-bench-backup murphys-bench-fetch-email murphys-bench-sla-check; do
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
