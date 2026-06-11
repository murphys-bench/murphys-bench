# Deployment units

systemd units for scheduled Murphy's Bench jobs. This VM has no `cron`, so
scheduling uses systemd timers. Installing them is a one-time `sudo` step.

## Nightly database backup

Files: `murphys-bench-backup.service` (runs `scripts/backup_db.sh`) and
`murphys-bench-backup.timer` (fires 02:15 nightly, `Persistent=true` so a
missed run catches up after downtime).

Install on the VM:

```bash
sudo cp /opt/murphys-bench/deploy/murphys-bench-backup.service /etc/systemd/system/
sudo cp /opt/murphys-bench/deploy/murphys-bench-backup.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now murphys-bench-backup.timer
sudo systemctl list-timers murphys-bench-backup.timer   # confirm next run time
```

Verify a manual run and inspect output:

```bash
sudo systemctl start murphys-bench-backup.service
journalctl -u murphys-bench-backup.service --no-pager | tail
ls -lh /opt/murphys-bench/backups/
```

## Still unscheduled (separate from backups — decide and add when ready)

`fetch_inbound_email` and `check_sla_overdue` are **not** currently scheduled
on this VM (no cron, no timers). Until they are, inbound email is not polled
automatically and SLA overdue counts are not refreshed on a schedule. Add
equivalent `.service` + `.timer` pairs (e.g. every 2 min for inbound email,
every 15 min for the SLA check) following the same pattern as the backup units.
