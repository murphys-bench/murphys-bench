#!/usr/bin/env bash
# Murphy's Bench backup scheduler tick.
#
# Run every 5 minutes by murphys-bench-backup.timer. Each destination (onsite,
# offsite) has its OWN schedule in the app-rendered backup-config.env. This tick
# works out which enabled destinations are due right now and, if any, fires one
# backup targeting exactly those (mb_backup.sh --only ...). If both are due at the
# same tick, a single snapshot is shipped to both.
#
# Privilege-free: the app only rewrites backup-config.env; schedule changes take
# effect on the next tick. Backups fire within ~5 min of the configured HH:MM.
set -uo pipefail

APP=/opt/murphys-bench
today="$(date +%F)"
now_day="$(date +%a | tr '[:upper:]' '[:lower:]')"   # mon,tue,...
now_hm="$(date +%H:%M)"
now_min=$(( 10#${now_hm%%:*} * 60 + 10#${now_hm##*:} ))

BACKUP_ONSITE_ENABLED=0;  BACKUP_ONSITE_SCHEDULE_DAYS="daily";  BACKUP_ONSITE_SCHEDULE_TIMES=""
BACKUP_OFFSITE_ENABLED=0; BACKUP_OFFSITE_SCHEDULE_DAYS="daily"; BACKUP_OFFSITE_SCHEDULE_TIMES=""
# shellcheck disable=SC1090
[ -f "$APP/backup-config.env" ] && . "$APP/backup-config.env"

# due DEST DAYS TIMES → prints the matching slot "HH:MM" if a run is due now for
# this destination and that slot hasn't already fired today; else prints nothing.
due(){
    local dest="$1" days="$2" times="$3" mark="$APP/logs/backup-last-run-$1"
    case ",${days}," in
        ,daily,) : ;;
        *",${now_day},"*) : ;;
        *) return 0 ;;
    esac
    local t sched_min diff
    IFS=',' read -ra TIMES <<< "$times"
    for t in "${TIMES[@]}"; do
        t="$(echo "$t" | tr -d '[:space:]')"; [ -z "$t" ] && continue
        sched_min=$(( 10#${t%%:*} * 60 + 10#${t##*:} ))
        diff=$(( now_min - sched_min ))
        if [ "$diff" -ge 0 ] && [ "$diff" -lt 5 ]; then
            [ "$(cat "$mark" 2>/dev/null || true)" = "$today $t" ] && return 0
            echo "$today $t" > "$mark"
            printf '%s' "$t"; return 0
        fi
    done
}

WANT=()
[ "${BACKUP_ONSITE_ENABLED:-0}" = "1" ]  && [ -n "$(due onsite  "$BACKUP_ONSITE_SCHEDULE_DAYS"  "$BACKUP_ONSITE_SCHEDULE_TIMES")"  ] && WANT+=("onsite")
[ "${BACKUP_OFFSITE_ENABLED:-0}" = "1" ] && [ -n "$(due offsite "$BACKUP_OFFSITE_SCHEDULE_DAYS" "$BACKUP_OFFSITE_SCHEDULE_TIMES")" ] && WANT+=("offsite")

[ "${#WANT[@]}" -gt 0 ] || exit 0
only="$(IFS=','; echo "${WANT[*]}")"
exec "$APP/scripts/mb_backup.sh" --only "$only"
