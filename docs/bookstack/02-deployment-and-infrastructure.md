# Murphy's Bench — Deployment & Infrastructure

> The "where does everything live" page. Start here when something is down.

## The production host

| Property | Value |
|---|---|
| Platform | Ubuntu 24.04 VM on Proxmox |
| IP | `10.58.58.82` (LAN only — no public domain yet) |
| Protocol | HTTP (plain, on the LAN) |
| App path | `/opt/murphys-bench/` |
| Python | 3.12 (Ubuntu 24.04 default), venv at `/opt/murphys-bench/venv/` |
| Database | SQLite (`db.sqlite3` on the VM) |
| App server | Gunicorn |
| Web server | Nginx (reverse proxy in front of Gunicorn) |

> ⚠️ **App path is `/opt/murphys-bench/`, NOT `~/murphys-bench/`.**
> ⚠️ **Production Python is `python3`, never `python`.** Use the venv: `/opt/murphys-bench/venv/bin/python`.

## The other two instances

Production isn't the only box. There are three Murphy's Bench instances, each with a distinct job:

| Instance | IP / host | Role | Data | Exposure |
|---|---|---|---|---|
| **prod** | `10.58.58.82` | Live SCS system | Real client data | LAN-only, plain HTTP |
| **mb-test** (staging) | `10.58.58.108` (Proxmox VMID 201) | Pre-prod gate — deploy + migrate here first | **A copy of prod data under prod's `FIELD_ENCRYPTION_KEY`**; outbound integrations neutralized | LAN-only |
| **MB2** (demo) | `10.58.35.223` | External demo for testers | **Fake data only** | Public via Cloudflare Tunnel at `https://mbdemo.scs-tech.net`, behind Cloudflare Access |

> ⚠️ **`mb-test` holds REAL client data** (a faithful copy, for honest migration testing) — keep it LAN-only and **never** repurpose it as a demo. **MB2 is the only fake-data, internet-facing box.**
>
> **Deploy order is always `mb-test` → verify → prod → MB2** (see *Development & Deploy Workflow*).

## SSH access

```bash
ssh -i ~/.ssh/id_ed25519 scs-tech@10.58.58.82
```

The `scs-tech` user has **NOPASSWD sudo for restart/status of the app service only** — not general sudo. Anything else (installing systemd unit files, etc.) needs an interactive sudo session.

## The Gunicorn service

> ⚠️ The service is **`murphys-bench.service`**, NOT `gunicorn.service`.

```bash
sudo systemctl restart murphys-bench      # restart after a deploy
sudo systemctl status  murphys-bench      # check it's running
journalctl -u murphys-bench -f            # tail app server logs
```

`scs-tech` can run `restart` and `status` for this service without a password.

## systemd timers (background jobs)

This VM has **no cron** — all scheduled work runs as systemd timers. Unit files live in the repo at `/opt/murphys-bench/deploy/` and are installed into `/etc/systemd/system/`.

| Timer | Cadence | Purpose |
|---|---|---|
| `murphys-bench-backup.timer` | nightly 02:15 | SQLite snapshot + files → Backblaze B2 (immutable) |
| inbound email fetch | every 2 min | poll mailbox → tickets/replies |
| SLA check | every 15 min | flag overdue tickets |

```bash
systemctl list-timers 'murphys-bench*'    # see all MB timers + next run
```

All three are installed, enabled and active. See *Operations & Maintenance* for details.

## Environment / secrets (`.env`)

The production `.env` lives in the app directory and holds the real secrets. Critical keys:

| Variable | Notes |
|---|---|
| `DEBUG` | **`False`** in production (defaults False; local dev sets `True`) |
| `SECRET_KEY` | real value — startup refuses to boot with the committed default when `DEBUG=False` |
| `FIELD_ENCRYPTION_KEY` | AES key for encrypted fields — **also stored in Bitwarden**. Without it, encrypted data is unreadable. |

Other settings (SMTP, inbound mailbox, Google Maps API key, shop address, colours) live in the **database** (`SiteSettings` singleton), editable from **Settings** in the app — not in `.env`.

> 🔑 **The `FIELD_ENCRYPTION_KEY` is the single most important secret.** A database backup contains only *encrypted ciphertext*; a restore is useless without this key. See *Backup & Disaster Recovery*.

## HTTPS / public access — deliberately deferred

The app is served over **plain HTTP on the LAN**. This is intentional for now. `manage.py check --deploy` shows 4 HTTPS warnings (HSTS, SSL redirect, secure session cookie, secure CSRF cookie) — **correct to leave off** until HTTPS is end-to-end, or internal access breaks.

When the Cloudflare tunnel goes live, flip these together in the production `.env`:

```ini
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000      # only once HTTPS is confirmed everywhere — HSTS is hard to undo
```

…then add the public hostname to `ALLOWED_HOSTS`, set `CSRF_TRUSTED_ORIGINS=https://<hostname>`, and re-run `manage.py check --deploy` (should come back clean).

## Source of truth

| Thing | Location |
|---|---|
| Code | GitHub private repo + local Mac at `~/Documents/Claude/murphys-bench` |
| Full developer notes | `CLAUDE.md` in the repo |
| Roadmap | `TODO.md` in the repo |
| Backups | `/opt/murphys-bench/backups/` (gitignored, 14-day rotation) + Proxmox VM snapshots |
