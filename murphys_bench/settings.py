"""
Django settings for Murphy's Bench - Internal Field Service Management

For internal network deployment (not cloud/public internet).
Configuration is environment-based (dev/production).
"""

from pathlib import Path
from decouple import config, Csv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Environment and deployment mode
ENVIRONMENT = config('ENVIRONMENT', default='development')
# Production-safe default: DEBUG is OFF unless explicitly enabled. Local dev sets
# DEBUG=True in .env; a forgotten DEBUG in production therefore fails safe (off).
DEBUG = config('DEBUG', default=False, cast=bool)

# SECURITY WARNING: keep the secret key used in production secret!
# Generate with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
# This default is committed to the repo and is therefore NOT secret — the guard
# below refuses to start with it when DEBUG=False.
_DEFAULT_SECRET_KEY = 'django-insecure-change-me-in-production-@ddk6oxul(ace7rj@37hx-ieizb)@f1j+w5@8px%!r287b_ef&'
SECRET_KEY = config('SECRET_KEY', default=_DEFAULT_SECRET_KEY)

# Field-level encryption key for sensitive data (device credentials, email passwords).
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# MUST be set in production .env — never use the default in production.
_DEFAULT_FIELD_ENCRYPTION_KEY = 'nCmoQA0nD3vW1siXNm3Gvp1lXzN8KItaIMQGwjgijpE='
FIELD_ENCRYPTION_KEY = config('FIELD_ENCRYPTION_KEY', default=_DEFAULT_FIELD_ENCRYPTION_KEY)

# Fail loud rather than silently running production with publicly-known secrets.
if not DEBUG:
    from django.core.exceptions import ImproperlyConfigured
    _insecure_defaults = []
    if SECRET_KEY == _DEFAULT_SECRET_KEY:
        _insecure_defaults.append('SECRET_KEY')
    if FIELD_ENCRYPTION_KEY == _DEFAULT_FIELD_ENCRYPTION_KEY:
        _insecure_defaults.append('FIELD_ENCRYPTION_KEY')
    if _insecure_defaults:
        raise ImproperlyConfigured(
            'Refusing to start with DEBUG=False and default '
            + ' and '.join(_insecure_defaults)
            + '. Set real value(s) in .env before running in production.\n'
            '  SECRET_KEY:           python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"\n'
            '  FIELD_ENCRYPTION_KEY: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )

# The guard above catches the *committed default*; this catches a key that is set
# but isn't a usable Fernet key at all — the common first-install mistake, since
# .env.example ships a placeholder string and copying it verbatim leaves DEBUG=True,
# which skips the guard above entirely. Without this, the failure surfaces ~40 lines
# deep in an import traceback ("Fernet key must be 32 url-safe base64-encoded bytes")
# during the first manage.py command, with no hint of how to fix it. Checked in every
# mode, dev included, because the placeholder is unusable in dev too.
try:
    from cryptography.fernet import Fernet as _Fernet
    _Fernet(FIELD_ENCRYPTION_KEY.encode() if isinstance(FIELD_ENCRYPTION_KEY, str)
            else FIELD_ENCRYPTION_KEY)
except Exception as _key_err:
    from django.core.exceptions import ImproperlyConfigured
    raise ImproperlyConfigured(
        f'FIELD_ENCRYPTION_KEY in .env is not a valid encryption key ({_key_err}).\n'
        '  Generate one:  python -c "from cryptography.fernet import Fernet; '
        'print(Fernet.generate_key().decode())"\n'
        '  Then set FIELD_ENCRYPTION_KEY in .env to that value.\n'
        '  Keep a copy somewhere safe — without it, stored credentials cannot be decrypted.'
    ) from None

# Allowed hosts for internal network
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1', cast=Csv())

# Application definition
INSTALLED_APPS = [
    # Django apps
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'encrypted_model_fields',
    'django_extensions',
    'auditlog',
    'axes',
    'django_otp',
    'django_otp.plugins.otp_totp',
    'django_otp.plugins.otp_static',
    'two_factor',

    # Murphy's Bench apps
    'accounts.apps.AccountsConfig',
    'core.apps.CoreConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'core.middleware.ContentSecurityPolicyMiddleware',
    'core.middleware.NegativePriceMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'axes.middleware.AxesMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'auditlog.middleware.AuditlogMiddleware',
    'core.middleware.MFAEnforcementMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'murphys_bench.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.site_settings',
            ],
        },
    },
]

WSGI_APPLICATION = 'murphys_bench.wsgi.application'

# Database Configuration
# Murphy's Bench runs on SQLite (WAL mode) in dev and production — a deliberate
# decision for a single-node, small-shop deployment.
# The off-site backup is a consistent SQLite snapshot (scripts/mb_backup.sh).
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
        'OPTIONS': {
            'min_length': 12,
        }
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Custom User Model
AUTH_USER_MODEL = 'core.User'

# Internationalization
LANGUAGE_CODE = 'en-us'

TIME_ZONE = config('TIMEZONE', default='America/Los_Angeles')

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']

# Media files (uploads)
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

