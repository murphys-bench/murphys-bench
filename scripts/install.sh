#!/usr/bin/env bash
# One-shot installer for Murphy's Bench. Takes a fresh box from "code is here"
# to "log in at http://<this-box>/ over the LAN" in one fail-loud command —
# app deps, database, AND the web server (gunicorn + nginx) that puts it on
# port 80. No code-reading, no hand-editing systemd/nginx files.
#
#   git clone <repo> /opt/murphys-bench
#   cd /opt/murphys-bench && scripts/install.sh
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
    *) echo "install: unknown arg '$a'" >&2; exit 2 ;;
  esac
done

log()  { echo "$(date '+%F %T') install: $*"; }
fail() { echo "INSTALL FAILED: $*" >&2; exit 1; }

# Poll a URL until it answers as expected, then echo the final status code.
#
# ⚠ EVERY post-reload HTTP check MUST go through this. `systemctl reload nginx`
# returns when the signal is sent, not when the new config is serving — nginx
# drains old workers asynchronously, so an immediate probe can be answered by a
# worker still holding the previous config. The v0.4.52 static check probed once
# with no retry and duly failed a perfectly correct install with HTTP 404, while
# the identical request returned 200 moments later.
#
#   $1 url   $2 expected code, or a case glob like '2*|3*'   $3 optional Host header
http_probe() {
    _url="$1"; _want="$2"; _host="${3:-}"; _code=000
    for _i in $(seq 1 15); do
        # No `|| echo 000` here: curl -w already prints 000 when it cannot connect,
        # so appending another produced the nonsense code "000000" in the failure
        # message. Normalise an empty result instead.
        if [ -n "$_host" ]; then
            _code="$(curl -s -o /dev/null -w '%{http_code}' -H "Host: $_host" "$_url" 2>/dev/null || true)"
        else
            _code="$(curl -s -o /dev/null -w '%{http_code}' "$_url" 2>/dev/null || true)"
        fi
        [ -n "$_code" ] || _code=000
        # shellcheck disable=SC2254
        case "$_code" in $_want) echo "$_code"; return 0 ;; esac
        sleep 1
    done
    echo "$_code"
}

# 403 and 404 have different causes and sending someone after the wrong one wastes
# their evening. The original message assumed permissions for every failure.
static_probe_hint() {
    case "$1" in
        403) cat <<'HINT'
  nginx found the file but is not allowed to read it, so the login page would
  render as unstyled HTML with no logo. A directory above the app denies access
  to the www-data user — Ubuntu home directories have been mode 750 since 21.04,
  which bites any install under /home.
  Check with:  sudo -u www-data stat STATICDIR
HINT
        ;;
        404) cat <<'HINT'
  nginx is running but has no route to that file — this is a config or path
  problem, NOT permissions. Confirm the site is the enabled one and its
  /static/ alias points at this install:
    sudo nginx -T | grep -A2 'location /static/'
HINT
        ;;
        000) cat <<'HINT'
  nginx did not answer at all on 127.0.0.1:80. Is it running, and is anything
  else already bound to port 80?
    sudo systemctl status nginx
    sudo ss -ltnp | grep :80
HINT
        ;;
        *) cat <<'HINT'
  nginx answered with an unexpected status for a plain static file. Check its
  error log:
    sudo tail -20 /var/log/nginx/error.log
HINT
        ;;
    esac
}

# NOTE: the working directory you ran this from is irrelevant — the script cd's to
# its own parent (see APP above). So "run it from the repo root" is useless advice
# and sent at least one tester chasing a directory problem that didn't exist. Say
# where it actually looked, why it looked there, and how to recover.
if [ ! -f manage.py ]; then
    fail "no manage.py in $APP

  This script installs the repository it lives inside. It looked in
    $APP
  because that is the parent of the scripts/ directory holding this file — the
  directory you ran the command from does not matter.

  That path is not a complete Murphy's Bench checkout. Usually this means the
  script was copied or downloaded on its own, or a clone was interrupted.

  Clone the whole repository, then run the copy inside it:
    git clone <REPO_URL> murphys-bench
    cd murphys-bench
    scripts/install.sh"
fi

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
    ENV_STAMP="$(date '+%F %T')"
    cat > .env <<ENVEOF
# Murphy's Bench environment — generated by scripts/install.sh on ${ENV_STAMP}.
# This file holds all secrets. Never commit it. Keep perms at 600.
DEBUG=False
SECRET_KEY=${SECRET_KEY}
FIELD_ENCRYPTION_KEY=${FERNET_KEY}
ALLOWED_HOSTS=${ENV_HOSTS}
TIMEZONE=America/Los_Angeles

# Database: SQLite (a file at db.sqlite3 — no DB server needed). This is the
# only supported database; there is no DB_ENGINE switch.

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

