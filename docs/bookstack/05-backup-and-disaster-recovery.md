# Murphy's Bench — Backup & Disaster Recovery

> Read this **before** you need it. The single biggest gotcha: a database backup is useless without the encryption key.

## What protects the data

| Layer | What it covers | Cadence |
|---|---|---|
| Admin-configured app backup | SQLite DB snapshot + attachments (`protected/`, `media/`) + `.env`, as one gzipped tarball, shipped to one or both configured destinations | Per destination's own schedule (Settings → Maintenance → Backups); a 5-minute systemd tick checks what's due |
| Onsite destination (optional) | The tarball, pushed over **SMB** to a NAS/network share via rclone — no OS-level mount, ever | Its own schedule + age/count retention on that share |
| Offsite destination (optional) | The tarball, pushed to an **S3-compatible bucket** (e.g. Backblaze B2) via rclone | Its own schedule + age/count retention on that bucket |
| Proxmox / PBS VM backups ✅ | Whole VM — **working & verified** (VMID collisions resolved Jun 22 2026: BookStack→202, Cloudflared→203, prod stays 103; daily verify job added). Confirmed healthy Jun 24 2026: prod `vm/103` has 4 retained backups, Verify State **All OK**, no collisions across the datastore. *(Backups are not client-side encrypted at rest — see the encryption note in the System Assessment, page 09.)* | per Proxmox schedule |
| GitHub | All application code | every push |
| Bitwarden | `FIELD_ENCRYPTION_KEY` (and other secrets) | manual |

> **Production runs on SQLite** (`/opt/murphys-bench/db.sqlite3`) — the only supported DB.

## The app backup

At least one destination (onsite or offsite) must be enabled — both can run, and each has its
**own independent schedule and retention**, configured admin-side in **Settings → Maintenance →
Backups**. No destination configured = backups don't run (fail loud, not a silent local-only
fallback).

- **Mechanism:** `scripts/mb_backup.sh`, fired by `scripts/backup_scheduler.sh` on a **5-minute**
  `murphys-bench-backup.timer` tick. Each tick checks every enabled destination's own
  day/time schedule and fires `mb_backup.sh --only <csv>` for exactly the destinations due (if
  both are due at once, one snapshot ships to both).