# Private attachment storage — deliberately OUTSIDE MEDIA_ROOT so nginx never
# serves these files directly. All attachment access goes through the
# authenticated, authorization-checked AttachmentDownloadView. See the
# attachment security review (memory project_mb_attachment_security_review).
PRIVATE_MEDIA_ROOT = BASE_DIR / 'protected'

# Single-runner lock for the inbound mail fetch. Overlapping fetches racing on the
# same message is what produced duplicate tickets (session 37), so the real job must
# keep one fixed path per install. It is a SETTING only so the test suite can point it
# at a temp file — otherwise a concurrent test run (or, on a box where the fetch timer
# is enabled, the timer itself) steals the lock and the dedup test fails spuriously.
INBOUND_FETCH_LOCK_PATH = BASE_DIR / 'inbound_fetch.lock'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Email Configuration
# For ticket ingestion via email, and sending notifications
EMAIL_BACKEND = config('EMAIL_BACKEND', default='django.core.mail.backends.console.EmailBackend')

if EMAIL_BACKEND != 'django.core.mail.backends.console.EmailBackend':
    EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
    EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
    EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
    EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
    EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')

# Attachments
ATTACHMENT_STORAGE_BACKEND = config('ATTACHMENT_STORAGE_BACKEND', default='local')

# Django 5.1 removed DEFAULT_FILE_STORAGE in favour of the STORAGES dict, so both
# keys are always restated here. In production, staticfiles uses
# ManifestStaticFilesStorage (content-hashed filenames, e.g. app.3f9a1c.css) so
# a deploy that only changes CSS/JS always busts any browser cache — update.sh
# already runs collectstatic after every build, which is this backend's only
# requirement. Without this, a browser can keep serving a pre-deploy stylesheet
# indefinitely since the static URL never changes (found live: a new purple
# button rendered with no text/color at all until a hard-refresh, mid the
# Estimate-options session). Local dev (DEBUG=True) keeps the plain backend —
# `manage.py runserver` never runs collectstatic, so Manifest's hashed-name
# lookup would 500 on `{% static %}` for anyone who hasn't run it by hand.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': (
        'django.contrib.staticfiles.storage.StaticFilesStorage' if DEBUG
        else 'django.contrib.staticfiles.storage.ManifestStaticFilesStorage'
    )},
}

if ATTACHMENT_STORAGE_BACKEND == 's3':
    STORAGES['default'] = {'BACKEND': 'storages.backends.s3boto3.S3Boto3Storage'}
    AWS_STORAGE_BUCKET_NAME = config('S3_BUCKET_NAME', default='')
    AWS_ACCESS_KEY_ID = config('S3_ACCESS_KEY', default='')
    AWS_SECRET_ACCESS_KEY = config('S3_SECRET_KEY', default='')
    AWS_S3_ENDPOINT_URL = config('S3_ENDPOINT_URL', default='') or None
    AWS_S3_REGION_NAME = config('S3_REGION', default='') or None
    AWS_DEFAULT_ACL = 'private'
    AWS_QUERYSTRING_AUTH = True

# Ticket locking (collision avoidance)
TICKET_LOCK_TIMEOUT_MINUTES = config('TICKET_LOCK_TIMEOUT_MINUTES', default=10, cast=int)

# WO/Ticket dependency — auto-resolve ticket when linked WO closes
AUTO_RESOLVE_TICKET_ON_WO_CLOSE = config('AUTO_RESOLVE_TICKET_ON_WO_CLOSE', default=False, cast=bool)

# Default from email for notifications
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='noreply@murphys-bench.local')
SERVER_EMAIL = config('SERVER_EMAIL', default='noreply@murphys-bench.local')

# ── Reverse-proxy trust — OPT-IN ────────────────────────────────────────────
# MB never terminates TLS; it runs behind a proxy (Cloudflare, nginx, Caddy). When
# it does, Django has to trust X-Forwarded-Proto to know the original request was
# HTTPS, and X-Forwarded-Host to build correct absolute URLs.
#
# ⚠ THIS IS OPT-IN, and must stay opt-in. These headers are trivially forged by
# whoever can reach the app directly. Trusting them unconditionally means any
# deployment where the app port is reachable without a sanitizing proxy in front
# will believe an attacker-supplied Host — which poisons absolute URLs in emails
# and password-reset style links. It was unconditional until the July 2026 review;
# that was safe for the author's own nginx deployment and unsafe as a shipped
# default for everyone else.
#
# Set TRUST_PROXY_HEADERS=True ONLY when a reverse proxy you control sits in front
# and overwrites (not appends to) these headers. See docs/deployment-tls.md.
TRUST_PROXY_HEADERS = config('TRUST_PROXY_HEADERS', default=False, cast=bool)
if TRUST_PROXY_HEADERS:
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    USE_X_FORWARDED_HOST = True

# Security Settings

