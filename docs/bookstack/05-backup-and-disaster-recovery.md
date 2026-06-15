# Murphy's Bench — Backup & Disaster Recovery

> Read this **before** you need it. The single biggest gotcha: a database backup is useless without the encryption key.

## What protects the data

| Layer | What it covers | Cadence |
|---|---|---|
| Nightly `pg_dump` | Full PostgreSQL database (gzipped) | 02:15 nightly, 14-day rotation |
| Proxmox VM snapshots | Whole VM (OS, app, DB, `.env`) | per Proxmox schedule |
| GitHub | All application code | every push |
| Bitwarden | `FIELD_ENCRYPTION_KEY` (and other secrets) | manual |

The `pg_dump` and the Proxmox snapshots are complementary: the dump is a small, portable, point-in-time DB copy; the snapshot restores the entire machine.

## The nightly database backup

- Script: `/opt/murphys-bench/scripts/backup_db.sh`
- Output: `/opt/murphys-bench/backups/` (gitignored), gzip-compressed, 14-day rotation
- Scheduled by `murphys-bench-backup.timer` (systemd, `Persistent=true` so a missed run catches up)

Check it's running and view history:

```bash
systemctl list-timers murphys-bench-backup.timer
journalctl -u murphys-bench-backup
ls -lh /opt/murphys-bench/backups/
```

Run a backup on demand:

```bash
/opt/murphys-bench/scripts/backup_db.sh
```

## 🔑 The encryption-key dependency (critical)

Several fields are **AES-256 encrypted at rest** (device credentials, org-vault credentials, stored mail passwords). The database dump contains only the **encrypted ciphertext** — *not* the key.

> **A restore needs TWO things: the database dump *and* the `FIELD_ENCRYPTION_KEY`.**
> The key lives in the production `.env` and is also stored in **Bitwarden**. If you lose the key, every encrypted field becomes permanently unreadable, even from a perfect backup.

When restoring to a fresh host, set `FIELD_ENCRYPTION_KEY` in the new `.env` to the **same value** that was used when the data was encrypted. A new/different key will not decrypt old data.

## Restore procedures

### A) Restore the whole VM (fastest, preferred)

Roll back to a Proxmox snapshot. This brings back the OS, app, database, and `.env` (including the encryption key) together. Then verify the app boots and timers are active.

### B) Restore just the database from a dump

On a host that already has the app + a matching `.env` (with the correct `FIELD_ENCRYPTION_KEY`):

```bash
# 1. Stop the app so nothing writes during the restore
sudo systemctl stop murphys-bench

# 2. Decompress the chosen dump
gunzip -k /opt/murphys-bench/backups/<dump-file>.sql.gz

# 3. Restore into PostgreSQL (drop/recreate the DB as appropriate for the dump format)
#    Use the same psql/pg_restore method that matches how backup_db.sh writes the dump.

# 4. Apply any newer migrations the restored DB predates
cd /opt/murphys-bench
venv/bin/python manage.py migrate

# 5. Start the app back up and verify
sudo systemctl start murphys-bench
```

> Confirm the exact dump format in `scripts/backup_db.sh` (plain SQL vs. custom format) before restoring — it determines whether you use `psql <` or `pg_restore`.

### C) Rebuild the app on a new host

1. Provision Ubuntu 24.04, PostgreSQL 16, Python 3.12, Nginx.
2. `git clone` the repo to `/opt/murphys-bench/`, create the venv, `pip install -r requirements.txt`.
3. Recreate `.env` — **including the original `FIELD_ENCRYPTION_KEY` and `SECRET_KEY`** from Bitwarden.
4. Restore the database (procedure B).
5. Install the systemd units from `deploy/` (service + the three timers).
6. Point inbound email at the correct mailbox in Settings → Inbound Email.
7. `manage.py check` and smoke-test in the browser.

## Recovery test (do this before you trust it)

Periodically prove the chain works end-to-end:

1. Take a fresh dump.
2. Restore it into a throwaway database / scratch VM **with the real `FIELD_ENCRYPTION_KEY`**.
3. Open a record with encrypted fields (e.g. an org-vault credential) and confirm it **decrypts and reads back correctly**.

A backup you have never restored is a hypothesis, not a backup.

## Quick recovery facts

- Backups live at `/opt/murphys-bench/backups/`, kept 14 days.
- The encryption key is in **Bitwarden** — without it, encrypted fields are unrecoverable.
- Inbound mail is **POP3 delete-from-server**, so MB is the *only* copy of inbound email — the DB backup is also the mail backup.
- Code is always recoverable from GitHub; only the database and `.env` carry irreplaceable state.