- **What it does — fail-loud** (exits non-zero rather than reporting a false "OK", which is
  exactly the trap the old pg_dump job fell into):
  1. Takes a **consistent SQLite snapshot** via the Python `sqlite3` online-backup API — safe
     while the app is live (WAL mode is on).
  2. Verifies the snapshot: `PRAGMA integrity_check` must be `ok` and table count ≥ 50.
  3. Bundles the snapshot + `.env` + `protected/` + `media/` into
     `backups/mb-backup-YYYYMMDD-HHMMSS.tar.gz` on the VM's own disk, then verifies the archive
     (`tar -t`, min size).
  4. **Ships the tarball to each due destination via rclone**, then **deletes the local staging
     copy** — the VM disk is transient staging only, never a backup destination itself (a backup
     that lives only on the box it protects isn't a backup).
  5. **Prunes each destination independently** by its own configured retention — by age (days) or
     by count (keep last N) — after a successful ship.
- **Admin controls:** enable/disable either destination, per-destination schedule (days + time),
  per-destination retention mode/value, a **Test destination** connectivity probe (mirrors the
  Invoice Ninja "Test Connection" pattern), a **status panel** (last run time/result per
  destination, from `logs/backup-status.json`), and a **Run now** button (out-of-band trigger,
  same mechanism as the in-app admin Update button).
- **Dead-man's-switch:** `mb_backup.sh` pings **healthchecks.io** on success and `/fail` on
  failure (`HEALTHCHECKS_URL` in `.env`) — see memory `reference_healthchecksio_backup_deadmanswitch`.
  A missed/failed run alerts even if the whole VM (and its timers) are down.

### Onsite (SMB) and offsite (S3) — both via rclone, no mount ever

Both destinations are reached the same way: as an **rclone remote**, configured entirely
in-app (host/share/username/password/folder for onsite; bucket/endpoint/keys for offsite).
`core/backup_ops.render_config()` writes `.rclone.conf` with two independent stanzas —
`[mbbackup]` (S3) and `[mbonsite]` (SMB) — rebuilt from only the currently-enabled destinations
each render. **MB never mounts a network share at the OS level** (no `sudo`, no `cifs-utils`,
no `/etc/fstab` step, on any box, ever) — rclone speaks SMB directly, the same way it speaks S3.
The onsite password is rclone-*obscured* (SMB needs rclone's own reversible obfuscation format
in the config file) via the vendored `bin/rclone obscure`; the plaintext only ever lives
encrypted in `SiteSettings` or briefly in memory, never in cleartext on disk.

> ⚠ **Fresh-box gap (open, not yet fixed):** `bin/rclone` has so far only ever been manually
> placed on each box (copied in ad hoc, never a documented setup step like the WeasyPrint system
> libs are). A fresh install or full rebuild needs this binary present before backups can run or
> be tested — `manage.py render_backup_config` will render the config files fine, but
> `mb_backup.sh` will fail at the ship step without the binary. Track this as a setup-step gap to
> close (add to `scripts/setup.sh` / INSTALL.md), not yet done as of this writing.

### Offsite immutability (when using Backblaze B2 with Object Lock)

If the configured S3-compatible offsite bucket has **Object Lock** with a governance retention
window enabled (SCS's own bucket does — 30 days), every uploaded backup is immutable for that
window — ransomware or a compromised key cannot delete or overwrite it. A bucket lifecycle rule
can auto-prune older copies past the lock window; MB's own per-destination retention setting
also prunes independently via `rclone delete`/`lsf`. This is a property of *how the offsite
bucket is configured*, not something MB enforces — confirm your bucket's lock/lifecycle settings
match what you expect.

Check it's running / view history:

```bash
systemctl list-timers murphys-bench-backup.timer
journalctl -u murphys-bench-backup
tail -n 20 /opt/murphys-bench/logs/backup.log
cat /opt/murphys-bench/logs/backup-status.json
```

Run a backup on demand — either the **Run now** button in Settings → Maintenance → Backups, or:

```bash
/opt/murphys-bench/scripts/mb_backup.sh
```

Regenerate the destination config files (normally automatic on every admin save, and on every
`update.sh` deploy) if they're ever missing after a restore or disk wipe:

```bash
venv/bin/python manage.py render_backup_config
```

## 🔑 The encryption-key dependency (critical)

Several fields are **AES-256 encrypted at rest** (device credentials, org-vault credentials, stored mail
passwords, S3/onsite backup credentials). The backup contains only the **encrypted ciphertext** — *not*
the key.

> **A restore needs TWO things: the backup tarball *and* the `FIELD_ENCRYPTION_KEY`.**
> The key lives in the production `.env` and is also stored in **Bitwarden**. If you lose the key, every
> encrypted field becomes permanently unreadable, even from a perfect backup.

When restoring to a fresh host, set `FIELD_ENCRYPTION_KEY` in the new `.env` to the **same value** that
was used when the data was encrypted. A new/different key will not decrypt old data.

## Restore procedures

### A) Restore the whole VM (Proxmox / PBS)

> ✅ **Working & verified** (the VMID collision that previously pruned the real production backup was
> fixed Jun 22 2026; PBS verify confirmed All OK Jun 24 — see page 09). This is the fastest
> whole-machine recovery: it brings back the OS, app, database, and `.env` together. Procedure **B**
> (restore from an onsite/offsite tarball) remains the option when you only need the data, or are
> rebuilding on a different host.

Roll back to a Proxmox / PBS backup. This brings back the OS, app, database, and `.env` (including the
encryption key) together. Then verify the app boots and the timers are active.

### B) Restore the database + files from a tarball

> **Preferred: `scripts/restore.sh`** automates this whole procedure fail-loud — it integrity-checks the
> bundled snapshot *before* touching anything live, saves the current db/protected/media/.env to
> `backups/pre-restore-<ts>/` (so the restore is itself reversible), stops the service, swaps everything
> in, restarts, and health-polls. Run: `cd /opt/murphys-bench && scripts/restore.sh backups/mb-backup-<ts>.tar.gz`.
> It keeps the live `.env` by default (same-box rollback keeps current secrets); use `--with-env` for a
> fresh-box rebuild. The manual steps below are what it does under the hood.

On a host that already has the app + a matching `.env` (with the correct `FIELD_ENCRYPTION_KEY`):

```bash
# 1. Stop the app so nothing writes during the restore
sudo systemctl stop murphys-bench

# 2. Get a backup — pull one from whichever destination(s) are configured
#    (adjust the remote name/path to match your Settings → Maintenance → Backups config)
cd /opt/murphys-bench
bin/rclone --config .rclone.conf copy mbbackup:<bucket>/<path>/<file>.tar.gz /tmp/    # offsite (S3)
bin/rclone --config .rclone.conf copy mbonsite:<share>/<folder>/<file>.tar.gz /tmp/   # onsite (SMB)

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
5. Install the systemd units from `deploy/` (service + the timers, incl. `murphys-bench-backup.timer`).
6. **Place the `bin/rclone` binary** (not currently automated — see the fresh-box gap noted above)
   before backups can run; re-enter onsite/offsite destination config in Settings → Maintenance →
   Backups (or restore `.rclone.conf` + run `manage.py render_backup_config` if you have the original).
7. Point inbound email at the correct mailbox in Settings → Inbound Email.
8. `manage.py check` and smoke-test in the browser.

## Recovery test (do this before you trust it)

Periodically prove the chain works end-to-end:

1. Take a fresh backup (**Run now** in Settings → Maintenance → Backups, or `scripts/mb_backup.sh`).
2. Confirm it actually landed on the configured destination(s) (`rclone lsf` against the remote, or
   the status panel's last-success timestamp).
3. Extract the tarball to a scratch location and open the snapshot DB **with the real
   `FIELD_ENCRYPTION_KEY`** in the matching `.env`.
4. Open a record with encrypted fields (e.g. an org-vault credential) and confirm it **decrypts and
   reads back correctly**.

A backup you have never restored is a hypothesis, not a backup.

## Quick recovery facts

- The VM disk is **staging only** — a completed backup ships off-box then the local copy is deleted;
  the VM itself is never a backup destination.
- Each configured destination (onsite SMB, offsite S3) has its **own schedule and its own retention**
  (by age or by count) — check Settings → Maintenance → Backups for the current values, not this doc.
- Production DB is **SQLite** (`db.sqlite3`); every tarball also contains attachments + `.env`.
- The encryption key is in **Bitwarden** — without it, encrypted fields (including onsite/offsite
  backup credentials themselves) are unrecoverable.
- Inbound mail is **POP3 delete-from-server**, so MB is the *only* copy of inbound email — a working
  backup is also the mail backup.
- Code is always recoverable from GitHub; only the database, attachments, and `.env` carry irreplaceable state.
- **Open gap:** `bin/rclone` provisioning on a fresh box isn't yet an automated setup step — see the
  callout above.
