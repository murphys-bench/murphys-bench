#!/usr/bin/env bash
# One-shot installer for Murphy's Bench. Takes a fresh box from "code is here"
# to "log in at http://<this-box>/ over the LAN" in one fail-loud command —
# app deps, database, AND the web server (gunicorn + nginx) that puts it on
# port 80. No code-reading, no hand-editing systemd/nginx files.
#
#   git clone <repo> /opt/murphys-bench
#   cd /opt/murphys-bench && scripts/setup.sh
#
# This deliberately stops at plain HTTP on your local network — the same
# posture Murphy's Bench's own production instance runs in. It does NOT set
# up a public domain, TLS certificate, or Cloudflare Tunnel; that is a
# separate, optional step covered in INSTALL.md § Going public (remote
# access). A LAN-only shop tool doesn't need it.
#
# Targets the Debian/Ubuntu (apt) family with Python >= 3.10.
#
# Flags / env:
#   --skip-apt        don't install system packages (already present, or no sudo)
#   --skip-web        don't touch gunicorn/nginx/systemd — stop after the app layer.
#                      Most installs should NOT pass this; it exists for cases where
#                      the default web setup would be wrong for your box:
#                        - this server already runs other nginx sites (the default
#                          web setup replaces nginx's default site with MB's, which
#                          would disrupt anything else already configured there)
#                        - you already run your own reverse proxy (Caddy, Traefik, a
#                          hand-maintained nginx config) and only want the app itself
#                        - you're not on systemd + nginx (the auto-wiring assumes both
#                          and will fail or fight your actual setup otherwise)
#                      If none of that describes you, leave this off — it's what gets
#                      you to a working login page without hand-editing config files.
#   --skip-tests      don't run the pytest smoke check at the end
#   --noinput         non-interactive: skip the createsuperuser prompt
#   ALLOWED_HOSTS=..  comma list for .env (default: localhost,127.0.0.1, plus this
#                      box's LAN IP — auto-detected — unless you override it)
#   COMPANY_NAME=..   .env COMPANY_NAME (default: Murphy's Bench)
#
# Safe to re-run: an existing .env is never overwritten; deps/migrate/build/web
# steps are idempotent (re-running just confirms/reloads).
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP"
VENV="$APP/venv/bin"
RUN_USER="$(id -un)"
RUN_GROUP="$(id -gn)"

SKIP_APT=0; SKIP_WEB=0; SKIP_TESTS=0; NOINPUT=0
for a in "$@"; do
  case "$a" in
    --skip-apt) SKIP_APT=1 ;;
    --skip-web) SKIP_WEB=1 ;;
    --skip-tests) SKIP_TESTS=1 ;;
    --noinput) NOINPUT=1 ;;
    *) echo "setup: unknown arg '$a'" >&2; exit 2 ;;
  esac
done

log()  { echo "$(date '+%F %T') setup: $*"; }
fail() { echo "SETUP FAILED: $*" >&2; exit 1; }

[ -f manage.py ] || fail "no manage.py in $APP — run this from the cloned repo root"

# 0) Preflight.
command -v git >/dev/null || fail "git not installed"
if [ "$SKIP_APT" = 0 ]; then
    command -v apt-get >/dev/null || fail "this installer targets the apt (Debian/Ubuntu) family; \
on another distro install python3/venv/pip/nginx/git/logrotate yourself and re-run with --skip-apt"
fi
PYBIN="$(command -v python3 || true)"
[ -n "$PYBIN" ] || fail "python3 not found"
PYVER="$("$PYBIN" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
"$PYBIN" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)' \
    || fail "Python >= 3.10 required (found $PYVER)"
LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
log "python $PYVER OK"

# 1) System packages.
if [ "$SKIP_APT" = 0 ]; then
    log "installing system packages (sudo)..."
    sudo apt-get update -qq || fail "apt update failed"
    # The libpango/cairo/ft2 stack + fonts are WeasyPrint's runtime deps (PDF
    # generation for repair reports and quotes); they pull cairo/glib/harfbuzz.
    sudo apt-get install -y -qq python3 python3-venv python3-pip nginx git logrotate curl \
        libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 fonts-dejavu-core rclone \
        || fail "apt install failed"
else
    log "skipping apt (--skip-apt)"
fi

