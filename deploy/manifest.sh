#!/usr/bin/env bash
# Murphy's Bench deployment manifest — the single description of what a correct
# install contains.
#
# WHY THIS FILE EXISTS
#
# The end state used to be written down in several places at once: install.sh
# encoded it as ordered steps, install_units.sh kept its own unit list,
# verify_install.sh asserted a second copy of that list, and update.sh checked a
# third. Every deployment defect this summer was one of those descriptions
# disagreeing with another, and the disagreements were invisible because each
# file looked correct on its own. Concrete example found while writing this:
# install.sh INSTALLED five sudo verbs, its own fallback instructions named
# three, and verify_install.sh checked three — so a box missing two verbs passed
# the clean-room gate.
#
# Everything below is data, sourced by the scripts that act on it. There is no
# parser and no second reader: adding a unit here is what installs it, enables
# it, verifies it and reports it missing after an update.
#
# RULES FOR EDITING
#
#  1. This file is DECLARATIONS ONLY. No commands, no side effects, no `set -e`.
#     It gets sourced by scripts that are mid-flight; it must not change their
#     shell state or exit on their behalf.
#  2. Nothing goes in here until something READS it. An unread entry is a third
#     artifact to keep in step, which is the problem this file exists to remove.
#     (Mutable-state paths belong here eventually — they are deliberately absent
#     until the code that owns backup/restore/update paths reads them.)
#  3. Adding a unit template to deploy/ without listing it here FAILS the test
#     suite. That is the one kind of drift a shared file cannot prevent by
#     construction, so it is covered by a test instead.

# ---------------------------------------------------------------------------
# systemd units
# ---------------------------------------------------------------------------

# Installed on every box. The three .path units are what make the in-app
# "Back up now", "Update" and "Restore" buttons do anything at all — without
# them the UI spins forever. The three timers are inert until the matching
# feature is configured in the app, but must exist BEFORE that, because the web
# process deliberately holds no privilege to enable a unit itself.
MB_UNITS=(
    murphys-bench.service              # gunicorn
    'murphys-bench-alert@.service'     # turns a failed job into a System Alert ticket
    murphys-bench-update.path          # in-app Update button
    murphys-bench-update.service
    murphys-bench-backup-now.path      # in-app "Back up now" button
    murphys-bench-backup-now.service
    murphys-bench-restore.path         # in-app Restore button
    murphys-bench-restore.service
    murphys-bench-backup.timer         # scheduled backups (per in-app schedule)
    murphys-bench-backup.service
    murphys-bench-fetch-email.timer    # inbound email -> tickets
    murphys-bench-fetch-email.service
    murphys-bench-sla-check.timer      # overdue-SLA sweep
    murphys-bench-sla-check.service
)

# Units systemd should actually start. A plain .service named by a .path or a
# .timer is launched by its trigger and must NOT be enabled itself.
MB_UNITS_ENABLE=(
    murphys-bench.service
    murphys-bench-update.path
    murphys-bench-backup-now.path
    murphys-bench-restore.path
    murphys-bench-backup.timer
    murphys-bench-fetch-email.timer
    murphys-bench-sla-check.timer
)

# Opt-in (scripts/install_units.sh --with-disk-check). Off by default because it
# needs send_alert configured or it just fails loudly. Kept separate so the
# update-time drift check never reports these missing on a box that
# deliberately does not have them.
MB_UNITS_OPTIONAL=(
    murphys-bench-disk-check.timer
    murphys-bench-disk-check.service
)
MB_UNITS_OPTIONAL_ENABLE=(
    murphys-bench-disk-check.timer
)

# Scheduled jobs that get an OnFailure= drop-in pointing at murphys-bench-alert@.
# A backup that fails silently is the thing this exists to stop. Named without
# the .service suffix; the drop-in directory is <name>.service.d.
MB_ALERT_HOOK_JOBS=(
    murphys-bench-backup
    murphys-bench-fetch-email
    murphys-bench-sla-check
)

# ---------------------------------------------------------------------------
# Privilege
# ---------------------------------------------------------------------------

# The ONLY commands the app user may run as root without a password, and only
# against murphys-bench itself.
#
# ⚠ DO NOT TRIM THIS LIST. `stop` and `start` are not decoration: rollback runs
# restore.sh, which stops the service, restores the database and starts it
# again. Granting `restart` alone leaves the recovery path broken in exactly the
# situation it exists for. `status`/`is-active` are how the app reports on its
# own service without a shell.
#
# ⚠ This is a statement about the sudoers FILE, not about the account. An
# install whose service account is also an administrator (a person's own login
# doing double duty) has far more than this, and MB cannot see that.
MB_SUDO_VERBS=(
    restart
    stop
    start
    status
    is-active
)

# ---------------------------------------------------------------------------
# System packages
# ---------------------------------------------------------------------------

# The libpango/cairo/ft2 stack + fonts are WeasyPrint's runtime deps (PDF
# generation for repair reports and quotes); they pull cairo/glib/harfbuzz.
MB_APT_PACKAGES=(
    python3 python3-venv python3-pip git logrotate curl
    libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core
    rclone
)

# Installed ONLY when MB manages the web layer. Deliberately conditional:
# Ubuntu's nginx package starts and enables itself, so installing it under
# --skip-web left an active nginx serving its default welcome page on port 80,
# contending with the operator's own proxy — on the one flag whose entire
# promise is "don't touch nginx".
MB_APT_PACKAGES_WEB=(
    nginx
)