# Reverse-proxy trust — OFF, matching this direct-access LAN install.
# X-Forwarded-Proto / X-Forwarded-Host are forgeable by anyone who can reach the
# app port directly, so MB does not trust them unless you say so. Turn this ON
# only when a proxy YOU control (Cloudflare Tunnel, nginx, Caddy) sits in front
# and overwrites those headers. See docs/deployment-tls.md.
TRUST_PROXY_HEADERS=False

# Content-Security-Policy is ENFORCING by default (settings.py). Nothing to set
# here. If a deployment genuinely breaks, set CSP_REPORT_ONLY=True to fall back to
# report-only (violations still log to /csp-report/) and report the breakage.
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

    # Every systemd unit — gunicorn, the two .path units behind the in-app
    # Back up now / Update buttons, and the backup / inbound-email / SLA timers —
    # is rendered from the templates in deploy/ with this install's real path and
    # user. Installing only gunicorn here (as this script used to) left those
    # buttons spinning forever and scheduled backups never running, on every box
    # that wasn't the author's own.
    log "installing systemd units (sudo)..."
    "$APP/scripts/install_units.sh" || fail "installing systemd units failed"

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

    # nginx serves /static/ straight off disk as www-data, so www-data needs to be
    # able to traverse every directory from / down to the app. Ubuntu has created
    # home directories mode 750 since 21.04, so an install under ~ gives a working
    # login page with every stylesheet and image 403ing — the app looks broken in a
    # way that doesn't obviously point at permissions. Grant traverse on the chain.
    log "granting the web server traverse permission on $APP..."
    d="$APP"
    while [ "$d" != "/" ]; do
        sudo chmod o+x "$d" || fail "could not chmod o+x $d"
        d="$(dirname "$d")"
    done
    sudo chmod -R o+rX "$APP/staticfiles" || fail "could not make staticfiles world-readable"

    # Prove it rather than assume it. This is the exact check that would have
    # caught the unstyled-login bug before a tester ever saw it: ask nginx for a
    # real static file the way a browser does.
    #
    # ⚠ MUST RETRY. `systemctl reload nginx` returns as soon as the signal is sent;
    # nginx reloads workers ASYNCHRONOUSLY and old workers keep serving the OLD
    # config until they drain. Probing immediately races that, and this check did
    # exactly that from v0.4.52 until it lost the race on a real install: a correct
    # box reported "INSTALL FAILED ... HTTP 404" and the same request returned 200
    # moments later. An intermittent false failure is worse than no check — it
    # teaches people the installer is flaky and to ignore what it says.
    probe="$(ls "$APP"/staticfiles/css/*.css 2>/dev/null | head -1 || true)"
    [ -n "$probe" ] || fail "collectstatic produced no CSS in $APP/staticfiles/css — the UI would render unstyled"
    code="$(http_probe "http://127.0.0.1/static/css/$(basename "$probe")" 200)"
    [ "$code" = "200" ] || fail "nginx returned HTTP $code for /static/css/$(basename "$probe")
$(static_probe_hint "$code" | sed "s|STATICDIR|$APP/staticfiles|")"
    log "static files verified served by nginx (HTTP 200)"

    # And prove the APP itself answers, not just files nginx reads off disk.
    # /static/ is an alias served straight from the filesystem, so the check above
    # passes even when gunicorn is dead — which is how a re-run once printed DONE
    # and "Murphy's Bench is running at ..." over an app returning 502 to every
    # request. Restart first: `systemctl enable --now` does NOT restart an
    # already-running service, so a re-run that changed the unit would otherwise
    # leave the old process bound to the old socket while nginx talks to the new one.
    log "restarting the app so it picks up the installed unit..."
    sudo systemctl restart murphys-bench || fail "could not restart murphys-bench — see: journalctl -u murphys-bench"
    app_host="$(grep '^ALLOWED_HOSTS=' .env 2>/dev/null | cut -d= -f2- | cut -d, -f1)"
    app_code="$(http_probe "http://127.0.0.1/" '2*|3*' "${app_host:-localhost}")"
    case "$app_code" in
        2*|3*) log "app verified reachable through nginx (HTTP $app_code)" ;;
        *) fail "the app is not answering through nginx (HTTP $app_code).

  Static files are served by nginx straight off disk, so a stylesheet loading
  proves nothing about the application itself. Something is wrong between nginx
  and gunicorn. Check, in this order:
    sudo systemctl status murphys-bench
    journalctl -u murphys-bench -n 40
    sudo tail -20 /var/log/nginx/error.log" ;;
    esac
else
    log "skipping web server setup (--skip-web) — app layer only"
fi

# 11) Done.
cat <<DONE

$(date '+%F %T') install: DONE

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

Background jobs are installed and running — scheduled backups, inbound email,
SLA checks, and the in-app Back up now / Update buttons. Nothing to wire up by
hand. Confirm any time with:
  systemctl list-units 'murphys-bench-*'

Truly optional (not required for anything above):
  - Disk-space alerting: scripts/install_units.sh --with-disk-check, once
    notifications are configured (Settings → Notifications)
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
