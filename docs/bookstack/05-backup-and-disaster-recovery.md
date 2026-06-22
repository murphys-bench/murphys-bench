# Murphy's Bench — Backup & Disaster Recovery

> Read this **before** you need it. The single biggest gotcha: a database backup is useless without the encryption key.

## What protects the data

| Layer | What it covers | Cadence |
|---|---|---|
| Nightly app backup | SQLite DB snapshot + attachments (`protected/`, `media/`) + `.env`, as one gzipped tarball | 02:15 nightly, last 14 kept locally |
| Off-site copy (Backblaze B2) | The same nightly tarball, pushed to bucket `scs-mb-backups` | every nightly run |
| Proxmox / PBS VM backups ⚠️ | Whole VM — **currently BROKEN for production** (VMID-103 collision prunes the real backup; see the System Assessment, page 09) | per Proxmox schedule |
| GitHub | All application code | every push |
| Bitwarden | `FIELD_ENCRYPTION_KEY` (and other secrets) | manual |

> **Production runs on SQLite** (`/opt/murphys-bench/db.sqlite3`), not PostgreSQL. The app *can* use
> PostgreSQL (set `DB_ENGINE=postgresql` in `.env`), but the SCS deployment deliberately uses SQLite —
> right call for a single-shop, low-concurrency workload. (Earlier docs claimed PostgreSQL 16 in
> production; that was never actually deployed.)

## The nightly backup

- Script: `/opt/murphys-bench/scripts/mb_backup.sh`. (The old `backup_db.sh` now just delegates to it,
  so the existing systemd timer keeps working unchanged.)
- What it does — **fail-loud** (exits non-zero rather than reporting a false "OK", which is exactly the
  trap the old pg_dump job fell into):
  1. Takes a **consistent SQLite snapshot** via the Python `sqlite3` online-backup API — safe while the
     app is live (WAL mode is on).
  2. Verifies the snapshot: `PRAGMA integrity_check` must be `ok` and table count ≥ 50.
  3. Bundles the snapshot + `.env` + `protected/` + `media/` into
     `backups/mb-backup-YYYYMMDD-HHMMSS.tar.gz`, then verifies the archive (`tar -t`, min size).
  4. Pushes the tarball off-site to Backblaze B2 (`b2:scs-mb-backups`) via the bundled rclone binary
     (`bin/rclone`, config `.rclone.conf`).
  5. Keeps the last 14 copies locally; B2 prunes its own copies via a lifecycle rule.
- Scheduled by `murphys-bench-backup.timer` (systemd, `Persistent=true`, so a missed run catches up).

### Off-site immutability (B2)

The B2 bucket `scs-mb-backups` has **Object Lock** with a **30-day governance** default retention, so
every uploaded backup is immutable for 30 days — ransomware or a compromised key cannot delete or
overwrite it. A B2 **lifecycle rule** deletes copies ~36 days after upload (just past the lock), so the
bucket self-maintains at ~35 days of history. The B2 application key is scoped to this one bucket and
stored only in `/opt/murphys-bench/.rclone.conf` (chmod 600, gitignored).

Check it's running / view history:

```bash
systemctl list-timers murphys-bench-backup.timer
journalctl -u murphys-bench-backup
tail -n 20 /opt/murphys-bench/logs/backup.log
ls -lh /opt/murphys-bench/backups/
```

Run a backup on demand:

```bash
/opt/murphys-bench/scripts/mb_backup.sh
```

## 🔑 The encryption-key dependency (critical)

Several fields are **AES-256 encrypted at rest** (device credentials, org-vault credentials, stored mail
passwords). The backup contains only the **encrypted ciphertext** — *not* the key.

> **A restore needs TWO things: the backup tarball *and* the `FIELD_ENCRYPTION_KEY`.**
> The key lives in the production `.env` and is also stored in **Bitwarden**. If you lose the key, every
> encrypted field becomes permanently unreadable, even from a perfect backup.

