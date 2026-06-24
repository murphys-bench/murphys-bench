# Installing Murphy's Bench

This guide installs Murphy's Bench on a fresh Ubuntu 24.04 server, served over
HTTPS behind a Cloudflare Tunnel. It targets a self-hosted instance on an
internal network or a small VM.

> **On HTTPS/TLS:** MB deliberately does **not** terminate TLS itself — it runs
> behind a reverse proxy (Cloudflare Tunnel, Caddy, nginx, or your own web
> server) that does. This is the standard Django deployment model, not a missing
> feature. A Cloudflare Tunnel is used below, but it's only one option. For the
> full rationale and every alternative (Caddy auto-HTTPS, nginx + your own /
> Let's Encrypt / DNS-01 cert, a subdomain on a web server you already run,
> self-signed for LAN-only, or plain HTTP on a trusted LAN), see
> [`docs/deployment-tls.md`](docs/deployment-tls.md).

> **Status of this document:** validated by a clean install from git onto a
> fresh Ubuntu 24.04.4 VM (`REDACTED-IP`) on Jun 22 2026, running on **SQLite**
> (the production database) — the gunicorn/nginx snippets below match that
> known-good prod deployment. Known-good configs also live in
> [`deploy/demo/`](deploy/demo/).

---

## 0. What you're standing up

| Piece | What it is |
|-------|-----------|
| Ubuntu 24.04 LTS | Host OS (Python 3.12 is the system default) |
| PostgreSQL 16 *(optional)* | Database — default is SQLite (a file, no DB server needed) |
| Gunicorn | Python app server (runs Django) |
| Nginx | Reverse proxy in front of Gunicorn; serves static files |
| Cloudflare Tunnel | Public HTTPS access without opening inbound ports |
| systemd | Runs Gunicorn + the scheduled jobs (backup, inbound email, SLA check) |

The frontend is **fully self-hosted — nothing loads from a CDN.** HTMX/Alpine are
vendored in `static/js/`, and Tailwind is compiled to `static/css/app.css` by
`scripts/build_css.sh` (a pinned standalone CLI — no Node/npm). That build runs
automatically in `scripts/setup.sh` and `scripts/update.sh`; a manual install must
run it before `collectstatic` (see step 7).

> **Fast path:** once the code is on the box (step 3) with Python available, you can
> run **`scripts/setup.sh`** to automate steps 4, 6, 7 (venv, `.env` with generated
> keys, CSS build, migrate, collectstatic, superuser, smoke test) in one fail-loud
> command, then do the gunicorn/nginx/Cloudflare wiring (steps 8–10) by hand. The
> manual steps below are the longhand it scripts.

---

## 1. Prerequisites

- A VM or server running **Ubuntu 24.04 LTS**, with sudo access.
- A **Cloudflare account** and a domain managed in it (for the tunnel + hostname).
- SSH access to the box.
- The Murphy's Bench Git repository URL.

---

## 2. System packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx git logrotate
```

> `logrotate` is used for the Gunicorn logs (see `deploy/README.md` →
> Observability) and is missing on minimal Ubuntu installs.

> PostgreSQL is **not** required — MB defaults to SQLite (a single file, no DB
> server). Only install `postgresql postgresql-contrib` if you opt into Postgres
> in step 5.

Confirm Python is 3.12:

```bash
python3 --version   # expect 3.12.x
```

---

## 3. Application user and code

Create a dedicated service user and put the app under `/opt`:

The app runs as the existing **`scs-tech` login user** (same as prod — a normal
login user, so it can hold an SSH key). Create the app dir owned by it:

```bash
sudo mkdir -p /opt/murphys-bench
sudo chown scs-tech:scs-tech /opt/murphys-bench
```

Then get the code into `/opt/murphys-bench`:

- **Public/clone access:** `git clone <REPO_URL> /opt/murphys-bench`.
- **Private repo (recommended — how the test/staging box was done):** add a
  read-only **SSH deploy key** so the box can `git pull` deploys without storing
  a password or a broad PAT:
  ```bash
  ssh-keygen -t ed25519 -f ~/.ssh/mb_deploy -N '' -C 'mb-deploy'
  cat ~/.ssh/mb_deploy.pub        # add this to the GitHub repo → Settings → Deploy keys (read-only)
  printf 'Host github.com\n    IdentityFile ~/.ssh/mb_deploy\n    IdentitiesOnly yes\n' >> ~/.ssh/config
  git clone git@github.com:<org>/murphys-bench.git /opt/murphys-bench
  ```
- **No-creds alternative:** seed it by `rsync` from a working checkout (excludes
  venv, secrets, local DB):
  ```bash
  rsync -az --delete --exclude venv/ --exclude .git/ --exclude .env \
    --exclude db.sqlite3 --exclude '__pycache__/' --exclude media/ \
    --exclude staticfiles/ ./ scs-tech@<HOST>:/opt/murphys-bench/
  ```

---

## 4. Python environment

```bash
cd /opt/murphys-bench
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

