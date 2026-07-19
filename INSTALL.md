# Installing Murphy's Bench

Murphy's Bench includes an installation script for a fresh Ubuntu 24.04 server or VM dedicated to the application.

The script installs the required system packages and Python dependencies, creates the database and application secrets, builds the CSS, runs migrations and tests, and configures Gunicorn and nginx.

```bash
git clone <REPO_URL> murphys-bench
cd murphys-bench
scripts/setup.sh
```

When it finishes, Murphy's Bench should be available at:

```
http://<server-LAN-IP>/
```

Log in with the superuser account created during setup.

This gives you a working LAN installation. Company details, email, users, backups, and workflow settings are configured after login.

`setup.sh` is intended for a fresh Ubuntu 24.04 system with no other website using port 80. It stops rather than altering an unsupported or conflicting web-server setup.

For an existing web server, a different reverse proxy, or a nonstandard deployment, use:

```bash
scripts/setup.sh --skip-web
```

That installs and initializes the application but leaves the Gunicorn, reverse-proxy, and TLS configuration to you.

## Supported Installation

The standard installer assumes:

- Ubuntu 24.04 LTS
- `sudo` access
- `systemd`
- a server or VM dedicated to Murphy's Bench
- no existing service using port 80
- access to the Murphy's Bench Git repository

Other Debian- or Ubuntu-based systems may work, but Ubuntu 24.04 is the supported installation target.

## What the Installer Does

The installer:

1. Installs Python, nginx, Git, PDF-rendering libraries, and other system packages.
2. Creates a Python virtual environment.
3. Installs the Python dependencies.
4. Creates `.env` with unique application and encryption keys.
5. Builds the locally hosted CSS.
6. Runs database migrations.
7. Collects static files.
8. Creates the initial superuser.
9. Runs Django checks and the test suite.
10. Installs and starts the Gunicorn systemd service.
11. Configures and enables the nginx site.

The installer preserves an existing `.env` when rerun. Review the script before rerunning it on a system whose web-server configuration has been changed manually.

## After Installation

Browse to:

```
http://<server-LAN-IP>/
```

Then:

1. Log in with the superuser account created by the installer.
2. Open Settings.
3. Configure company information, email, users, workflow options, and backups.

See `SETUP.md` for the initial configuration walkthrough and `FEATURES.md` for the day-to-day features.

## Local Network and Remote Access

The standard installation uses plain HTTP and is intended for a trusted local network or access through a VPN.

Do not expose the standard port-80 installation directly to the internet.

For remote access, use one of the following:

- a VPN such as WireGuard, Tailscale, or the VPN provided by your firewall
- Cloudflare Tunnel
- Caddy with TLS
- nginx with Let's Encrypt

See `docs/deployment-tls.md` before enabling public access.

Once HTTPS is working correctly, update `.env`:

```
ALLOWED_HOSTS=192.168.1.50,your.public.hostname
CSRF_TRUSTED_ORIGINS=https://your.public.hostname
SECURE_SSL_REDIRECT=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```

Enable HSTS only after HTTPS has been tested and is expected to remain permanent:

```
SECURE_HSTS_SECONDS=31536000
```

Restart the application after changing `.env`.

## Manual Installation

Use the manual process when the standard installer does not fit the server or when you need to integrate Murphy's Bench with an existing environment.

### 1. Install System Packages

```bash
sudo apt update
sudo apt install -y \
    python3 \
    python3-venv \
    python3-pip \
    nginx \
    git \
    logrotate \
    rclone \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libpangoft2-1.0-0 \
    fonts-dejavu-core
```

Confirm the Python version:

```bash
python3 --version
```

Murphy's Bench requires Python 3.10 or later.

### 2. Create the Application Directory

```bash
sudo mkdir -p /opt/murphys-bench
sudo chown "$USER":"$USER" /opt/murphys-bench
git clone <REPO_URL> /opt/murphys-bench
cd /opt/murphys-bench
```

The application should run as a normal system user, not as `root`.

### 3. Install the Application-Local rclone Copy

Murphy's Bench uses `bin/rclone` for configured SMB and S3-compatible backup destinations.

```bash
mkdir -p bin
cp "$(command -v rclone)" bin/rclone
chmod +x bin/rclone
```

This step can be omitted until backups are configured.

### 4. Create the Python Environment

```bash
python3 -m venv venv
venv/bin/pip install --upgrade pip
venv/bin/pip install -r requirements.txt
```

### 5. Create `.env`

```bash
cp .env.example .env
```

Generate the application keys:

```bash
venv/bin/python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Add the generated values and the server address to `.env`:

```
DEBUG=False
SECRET_KEY=<generated-secret-key>
FIELD_ENCRYPTION_KEY=<generated-encryption-key>
ALLOWED_HOSTS=192.168.1.50

SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
SECURE_HSTS_SECONDS=0
```

For a plain-HTTP LAN installation, the cookie settings must be explicitly disabled. Browsers will not send cookies marked `Secure` over HTTP.

Keep a separate secure copy of `FIELD_ENCRYPTION_KEY`. If it is lost, encrypted credentials cannot be recovered from a backup.

### 6. Initialize the Application

```bash
mkdir -p logs media protected backups
scripts/build_css.sh
venv/bin/python manage.py migrate
venv/bin/python manage.py collectstatic --noinput
venv/bin/python manage.py createsuperuser
venv/bin/python manage.py check
```

**Optional — verify the install** by running the test suite. This is not part of
initializing the application; it's a smoke test you can run if you want confirmation
the code is healthy on this box (it's also run automatically by CI on every change):

```bash
venv/bin/python -m pytest
```

### 7. Configure Gunicorn

Create `/etc/systemd/system/murphys-bench.service`:

```ini
[Unit]
Description=Murphy's Bench
After=network.target

[Service]
User=<application-user>
Group=<application-user>
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

Enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now murphys-bench
systemctl is-active murphys-bench
```

### 8. Configure nginx

Create `/etc/nginx/sites-available/murphys-bench`:

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

Enable the site:

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo ln -s /etc/nginx/sites-available/murphys-bench \
    /etc/nginx/sites-enabled/murphys-bench
sudo nginx -t
sudo systemctl reload nginx
```

Browse to:

```
http://<server-LAN-IP>/
```

## Scheduled Jobs

Backups, inbound-email polling, and SLA checks use optional systemd timers.

They are not required for the first login, but the corresponding features will not run automatically until their timers are configured.

See `deploy/README.md`.

## Resetting a Demo or Test System

To remove operational records while retaining configuration:

```bash
venv/bin/python manage.py reset_operational_data
venv/bin/python manage.py reset_operational_data \
    --confirm "DELETE ALL OPERATIONAL DATA"
```

The first command is a dry run.

Do not use `manage.py flush`; it also removes application configuration.
