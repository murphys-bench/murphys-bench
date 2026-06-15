# Installing Murphy's Bench

This guide installs Murphy's Bench on a fresh Ubuntu 24.04 server, served over
HTTPS behind a Cloudflare Tunnel. It targets a self-hosted instance on an
internal network or a small VM.

> **Status of this document:** first draft, written from the SCS production
> setup. It is being validated by performing a clean install on a separate
> demo VM. Sections marked **⚠ VERIFY** are expected to be corrected during
> that install — if a step doesn't work, fix the step here, don't work around it.

---

## 0. What you're standing up

| Piece | What it is |
|-------|-----------|
| Ubuntu 24.04 LTS | Host OS (Python 3.12 is the system default) |
| PostgreSQL 16 | Database |
| Gunicorn | Python app server (runs Django) |
| Nginx | Reverse proxy in front of Gunicorn; serves static files |
| Cloudflare Tunnel | Public HTTPS access without opening inbound ports |
| systemd | Runs Gunicorn + the scheduled jobs (backup, inbound email, SLA check) |

There is **no build step** for the frontend — Tailwind/HTMX/Alpine load from CDNs.

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
sudo apt install -y python3 python3-venv python3-pip \
    postgresql postgresql-contrib \
    nginx git
```

Confirm Python is 3.12:

```bash
python3 --version   # expect 3.12.x
```

---

## 3. Application user and code

Create a dedicated service user and put the app under `/opt`:

```bash
sudo adduser --system --group --home /opt/murphys-bench scs-tech   # ⚠ VERIFY: prod uses a login user 'scs-tech'; a --system user may differ
sudo git clone <REPO_URL> /opt/murphys-bench
sudo chown -R scs-tech:scs-tech /opt/murphys-bench
```

> **⚠ VERIFY:** On the SCS prod box the owning user is `scs-tech` and is a
> normal login user (so it can hold an SSH key and run `git pull`). Decide
> deliberately whether the demo box uses a login user or a locked system user,
> and write down the choice here.

---

## 4. Python environment

```bash
cd /opt/murphys-bench
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

---

## 5. PostgreSQL database

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
ALLOWED_HOSTS=demo.example.com    # the public hostname Cloudflare will serve

DB_ENGINE=django.db.backends.postgresql
DB_NAME=murphys_bench
DB_USER=mb_user
DB_PASSWORD=<the password from step 5>
DB_HOST=localhost
DB_PORT=5432

# HTTPS hardening — turn ON only once Cloudflare HTTPS is confirmed end-to-end (step 9)
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

```bash
cd /opt/murphys-bench
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser
```

Quick smoke test before wiring up the web server:

```bash
venv/bin/python manage.py check --deploy   # should be clean once HTTPS settings are on
venv/bin/python -m pytest                   # spine test suite should pass
```

---

## 8. Gunicorn + Nginx

> **⚠ VERIFY — capture from prod during the demo install.** The exact Gunicorn
> systemd unit and Nginx site config live on the SCS production VM, not in the
> repo. The templates below are a starting point; replace them with the real,
> working versions once confirmed on the demo box, and consider committing them
> to `deploy/` so the next install is copy-paste.

**Gunicorn unit** — `/etc/systemd/system/murphys-bench.service`:

```ini
[Unit]
Description=Murphy's Bench (Gunicorn)
After=network.target postgresql.service

[Service]
User=scs-tech
Group=scs-tech
WorkingDirectory=/opt/murphys-bench
ExecStart=/opt/murphys-bench/venv/bin/gunicorn \
    --workers 3 \
    --bind unix:/opt/murphys-bench/murphys-bench.sock \
    murphys_bench.wsgi:application
Restart=always

[Install]
WantedBy=multi-user.target
```

> Note: `gunicorn` is **not currently in `requirements.txt`** — install it into
> the venv (`venv/bin/pip install gunicorn`) and add it to requirements. **⚠ VERIFY**

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

    location /static/ {
        alias /opt/murphys-bench/staticfiles/;   # ⚠ VERIFY STATIC_ROOT path
    }
    location /media/ {
        alias /opt/murphys-bench/media/;
    }
    location / {
        proxy_pass http://unix:/opt/murphys-bench/murphys-bench.sock;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/murphys-bench /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## 9. Cloudflare Tunnel (public HTTPS)

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
