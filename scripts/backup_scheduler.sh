#!/usr/bin/env bash
# Murphy's Bench backup scheduler tick.
#
# Run every 5 minutes by murphys-bench-backup.timer. Reads the app-rendered
# schedule (Settings → Maintenance → Backups) from backup-config.env and, if a
# scheduled slot is due now and hasn't already run today, launches mb_backup.sh.
#
# This keeps scheduling privilege-free: the app only rewrites backup-config.env,
# never touches systemd. Schedule changes take effect on the next tick. Backups
# fire within ~5 min of the configured HH:MM.
set -uo pipefail

APP=/opt/murphys-bench
MARK="$APP/logs/backup-last-run"   # stores "YYYY-MM-DD HH:MM" of the last fired slot

BACKUP_SCHEDULE_DAYS="daily"
BACKUP_SCHEDULE_TIMES="02:00"
# shellcheck disable=SC1090
[ -f "$APP/backup-config.env" ] && . "$APP/backup-config.env"

now_day="$(date +%a | tr '[:upper:]' '[:lower:]')"   # mon,tue,...
now_hm="$(date +%H:%M)"
today="$(date +%F)"

# Day gate: 'daily' always matches; otherwise the CSV must contain today's token.
case ",${BACKUP_SCHEDULE_DAYS}," in
    ,daily,) : ;;
    *",${now_day},"*) : ;;
    *) exit 0 ;;
esac

# Time gate: is the current HH:MM one of the configured slots (within this tick)?
slot_due=""
IFS=',' read -ra TIMES <<< "$BACKUP_SCHEDULE_TIMES"
for t in "${TIMES[@]}"; do
    t="$(echo "$t" | tr -d '[:space:]')"
    [ -z "$t" ] && continue
    # Match if the scheduled minute is within the last 5 minutes (the tick window),
    # so a slot at 02:00 fires on the 02:00–02:04 tick even if cron is a bit late.
    sched_min=$(( 10#${t%%:*} * 60 + 10#${t##*:} ))
    now_min=$(( 10#${now_hm%%:*} * 60 + 10#${now_hm##*:} ))
    diff=$(( now_min - sched_min ))
    if [ "$diff" -ge 0 ] && [ "$diff" -lt 5 ]; then slot_due="$t"; break; fi
done
[ -n "$slot_due" ] || exit 0

# Dedupe: don't fire the same slot twice today.
last="$(cat "$MARK" 2>/dev/null || true)"
if [ "$last" = "$today $slot_due" ]; then exit 0; fi
echo "$today $slot_due" > "$MARK"

exec "$APP/scripts/mb_backup.sh"
