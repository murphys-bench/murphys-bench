#!/usr/bin/env bash
#
# Nightly logical backup of the Murphy's Bench PostgreSQL database.
#
# Complements the whole-VM Proxmox snapshots with a portable, restorable,
# compressed SQL dump. Run from cron (see install note at the bottom).
#
# SECURITY NOTE: this dump contains the *encrypted* ciphertext of credential
# fields, not the FIELD_ENCRYPTION_KEY. To actually restore working credentials
# you also need that key (kept in Bitwarden). The dump alone is therefore safe
# to copy off-box; a restore needs dump + key together.
#
set -euo pipefail

APP_DIR="/opt/murphys-bench"
BACKUP_DIR="$APP_DIR/backups"
KEEP_DAYS=14

mkdir -p "$BACKUP_DIR"

# Pull DB credentials from the app's .env (single source of truth).
env_val() { grep -E "^$1=" "$APP_DIR/.env" | head -1 | cut -d= -f2-; }
DB_NAME="$(env_val DB_NAME)"
DB_USER="$(env_val DB_USER)"
DB_PASSWORD="$(env_val DB_PASSWORD)"
DB_HOST="$(env_val DB_HOST)"

STAMP="$(date +%Y%m%d_%H%M%S)"
OUT="$BACKUP_DIR/mb_${STAMP}.sql.gz"

PGPASSWORD="$DB_PASSWORD" /usr/bin/pg_dump \
    -h "${DB_HOST:-localhost}" -U "$DB_USER" "$DB_NAME" \
    | /usr/bin/gzip > "$OUT"

# Rotate: drop dumps older than KEEP_DAYS so the directory doesn't grow forever.
find "$BACKUP_DIR" -name 'mb_*.sql.gz' -mtime +"$KEEP_DAYS" -delete

echo "$(date '+%F %T') backup OK -> $OUT ($(du -h "$OUT" | cut -f1))"

# ── Scheduling ──────────────────────────────────────────────────────────────
# This VM has no cron. Scheduling is via systemd timer — see deploy/README.md
# for the murphys-bench-backup.{service,timer} units and the one-time sudo
# install. Output is captured by journalctl (journalctl -u murphys-bench-backup).
