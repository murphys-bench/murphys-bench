# Installing Murphy's Bench

**The short version:** clone the repo onto an Ubuntu 24.04 box and run
`scripts/setup.sh`. That one command installs everything — system packages,
Python dependencies, the database, gunicorn, and nginx — and finishes with the
app reachable at `http://<this-box's-LAN-IP>/` on your local network. Log in
as the superuser it creates along the way. That's it — nothing else is
required to start using it.

```bash
git clone <REPO_URL> murphys-bench
cd murphys-bench
scripts/setup.sh
```

> **This is not a one-click product installer.** `setup.sh` genuinely does
> finish the job for the common case (a fresh Ubuntu box, nginx, no existing
> web server on it) — but Murphy's Bench is a Django app you're standing up
> yourself, not a signed `.exe` or a hosted SaaS signup. If something about
> your box doesn't match the common case (you already run other websites on
> it, you use a different reverse proxy, you're not on Ubuntu), the script
> tells you so and stops rather than guessing — read on for what it assumes
> and what to do if it doesn't fit.

> **What you get at the end is plain HTTP on your local network only.**
> That's a deliberate, permanent choice, not a "step 1 of 2" — see
> [Going public (remote access)](#going-public-remote-access) below for why,
> and what your options are if you do need it reachable from outside your
> network.

---

## What `setup.sh` actually does

In order, on a fresh box:

1. Installs system packages via `apt` — Python, nginx, git, and the PDF-
   rendering libraries Murphy's Bench needs to generate repair reports and
   quotes.
2. Creates a Python virtual environment and installs the app's Python
   dependencies.
3. Generates `.env` with fresh, unique secret keys for this instance (never
   reused from anywhere else, never committed to the repo).
4. Builds the self-hosted CSS (no Node/npm required).
5. Runs database migrations and creates your superuser login.
6. Runs the test suite as a smoke check — it won't leave you with a broken
   install.
7. **Writes and starts a gunicorn systemd service**, and **writes and enables
   an nginx site**, so the app is actually being served — not just installed.

Steps 1–6 are the "app layer." Step 7 is the part a lot of self-hosted
software leaves as a manual exercise for the reader — hand-writing a systemd
unit file and an nginx server block from a doc snippet, which is exactly the
kind of thing that's easy to get subtly wrong if you haven't done it before.
`setup.sh` does it for you.

**It's safe to re-run.** An existing `.env` is never touched; everything else
just re-confirms or reloads.

### What it assumes

- **Ubuntu 24.04** (or another Debian/Ubuntu `apt`-based distro) with
  `systemd` and no conflicting nginx setup already on the box.
- **Nothing else is using port 80** on this box. `setup.sh` replaces nginx's
  default site with Murphy's Bench's own — fine for a box dedicated to this
  app, wrong if you're already hosting something else there.

### When to use `--skip-web` instead

The script's own `--skip-web` flag (see its header comment for the full
explanation) exists for the cases above — you already run other sites on this
nginx, you use your own reverse proxy, or you're not on a systemd+nginx host.
Pass it and `setup.sh` stops after the app layer (step 6); you then wire up
serving it yourself. This is the exception, not the default path — most
installs should leave it off.

---

## Prerequisites

- A VM or server running **Ubuntu 24.04 LTS**, with `sudo` access, dedicated
  to this app (see "port 80" note above).
