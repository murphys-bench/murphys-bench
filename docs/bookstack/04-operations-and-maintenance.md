# Murphy's Bench — Operations & Maintenance

> Day-to-day running of the system: scheduled jobs, the mailbox, logs, and admin tasks.

## Scheduled jobs (systemd timers)

This VM has **no cron**. All recurring work runs as systemd timers, installed from `/opt/murphys-bench/deploy/`.

| Timer | Cadence | What it does |
|---|---|---|
| inbound email fetch (`fetch_inbound_email`) | every 2 min | Polls the support mailbox; new mail becomes tickets, replies thread into existing tickets |
| SLA check (`check_sla_overdue`) | every 15 min | Flags tickets that have blown their SLA deadline (in-app alerts) |
| backup (`murphys-bench-backup`) | 5-min tick, fires per-destination schedule | SQLite snapshot + files → onsite (SMB) and/or offsite (S3), per Settings → Maintenance → Backups |

Check status / next run:

```bash
systemctl list-timers 'murphys-bench*'
journalctl -u murphys-bench-backup        # backup history
```

Run a job manually (useful for testing):

```bash
cd /opt/murphys-bench
venv/bin/python manage.py fetch_inbound_email
venv/bin/python manage.py check_sla_overdue
```

### Installing / reinstalling a timer (one-time, needs interactive sudo)

```bash
sudo cp /opt/murphys-bench/deploy/murphys-bench-backup.service /etc/systemd/system/
sudo cp /opt/murphys-bench/deploy/murphys-bench-backup.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now murphys-bench-backup.timer
sudo systemctl list-timers murphys-bench-backup.timer
```

## The inbound mailbox

Inbound mail is fetched over **POP3 with delete-from-server** (switched from IMAP to stop a duplication bug). The mailbox, credentials, and protocol are configured in **Settings → Inbound Email**.

> ⚠️ **Tradeoff of POP3 delete-from-server:** MB becomes the *only* copy of inbound mail — there is no copy left on the mail server. The nightly DB backup is therefore also your inbound-mail backup.

> 📌 **Open action:** inbound is currently pointed at `testing@shamrockcomputerservices.com`. Switch it to the **real support inbox** in Settings → Inbound Email once you're confident, so customer emails become tickets automatically.

How threading works: the fetcher reads the `[TKT-…]` token in the subject line (it does **not** rely on `In-Reply-To`/`References` headers). A subject-matched reply **always** threads into its ticket:

- A reply to a **converted** ticket stays `converted` (just flagged "needs response") — never un-converts a live work order.
- A reply to a **closed** ticket **reopens** it to `open`.

## Logs

| Log | Where |
|---|---|
| App server (Gunicorn) | `journalctl -u murphys-bench` |
| Application log | `murphys_bench.log` (in the app dir) — email/inbound failures land here |
| Inbound mail audit | `InboundEmailLog` (in-app, every fetched message) |
| Outbound mail audit | `EmailSendLog` (in-app, every send attempt) |
| Timer runs | `journalctl -u <timer-unit>` |

Email and inbound failures **log loudly** (to the `core` logger / `murphys_bench.log`) rather than failing silently — by design.

## Admin tasks (all in the native app, not Django admin)

Almost everything is now in the native **Settings** UI (`/settings/`, admin only). Django admin (`/admin/`) is a **break-glass tool only**.

Native Settings covers: Company info, Outbound/Inbound Email, Email Templates + branding + signatures, Attachments, Security/MFA, Mileage, Statuses + colours, Help Topics, SLA Plans, Repair Types, Canned Responses, Quick Labor, Checklist Items, KB Categories, Dashboard Tiles, Custom Fields, Blocked Senders, Org Credentials vault, **Users** and **Roles**.

What still requires Django admin (by design):

- Superuser / `is_staff` flag management (can't self-escalate in native UI).
- Emergency fixes for records stuck in a bad state.

## Wiping operational data (OSTicket-cutover clean slate)

Use the purpose-built command — **never `manage.py flush`** (which also destroys configuration).

```bash
# Dry run (default — shows what WOULD be deleted, changes nothing):
venv/bin/python manage.py reset_operational_data

# Destructive run — requires the exact confirmation phrase:
venv/bin/python manage.py reset_operational_data --confirm "DELETE ALL OPERATIONAL DATA"

# Optionally keep specific non-superusers:
venv/bin/python manage.py reset_operational_data --confirm "DELETE ALL OPERATIONAL DATA" --keep-users alice,bob
```

It deletes operational data (clients, contacts, devices, tickets, work orders, mileage, attachments + files, logs, non-superuser users) in a single transaction, while **keeping** all configuration (settings, roles, statuses, help topics, SLA plans, repair types, checklists, canned responses, templates, tiles, custom-field *definitions*, KB, org credentials) and **all superusers**.

## Health checklist when something's wrong

1. Service up? `sudo systemctl status murphys-bench`
2. App errors? `journalctl -u murphys-bench -f` then reproduce.
3. Mail not arriving as tickets? Run `fetch_inbound_email` manually and watch output; check Settings → Inbound Email points at the right mailbox.
4. Overdue flags missing? Check the SLA timer in `systemctl list-timers`.
5. Config check: `cd /opt/murphys-bench && venv/bin/python manage.py check`.
