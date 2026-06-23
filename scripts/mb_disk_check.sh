#!/usr/bin/env bash
# Alert (-> System Alert ticket) if the root filesystem is at/over THRESHOLD%.
# Run by murphys-bench-disk-check.timer. Usage: mb_disk_check.sh [threshold%]
set -euo pipefail

APP=/opt/murphys-bench
THRESHOLD="${1:-85}"
USE="$(df --output=pcent / | tail -1 | tr -dc '0-9')"

if [ "${USE:-0}" -ge "$THRESHOLD" ]; then
    "$APP/venv/bin/python" "$APP/manage.py" send_alert \
        "Disk ${USE}% on $(hostname)" \
        "Root filesystem at ${USE}% (threshold ${THRESHOLD}%). Investigate: df -h / ; du -sh $APP/logs $APP/backups"
fi
