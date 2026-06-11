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

## Inbound email polling (every 2 min) + SLA overdue check (every 15 min)

Files: `murphys-bench-fetch-email.{service,timer}` (runs `manage.py
fetch_inbound_email`) and `murphys-bench-sla-check.{service,timer}` (runs
`manage.py check_sla_overdue`). Both timers use `OnUnitActiveSec` so the next
run is scheduled after the previous one finishes (no overlap).

Install on the VM:

```bash
sudo cp /opt/murphys-bench/deploy/murphys-bench-fetch-email.service /etc/systemd/system/
sudo cp /opt/murphys-bench/deploy/murphys-bench-fetch-email.timer   /etc/systemd/system/
sudo cp /opt/murphys-bench/deploy/murphys-bench-sla-check.service   /etc/systemd/system/
sudo cp /opt/murphys-bench/deploy/murphys-bench-sla-check.timer     /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now murphys-bench-fetch-email.timer
sudo systemctl enable --now murphys-bench-sla-check.timer
sudo systemctl list-timers 'murphys-bench-*'   # confirm both are scheduled
```

Watch inbound email actually run:

```bash
journalctl -u murphys-bench-fetch-email.service -f   # live; Ctrl-C to stop
```

Note: inbound polling only does something when inbound email is enabled and
configured in Settings, and pointed at the real support mailbox (not a test one).