---

## 5. (Optional) PostgreSQL database

> **Note:** Murphy's Bench defaults to **SQLite** (a single file, no database server) and the SCS
> production deployment deliberately uses SQLite. This section is only needed if you set
> `DB_ENGINE=postgresql` in `.env`. To use the default SQLite, skip this section entirely.

Install the Postgres driver into the venv (it is **not** in `requirements.txt`, since
the default SQLite build doesn't need it):

```bash
venv/bin/pip install psycopg2-binary
```

```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE murphys_bench;
CREATE USER mb_user WITH PASSWORD 'CHANGE_ME_STRONG_PASSWORD';
ALTER ROLE mb_user SET client_encoding TO 'utf8';
ALTER ROLE mb_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE mb_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE murphys_bench TO mb_user;
SQL
```

On PostgreSQL 16 you may also need to grant schema rights:

```bash
sudo -u postgres psql -d murphys_bench -c "GRANT ALL ON SCHEMA public TO mb_user;"   # ⚠ VERIFY
```

---

## 6. Environment file (`.env`)

Copy the example and fill it in. **This file holds all secrets — never commit it.**

```bash
cd /opt/murphys-bench
cp .env.example .env
```

Generate the two keys (each instance gets its OWN — never reuse another box's):

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
ALLOWED_HOSTS=demo.example.com    # the public hostname (or the LAN IP for an internal-only box)

# Database: MB defaults to SQLite (a file at db.sqlite3) — no DB_* settings needed.
# To use PostgreSQL instead (optional — see step 5), uncomment these:
# DB_ENGINE=postgresql
# DB_NAME=murphys_bench
# DB_USER=mb_user
# DB_PASSWORD=<the password from step 5>
# DB_HOST=localhost
# DB_PORT=5432

# HTTPS hardening — turn ON only once Cloudflare HTTPS is confirmed end-to-end (step 9).
# For a plain-HTTP LAN-only box, leave all four OFF (the defaults) or internal access breaks.
CSRF_TRUSTED_ORIGINS=https://demo.example.com
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
# SECURE_HSTS_SECONDS=31536000    # leave commented until HTTPS is rock-solid; HSTS is hard to undo
```

> **CRITICAL:** Losing `FIELD_ENCRYPTION_KEY` makes all encrypted credential
> data permanently unrecoverable. Store it somewhere safe (e.g. a password
> manager) the moment you generate it.

---

## 7. Initialize Django

First create the runtime directories the app writes to. **`logs/` is required** —
Django's logging opens `logs/murphys_bench.log` at startup and will **not** create
the parent dir, so every `manage.py` command fails until it exists:

```bash
cd /opt/murphys-bench
mkdir -p logs media protected
```

Then build the self-hosted stylesheet (required before `collectstatic` — it
compiles `static/css/app.css`; downloads a pinned Tailwind CLI on first run, no
Node) and initialize:

```bash
scripts/build_css.sh
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser
```

Quick smoke test before wiring up the web server:

```bash
venv/bin/python manage.py check --deploy   # HTTPS warnings are expected on a plain-HTTP LAN box
venv/bin/python -m pytest                   # spine test suite should pass
```

---

## 8. Gunicorn + Nginx

> **Known-good configs are in [`deploy/demo/`](deploy/demo/)** — copy them in
> rather than retyping (`deploy/demo/README.md` has the exact commands). The
> versions below match the production deployment (Gunicorn on a unix socket).

**Gunicorn unit** — `/etc/systemd/system/murphys-bench.service`:

```ini
[Unit]
Description=Murphy's Bench (Gunicorn)
After=network.target

[Service]
User=scs-tech
Group=scs-tech
WorkingDirectory=/opt/murphys-bench
EnvironmentFile=/opt/murphys-bench/.env
ExecStart=/opt/murphys-bench/venv/bin/gunicorn --workers 3 \
    --bind unix:/opt/murphys-bench/murphys.sock \
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
    listen 80;
    server_name demo.example.com;

    client_max_body_size 50M;

    location /static/ {
        alias /opt/murphys-bench/staticfiles/;
    }
    location /media/ {
        alias /opt/murphys-bench/media/;
    }
    location / {
        proxy_pass http://unix:/opt/murphys-bench/murphys.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/murphys-bench /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default     # so MB isn't shadowed by the default site
sudo nginx -t && sudo systemctl reload nginx
```

---

## 9. Cloudflare Tunnel (public HTTPS)

> **Network posture — decide first, it changes this section.**
> - **Internal-only instance (e.g. a production shop box):** skip this entire
>   step. Don't run a tunnel and don't set a public hostname — keep it reachable
>   on the LAN only. Leave the HTTPS-redirect/HSTS settings off; nothing is
>   exposed.
> - **Demo/test instance for outside testers:** run the tunnel for the public
>   hostname, **but keep local LAN access working too** so admins can debug what
>   testers find. Use this posture:
>   - Terminate TLS at the Cloudflare edge; **leave `SECURE_SSL_REDIRECT` OFF**
>     (otherwise plain-HTTP LAN requests get bounced to an invalid-cert URL).
>   - Put **both** the LAN IP and the public hostname in `ALLOWED_HOSTS`.
>   - **Do not enable HSTS** — it locks browsers into HTTPS-only for that host
>     and is hard to undo.
>   - Enforce HTTPS for outside visitors with Cloudflare's "Always Use HTTPS" at
>     the edge instead of Django's redirect.

This exposes the box over HTTPS without opening any inbound firewall ports.

```bash
# Install cloudflared
curl -L https://pkg.cloudflare.com/cloudflared-stable-linux-amd64.deb -o cloudflared.deb   # ⚠ VERIFY current install method
sudo dpkg -i cloudflared.deb

# Authenticate (opens a browser link — do this from your workstation)
cloudflared tunnel login

# Create and route the tunnel
cloudflared tunnel create murphys-bench-demo
cloudflared tunnel route dns murphys-bench-demo demo.example.com
```

Point the tunnel at Nginx (`~/.cloudflared/config.yml`), then run it as a service:

```yaml
tunnel: <TUNNEL_ID>
credentials-file: /root/.cloudflared/<TUNNEL_ID>.json
ingress:
  - hostname: demo.example.com
    service: http://localhost:80
  - service: http_status:404
```

```bash
sudo cloudflared service install
sudo systemctl enable --now cloudflared
```

**Now turn on the HTTPS hardening** from step 6 (if you left it off), restart
Gunicorn, and confirm:

```bash
sudo systemctl restart murphys-bench
curl -I https://demo.example.com         # expect HTTP/2 200 (or a redirect to /account/login/)
```

`manage.py check --deploy` should now come back clean.

---

## 10. Scheduled jobs (systemd timers)

The backup, inbound-email, and SLA-check jobs run on systemd timers (this stack
uses no cron). Full instructions are in [`deploy/README.md`](deploy/README.md).

For a **demo box** you typically want:
- **SLA check** timer — on (harmless, exercises the feature).
- **Inbound email** timer — only if you're demoing inbound; leave off otherwise.
- **Backup** timer — **skip it.** A demo box is disposable; no need for backups.

---

## 11. First login and config

1. Browse to `https://demo.example.com/` and log in as the superuser from step 7.
2. Go to **Settings** (admin only) and fill in Company info, colors/logo, etc.
3. Create user accounts for testers (Settings → Users).
4. Seed a few **fake** clients, contacts, devices, tickets, and work orders so
   there's something to interact with. **Do not load real client data onto a
   demo box.**

See **[SETUP.md](SETUP.md)** for the admin/configuration walkthrough.   <!-- ⚠ TODO: write SETUP.md -->

---

## Appendix: resetting a demo box

To wipe operational data (clients/tickets/WOs/etc.) while keeping all
configuration, use the built-in command — **never** `manage.py flush`, which
also destroys config:

```bash
venv/bin/python manage.py reset_operational_data            # dry-run by default
venv/bin/python manage.py reset_operational_data --confirm "DELETE ALL OPERATIONAL DATA"
```
