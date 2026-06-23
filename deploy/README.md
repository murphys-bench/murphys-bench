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

## Observability — failures become tickets

MB self-monitors: its own operational failures open a **System Alert ticket**
(a dedicated "System Alerts" client + the admin notification bell) via
`manage.py send_alert` / `core.system_alerts.create_system_alert`. This is used
instead of email because the box can't send system mail. App 500s also auto-open
a ticket (`core.log_handlers.SystemAlertHandler` on the `django.request` logger,
production only — wired in `settings.LOGGING`).

Install the log rotation, the OnFailure→ticket handler, and the disk check:

```bash
sudo apt install -y logrotate     # not present on minimal Ubuntu installs
sudo cp /opt/murphys-bench/deploy/logrotate-murphys-bench /etc/logrotate.d/murphys-bench
sudo cp /opt/murphys-bench/deploy/murphys-bench-alert@.service /etc/systemd/system/
sudo cp /opt/murphys-bench/deploy/murphys-bench-disk-check.service /etc/systemd/system/
sudo cp /opt/murphys-bench/deploy/murphys-bench-disk-check.timer   /etc/systemd/system/
for u in murphys-bench-backup murphys-bench-fetch-email murphys-bench-sla-check; do
  sudo mkdir -p /etc/systemd/system/$u.service.d
  sudo tee /etc/systemd/system/$u.service.d/onfailure.conf >/dev/null <<'DROPIN'
[Unit]
OnFailure=murphys-bench-alert@%N.service
DROPIN
done
sudo systemctl daemon-reload
sudo systemctl enable --now murphys-bench-disk-check.timer
```

### Backup dead-man's-switch (healthchecks.io)

The backup's *liveness* (catching a job that never runs) is covered by an external
heartbeat. Create a free check at healthchecks.io and add its ping URL to `.env`:

```ini
HEALTHCHECKS_URL=https://hc-ping.com/<your-check-uuid>
```

`scripts/mb_backup.sh` pings it on success and `<url>/fail` on failure; a missed
ping makes healthchecks.io email you. Leave `HEALTHCHECKS_URL` unset to disable.