if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    # HSTS and HTTPS-redirect are opt-in via .env. Enabling HSTS is hard to undo
    # (browsers cache it), so turn it on only once HTTPS is confirmed end-to-end
    # (Cloudflare + nginx). Defaults keep current behavior; flip in .env when ready:
    #   SECURE_SSL_REDIRECT=True
    #   SECURE_HSTS_SECONDS=31536000
    SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=False, cast=bool)
    SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=0, cast=int)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = config('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=True, cast=bool)
    SECURE_HSTS_PRELOAD = config('SECURE_HSTS_PRELOAD', default=True, cast=bool)

# CSRF trusted origins — required for any domain other than ALLOWED_HOSTS (e.g. Cloudflare tunnel URL).
# Add your public hostname here: CSRF_TRUSTED_ORIGINS=https://mb.yourdomain.com
_csrf_origins = config('CSRF_TRUSTED_ORIGINS', default='', cast=Csv())
if _csrf_origins:
    CSRF_TRUSTED_ORIGINS = list(_csrf_origins)

# Session security
SESSION_COOKIE_SECURE = config('SESSION_COOKIE_SECURE', default=not DEBUG, cast=bool)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'  # Strict breaks Cloudflare redirects; Lax is safe
SESSION_COOKIE_AGE = config('SESSION_COOKIE_AGE', default=28800, cast=int)  # 8 hours

# CSRF security
CSRF_COOKIE_SECURE = config('CSRF_COOKIE_SECURE', default=not DEBUG, cast=bool)
CSRF_COOKIE_HTTPONLY = True

# ── Content-Security-Policy ─────────────────────────────────────────────────
# Emitted by core.middleware.ContentSecurityPolicyMiddleware. The front-end is
# fully self-hosted (no CDN), so every fetchable origin is 'self'. script-src
# keeps 'unsafe-eval'/'unsafe-inline' BY NECESSITY: Alpine.js evaluates its 400+
# template expressions via new Function() and the app has inline <script> blocks
# and inline event handlers. The real hardening is in the other directives —
# default-src/connect-src 'self' (an injected script can't exfiltrate cross-origin),
# frame-ancestors 'none' (clickjacking), object-src 'none', base-uri/form-action 'self'.
# SHIPS ENFORCING. It used to default to report-only, which meant it enforced
# nothing on every install except the two boxes whose .env said otherwise — the
# author's. A security header that protects only its author is not a shipped
# control. The policy has been validated in production since v0.4.0.
# Escape hatch if a deployment genuinely breaks: CSP_REPORT_ONLY=True in .env
# (violations still report to /csp-report/), or CSP_POLICY='' to disable outright.
CSP_POLICY = config('CSP_POLICY', default=(
    "default-src 'self'; "
    "script-src 'self' 'unsafe-eval' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
))
CSP_REPORT_ONLY = config('CSP_REPORT_ONLY', default=False, cast=bool)

# ── Login brute-force protection (django-axes) ──────────────────────────────
AXES_FAILURE_LIMIT = config('AXES_FAILURE_LIMIT', default=5, cast=int)
AXES_COOLOFF_TIME = 1          # hours before lockout clears automatically
AXES_LOCKOUT_CALLABLE = None   # use default 403 response
AXES_RESET_ON_SUCCESS = True   # clear failure count on successful login
AXES_ENABLE_ADMIN = True       # allow admin to view/reset lockouts
AXES_LOCKOUT_PARAMETERS = ['ip_address', 'username']  # lock by IP + username combo
AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesStandaloneBackend',
    'django.contrib.auth.backends.ModelBackend',
]

# Logging
#
# Where the application log is written. This is a seam for the TEST SUITE ONLY: the path used
# to be fixed, so every run appended its own manufactured failures ("Outbound email test failed
# for host smtp.example.com") to the exact file Murphy's Bench tells an operator to read when
# something breaks. conftest.py points MB_LOG_FILE at a temp file for the duration of a run.
# No install sets it, so every real deployment logs exactly where it always has.
_log_file = config('MB_LOG_FILE', default='')
LOG_FILE = Path(_log_file) if _log_file else BASE_DIR / 'logs' / 'murphys_bench.log'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {asctime} {module} {message}',
            'style': '{',
        },
    },
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        },
        'require_debug_true': {
            '()': 'django.utils.log.RequireDebugTrue',
        },
    },
    'handlers': {
        'console': {
            'level': 'INFO',
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
        'file': {
            'level': 'INFO',
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': LOG_FILE,
            'maxBytes': 1024 * 1024 * 10,  # 10MB
            'backupCount': 5,
            'formatter': 'verbose',
        },
        'system_alert': {
            'level': 'ERROR',
            'class': 'core.log_handlers.SystemAlertHandler',
            'filters': ['require_debug_false'],  # production only
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console', 'file'],
            'level': 'INFO',
        },
        'core': {
            'handlers': ['console', 'file'],
            'level': 'DEBUG',
        },
        'django.request': {
            'handlers': ['console', 'file', 'system_alert'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}

# Authentication
LOGIN_URL = '/account/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/account/login/'

# Two-factor auth
TWO_FACTOR_REMEMBER_COOKIE_AGE = None  # Don't remember — always ask on new session

# Pagination
ITEMS_PER_PAGE = 25

# Date/time formatting
DATE_FORMAT = 'M d, Y'
TIME_FORMAT = 'H:i'
DATETIME_FORMAT = 'M d, Y H:i'
