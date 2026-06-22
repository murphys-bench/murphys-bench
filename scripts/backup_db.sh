#!/usr/bin/env bash
# RETIRED 2026-06-22: the old pg_dump logic backed up an EMPTY Postgres DB
# (prod actually runs on SQLite). Original saved as backup_db.sh.pgdump.retired-*.
# This now delegates to the real backup script so the existing systemd timer
# (murphys-bench-backup.timer, nightly 02:15) keeps working unchanged.
exec /opt/murphys-bench/scripts/mb_backup.sh