# 2) Vendor the rclone binary into bin/rclone — backup destinations (onsite
# SMB + offsite S3) both go through it (core/backup_ops.py: rclone_bin() ==
# $APP/bin/rclone, deliberately a per-app copy, not whatever's on $PATH, so
# an app-level backup/restore never depends on what else is installed
# system-wide). apt just installed a system copy above; copy it in here so
# a fresh box works with zero manual steps — this was previously a gap
# (only ever done by hand on the boxes that already had it).
mkdir -p bin
if [ ! -x bin/rclone ]; then
    SYS_RCLONE="$(command -v rclone || true)"
    if [ -n "$SYS_RCLONE" ]; then
        cp "$SYS_RCLONE" bin/rclone && chmod +x bin/rclone
        log "vendored rclone ($("$APP/bin/rclone" version | head -1)) into bin/rclone"
    elif [ "$SKIP_APT" = 1 ]; then
        log "rclone not found and apt was skipped (--skip-apt) — backup destinations \
(Settings -> Maintenance -> Backups) won't work until you install rclone and copy/symlink \
it to $APP/bin/rclone yourself"
    else
        fail "rclone not found on PATH after apt install — install it manually and copy/symlink \
it to $APP/bin/rclone"
    fi
else
    log "bin/rclone already present — leaving it"
fi

# 3) Python virtualenv + dependencies.
if [ ! -x "$VENV/python" ]; then
    log "creating virtualenv..."
    "$PYBIN" -m venv venv || fail "venv creation failed"
fi
log "installing Python dependencies..."
"$VENV/pip" install --upgrade -q pip || fail "pip self-upgrade failed"
"$VENV/pip" install -q -r requirements.txt || fail "pip install -r requirements.txt failed"

# 4) Runtime directories the app writes to (logs/ is required at startup).
mkdir -p logs media protected backups
log "runtime dirs ready (logs media protected backups)"

# 5) .env — create with generated keys if absent; NEVER clobber an existing one.
if [ -f .env ]; then
    log ".env already exists — leaving it untouched"
else
    log "generating .env with fresh per-instance keys (SQLite default)..."
    SECRET_KEY="$("$VENV/python" -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())')" \
        || fail "could not generate SECRET_KEY"
    FERNET_KEY="$("$VENV/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')" \
        || fail "could not generate FIELD_ENCRYPTION_KEY"
    # Resolve defaults into plain vars first — apostrophes/spaces in a ${VAR:-default}
    # inside the heredoc break bash's ${...} parsing ("bad substitution").
    DEFAULT_HOSTS="localhost,127.0.0.1"
    [ -n "$LAN_IP" ] && DEFAULT_HOSTS="${DEFAULT_HOSTS},${LAN_IP}"
    ENV_HOSTS="${ALLOWED_HOSTS:-$DEFAULT_HOSTS}"
    ENV_COMPANY="${COMPANY_NAME:-Murphys Bench}"
    ENV_STAMP="$(date '+%F %T')"
    cat > .env <<ENVEOF
# Murphy's Bench environment — generated by scripts/setup.sh on ${ENV_STAMP}.
# This file holds all secrets. Never commit it. Keep perms at 600.
DEBUG=False
SECRET_KEY=${SECRET_KEY}
FIELD_ENCRYPTION_KEY=${FERNET_KEY}
ALLOWED_HOSTS=${ENV_HOSTS}
COMPANY_NAME=${ENV_COMPANY}
TIMEZONE=America/Los_Angeles

# Database: SQLite by default (a file at db.sqlite3 — no DB server needed).
# To use PostgreSQL instead, see INSTALL.md step 5 and set DB_ENGINE=... here.

# HTTPS hardening — explicitly OFF for this plain-HTTP LAN install.
# SESSION_COOKIE_SECURE and CSRF_COOKIE_SECURE default to "not DEBUG" in
# settings.py, i.e. True whenever DEBUG=False — which would silently break
# login/session/CSRF here, since a browser won't send a Secure cookie over
# plain HTTP. Set explicitly rather than relying on that default.
# Turn these ON only once TLS is confirmed end-to-end (reverse proxy /
# Cloudflare Tunnel) — see INSTALL.md "Going public (remote access)".
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
SECURE_HSTS_SECONDS=0
# CSRF_TRUSTED_ORIGINS=https://your.hostname
ENVEOF
    chmod 600 .env
    log ".env created (chmod 600)"
fi

# 6) Build the self-hosted Tailwind stylesheet BEFORE collectstatic (no Node).
log "building CSS (self-hosted Tailwind)..."
"$APP/scripts/build_css.sh" || fail "CSS build failed"

# 7) Initialize Django.
log "running migrations..."
"$VENV/python" manage.py migrate --noinput || fail "migrate failed"
log "collecting static files..."
"$VENV/python" manage.py collectstatic --noinput >/dev/null || fail "collectstatic failed"

# 8) Superuser (interactive, only if none exists yet).
HAS_SU="$("$VENV/python" manage.py shell -c \
    'from django.contrib.auth import get_user_model as g; print(g().objects.filter(is_superuser=True).exists())' \
    2>/dev/null | tail -1 || echo True)"