- SSH access to the box (or you're running the script directly on it).
- The Murphy's Bench Git repository URL.

---

## Manual install (if `setup.sh` doesn't fit your box)

Everything `setup.sh` automates, spelled out, in case you're on a different
distro, want to understand each step, or hit something the script's
assumptions don't cover.

### 1. System packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx git logrotate \
    libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core
```

`logrotate` is used for the gunicorn logs. The `libpango*`/`fonts-dejavu-core`
packages are **WeasyPrint's** runtime dependencies — MB uses WeasyPrint to
generate PDFs (emailed repair reports and quotes). Without them the app still
runs, but emailing a report/quote fails with a clear error.

Confirm Python is 3.10+:

```bash
python3 --version
```

### 2. Application code

```bash
git clone <REPO_URL> /opt/murphys-bench
cd /opt/murphys-bench
```

Run this as whichever normal login user should own and run the app (it does
not need to be `root`, and doesn't need a dedicated system account — running
as your own login user, so it can hold an SSH key for deploys, is fine).

### 3. Backup destinations (rclone)

Onsite (SMB/NAS) and offsite (S3-compatible) backup destinations, configured later in
Settings → Maintenance → Backups, both go through a **vendored copy** of rclone at
`bin/rclone` — deliberately a per-app copy, not whatever's on `$PATH`, so a backup/restore
never depends on what else happens to be installed system-wide.

```bash
sudo apt install -y rclone
mkdir -p bin
cp "$(command -v rclone)" bin/rclone
chmod +x bin/rclone
```

Skip this if you don't plan to configure any backup destination — the app runs fine
without it, backups just won't be available until `bin/rclone` exists.

### 4. Python environment

```bash
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

### 5. Database

Nothing to install or configure — Murphy's Bench uses **SQLite**, a single
file (`db.sqlite3`) created automatically when you run migrations (step 6).

### 6. Environment file (`.env`)

```bash
cp .env.example .env
```

Generate the two keys (each instance gets its own — never reuse another box's):

```bash
# SECRET_KEY
venv/bin/python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

# FIELD_ENCRYPTION_KEY  (AES-256 / Fernet — encrypts stored credentials)
venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Then edit `.env`:

```ini
DEBUG=False                       # production-safe; app REFUSES to start with default keys
SECRET_KEY=<generated above>
FIELD_ENCRYPTION_KEY=<generated above>
ALLOWED_HOSTS=192.168.1.50        # this box's LAN IP (and/or hostname)

# Database: SQLite (a file at db.sqlite3) — no DB settings needed.

# HTTPS hardening — explicitly OFF for a plain-HTTP LAN install. Don't just
# leave these commented out: SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE
# default to "not DEBUG" in settings.py, i.e. True whenever DEBUG=False —
# which silently breaks login/session/CSRF here, since a browser won't send
# a Secure cookie over plain HTTP. Set them explicitly:
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
SECURE_HSTS_SECONDS=0
# CSRF_TRUSTED_ORIGINS=https://your.hostname

# Only flip the three above to True once you've set up a TLS front door —
# see "Going public" below. Turning them on without TLS in front breaks access.
```

> **CRITICAL:** Losing `FIELD_ENCRYPTION_KEY` makes all encrypted credential
> data permanently unrecoverable. Store it somewhere safe (e.g. a password
> manager) the moment you generate it.

### 7. Initialize Django

```bash
mkdir -p logs media protected backups
scripts/build_css.sh
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser
```

`logs/` must exist before any `manage.py` command runs — Django's logging
opens `logs/murphys_bench.log` at startup and won't create the parent dir.

Quick smoke test:

```bash
venv/bin/python manage.py check --deploy   # HTTPS warnings are expected on a plain-HTTP LAN box
venv/bin/python -m pytest                   # spine test suite should pass
```

### 8. Gunicorn + nginx

**Gunicorn unit** — `/etc/systemd/system/murphys-bench.service`:

```ini
[Unit]
Description=Murphy's Bench (Gunicorn)
After=network.target

[Service]
User=<your-login-user>
Group=<your-login-user>
WorkingDirectory=/opt/murphys-bench
ExecStart=/opt/murphys-bench/venv/bin/gunicorn --workers 3 \
    --bind 127.0.0.1:8001 \
    --access-logfile /opt/murphys-bench/logs/gunicorn-access.log \
    --error-logfile /opt/murphys-bench/logs/gunicorn-error.log \
    murphys_bench.wsgi:application
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now murphys-bench
systemctl is-active murphys-bench
```

**Nginx site** — `/etc/nginx/sites-available/murphys-bench`:

```nginx
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 50M;

    location /static/ {
        alias /opt/murphys-bench/staticfiles/;
    }
    location /media/ {
        alias /opt/murphys-bench/media/;
    }
    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/murphys-bench /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Browse to `http://<this-box's-LAN-IP>/` and log in as the superuser from
step 6.

---

## Going public (remote access)

**By default — and for most shops, permanently — Murphy's Bench is only
reachable on your local network.** That's not a placeholder state waiting for
a "part 2." A shop tool that only you and your techs use, from inside your
own building or over VPN, has no real need to be reachable from the open
internet, and every way of making it reachable adds a public attack surface
that plain LAN access simply doesn't have.

This is also how Murphy's Bench's own production instance runs, day to day:
plain HTTP, LAN-only, no public hostname, no TLS. It's a deliberate choice for
that box, not an oversight — see `docs/deployment-tls.md` for the full
reasoning.

**If you do need it reachable from outside your network** — you work from
multiple sites, want to check a job from home, or want techs on the road to
reach it — you need two things: a way in through your firewall, and TLS (so
credentials and session data aren't sent in the clear over the internet).
Murphy's Bench doesn't terminate TLS itself (that's normal for a Django app,
not a missing feature — see `docs/deployment-tls.md` for why), so this always
means putting something TLS-capable in front of it. Options, roughly easiest
to most involved:

- **Cloudflare Tunnel** — no inbound firewall ports to open at all; Cloudflare
  terminates TLS at their edge and tunnels the request to your box. This is
  what Murphy's Bench's own **demo instance** uses, gated by Cloudflare
  Access (an extra login layer on top, since a demo box is more exposed by
  design than most shops would want their real one to be). Free for this use
  case. Requires a domain name managed in a Cloudflare account.
- **Caddy** as your reverse proxy instead of nginx — handles Let's Encrypt
  certificates automatically if you have a domain pointed at a public IP.
  More setup than Cloudflare Tunnel, no third party in the request path.
- **Your own nginx + Let's Encrypt (certbot)** — same idea as Caddy, more
  manual certificate management.
- **A VPN back into your LAN** (WireGuard, Tailscale, your firewall's
  built-in VPN) instead of exposing MB itself — techs connect to the network,
  then reach MB over plain HTTP exactly as if they were on-site. No public
  hostname or TLS-on-MB needed at all.

Whichever you choose, once TLS is confirmed working end-to-end, flip these in
`.env`:

```ini
ALLOWED_HOSTS=192.168.1.50,your.public.hostname
CSRF_TRUSTED_ORIGINS=https://your.public.hostname
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
# SECURE_HSTS_SECONDS=31536000    # only once HTTPS is rock-solid everywhere; hard to undo
```

Then `manage.py check --deploy` should come back clean.

Full rationale and every option in more detail:
[`docs/deployment-tls.md`](docs/deployment-tls.md).

---

## Scheduled jobs (systemd timers)

Backup, inbound-email polling, and SLA-overdue checking run on systemd
timers — optional, and not required to log in and start using the app. Full
instructions in [`deploy/README.md`](deploy/README.md).

## First login and config

1. Browse to `http://<this-box's-LAN-IP>/` and log in as the superuser.
2. Go to **Settings** (admin only) and work through company info, colors,
   email, users, and workflow config — see [`SETUP.md`](SETUP.md) for the
   full walkthrough.
3. See [`FEATURES.md`](FEATURES.md) for what the app actually does day to
   day.

---

## Appendix: resetting a demo/test box

To wipe operational data (clients/tickets/WOs/etc.) while keeping all
configuration, use the built-in command — **never** `manage.py flush`, which
also destroys config:

```bash
venv/bin/python manage.py reset_operational_data            # dry-run by default
venv/bin/python manage.py reset_operational_data --confirm "DELETE ALL OPERATIONAL DATA"
```