When restoring to a fresh host, set `FIELD_ENCRYPTION_KEY` in the new `.env` to the **same value** that
was used when the data was encrypted. A new/different key will not decrypt old data.

## Restore procedures

### A) Restore the whole VM (Proxmox / PBS)

> ⚠️ **As of June 2026 this path is NOT reliable for production** — a VMID collision is pruning the real
> murphys-bench VM backup (see the System Assessment, page 09). Until it's fixed, prefer procedure **B**
> (restore from the Backblaze B2 tarball), which is verified working. Once PBS is fixed, this is the
> fastest whole-machine recovery.

Roll back to a Proxmox / PBS backup. This brings back the OS, app, database, and `.env` (including the
encryption key) together. Then verify the app boots and the timers are active.

### B) Restore the database + files from a nightly tarball

On a host that already has the app + a matching `.env` (with the correct `FIELD_ENCRYPTION_KEY`):

```bash
# 1. Stop the app so nothing writes during the restore
sudo systemctl stop murphys-bench

# 2. Get a backup — a local copy under backups/, or pull one from B2:
cd /opt/murphys-bench
bin/rclone --config .rclone.conf copy b2:scs-mb-backups/<file>.tar.gz /tmp/

# 3. Extract it (contains: db-<ts>.sqlite3, .env, protected/, media/)
mkdir -p /tmp/mbrestore && tar -xzf /tmp/<file>.tar.gz -C /tmp/mbrestore

# 4. Put the DB and files in place
cp /tmp/mbrestore/db-*.sqlite3 /opt/murphys-bench/db.sqlite3
cp -a /tmp/mbrestore/protected /tmp/mbrestore/media /opt/murphys-bench/
#    (the tarball also has .env — only restore that when rebuilding from scratch)

# 5. Apply any newer migrations the restored DB predates
venv/bin/python manage.py migrate

# 6. Start the app back up and verify
sudo systemctl start murphys-bench
```

### C) Rebuild the app on a new host

1. Provision Ubuntu 24.04, Python 3.12, Nginx. **No database server is needed** — SQLite is just a file.
2. `git clone` the repo to `/opt/murphys-bench/`, create the venv, `pip install -r requirements.txt`.
3. Recreate `.env` — **including the original `FIELD_ENCRYPTION_KEY` and `SECRET_KEY`** from Bitwarden.
4. Restore the database + files (procedure B).
5. Install the systemd units from `deploy/` (service + the three timers).
6. For off-site backups, restore `/opt/murphys-bench/.rclone.conf` (the B2 key) or create a new one.
7. Point inbound email at the correct mailbox in Settings → Inbound Email.
8. `manage.py check` and smoke-test in the browser.

## Recovery test (do this before you trust it)

Periodically prove the chain works end-to-end:

1. Take a fresh backup (`scripts/mb_backup.sh`).
2. Extract the tarball to a scratch location and open the snapshot DB **with the real
   `FIELD_ENCRYPTION_KEY`** in the matching `.env`.
3. Open a record with encrypted fields (e.g. an org-vault credential) and confirm it **decrypts and
   reads back correctly**.

A backup you have never restored is a hypothesis, not a backup.

## Quick recovery facts

- Local backups: `/opt/murphys-bench/backups/`, last 14 kept.
- Off-site: Backblaze B2 `scs-mb-backups` — immutable 30 days (Object Lock), ~36-day lifecycle prune.
- Production DB is **SQLite** (`db.sqlite3`); the nightly tarball also contains attachments + `.env`.
- The encryption key is in **Bitwarden** — without it, encrypted fields are unrecoverable.
- Inbound mail is **POP3 delete-from-server**, so MB is the *only* copy of inbound email — the nightly
  backup is also the mail backup.
- Code is always recoverable from GitHub; only the database, attachments, and `.env` carry irreplaceable state.