if [ "$HAS_SU" = "False" ]; then
    if [ "$NOINPUT" = 1 ]; then
        log "no superuser yet — skipping (--noinput). Create one later: venv/bin/python manage.py createsuperuser"
    else
        log "no superuser found — creating one now (Ctrl-C to skip)."
        "$VENV/python" manage.py createsuperuser || log "createsuperuser skipped/failed — create one later with: venv/bin/python manage.py createsuperuser"
    fi
else
    log "superuser already exists — skipping createsuperuser"
fi

# 9) Smoke checks.
log "running deploy check (HTTPS warnings are expected on a plain-HTTP box)..."
"$VENV/python" manage.py check || fail "manage.py check failed"
if [ "$SKIP_TESTS" = 0 ]; then
    log "running the test suite (smoke)..."
    "$VENV/python" -m pytest -q || fail "test suite failed — do not deploy until green"
else
    log "skipping tests (--skip-tests)"
fi

# 10) Web server — gunicorn (systemd) + nginx, so the app is actually reachable
# in a browser without hand-editing config files. Plain HTTP, LAN-only by
# default (same posture as Murphy's Bench's own production instance) — see
# INSTALL.md "Going public" for a domain/TLS/Cloudflare Tunnel, which is a
# separate, optional step, not required to log in over the LAN.
#
# Binds gunicorn to TCP 127.0.0.1:8001 rather than a unix socket — this is
# the known-good choice from the demo box's real stand-up (deploy/demo/):
# a unix socket needs nginx's www-data user to have permission to read it,
# which varies by how the app directory is owned, while a loopback TCP port
# needs no permission wrangling and works the same on any box.
if [ "$SKIP_WEB" = 0 ]; then
    command -v systemctl >/dev/null || fail "--skip-web wasn't passed but this host has no systemd; \
re-run with --skip-web and wire up your own process manager/reverse proxy"
    command -v nginx >/dev/null || fail "--skip-web wasn't passed but nginx isn't installed; \
either drop --skip-apt so this script installs it, or pass --skip-web to wire up your own"

    log "writing gunicorn systemd unit (sudo)..."
    sudo tee /etc/systemd/system/murphys-bench.service >/dev/null <<UNITEOF
[Unit]
Description=Murphy's Bench (Gunicorn)
After=network.target

[Service]
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${APP}
ExecStart=${VENV}/gunicorn --workers 3 --bind 127.0.0.1:8001 \\
    --access-logfile ${APP}/logs/gunicorn-access.log \\
    --error-logfile ${APP}/logs/gunicorn-error.log \\
    murphys_bench.wsgi:application
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
UNITEOF
    sudo systemctl daemon-reload || fail "systemctl daemon-reload failed"
    sudo systemctl enable --now murphys-bench || fail "starting the gunicorn service failed"
    log "gunicorn service enabled + started"

    log "writing nginx site (sudo)..."
    sudo tee /etc/nginx/sites-available/murphys-bench >/dev/null <<NGINXEOF
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 50M;

    location /static/ { alias ${APP}/staticfiles/; }
    location /media/  { alias ${APP}/media/; }

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINXEOF
    sudo rm -f /etc/nginx/sites-enabled/default
    sudo ln -sf /etc/nginx/sites-available/murphys-bench /etc/nginx/sites-enabled/murphys-bench
    sudo nginx -t || fail "nginx config test failed — check /etc/nginx/sites-available/murphys-bench"
    sudo systemctl reload nginx 2>/dev/null || sudo systemctl restart nginx || fail "nginx reload/restart failed"
    log "nginx site enabled + reloaded"
else
    log "skipping web server setup (--skip-web) — app layer only"
fi

# 11) Done.
cat <<DONE

$(date '+%F %T') setup: DONE

⚠ SAVE YOUR ENCRYPTION KEY
  FIELD_ENCRYPTION_KEY in $APP/.env protects all stored credentials. If you lose
  it, encrypted data is permanently unrecoverable. Copy it into a password
  manager NOW (also save SECRET_KEY).

DONE
if [ "$SKIP_WEB" = 0 ]; then
    cat <<DONE2
Murphy's Bench is running at:
  http://${LAN_IP:-<this-box-s-LAN-IP>}/

Log in as the superuser you just created. This is plain HTTP on your local
network — the same way MB's own production instance runs. Nothing further is
required to use it day to day.

Optional next steps (not required to log in):
  - Scheduled-job timers: backup / inbound-email / SLA  (deploy/README.md)
  - A public domain with TLS (Cloudflare Tunnel or otherwise) — see
    INSTALL.md "Going public (remote access)" — only if you need to reach
    this instance from outside your network.
DONE2
else
    cat <<DONE3
Web server setup was skipped (--skip-web). The app layer is ready at $APP —
wire up your own process manager and reverse proxy to reach it, or re-run
without --skip-web if this box turns out to be a normal systemd+nginx host.
DONE3
fi
