"""
Django settings for Murphy's Bench - Internal Field Service Management

For internal network deployment (not cloud/public internet).
Configuration is environment-based (dev/production).
"""

from pathlib import Path
import os
from decouple import config, Csv

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Environment and deployment mode
ENVIRONMENT = config('ENVIRONMENT', default='development')
DEBUG = config('DEBUG', default=True, cast=bool)

# SECURITY WARNING: keep the secret key used in production secret!
# Generate with: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
SECRET_KEY = config(
    'SECRET_KEY',
    default='django-insecure-change-me-in-production-@ddk6oxul(ace7rj@37hx-ieizb)@f1j+w5@8px%!r287b_ef&'
)

# Field-level encryption key for sensitive data (device credentials, email passwords).
# Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# MUST be set in production .env — never use the default in production.
FIELD_ENCRYPTION_KEY = config(
    'FIELD_ENCRYPTION_KEY',
    default='nCmoQA0nD3vW1siXNm3Gvp1lXzN8KItaIMQGwjgijpE='
)

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
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
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
# For development: SQLite (quick testing)
# For production: PostgreSQL (recommended)
# Set DB_ENGINE env var to switch

DB_ENGINE = config('DB_ENGINE', default='sqlite3')

if DB_ENGINE == 'postgresql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': config('DB_NAME', default='murphys_bench'),
            'USER': config('DB_USER', default='postgres'),
            'PASSWORD': config('DB_PASSWORD', default=''),
            'HOST': config('DB_HOST', default='localhost'),
            'PORT': config('DB_PORT', default='5432'),
        }
    }
else:
    # SQLite for development
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
            'min_length': 8,
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

if ATTACHMENT_STORAGE_BACKEND == 's3':
    DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
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

# Security Settings
# Internal network deployment - stricter than default, but not cloud-paranoid
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_SECURITY_POLICY = {
        'default-src': ("'self'",),
        'script-src': ("'self'", "'unsafe-inline'"),  # HTMX requires inline
        'style-src': ("'self'", "'unsafe-inline'"),   # Tailwind requires inline
    }

# Session security
SESSION_COOKIE_SECURE = config('SESSION_COOKIE_SECURE', default=not DEBUG, cast=bool)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Strict'

# CSRF security
CSRF_COOKIE_SECURE = config('CSRF_COOKIE_SECURE', default=not DEBUG, cast=bool)
CSRF_COOKIE_HTTPONLY = True

# Logging
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
            'filename': BASE_DIR / 'logs' / 'murphys_bench.log',
            'maxBytes': 1024 * 1024 * 10,  # 10MB
            'backupCount': 5,
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
    },
}

# Authentication
LOGIN_URL = '/account/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/account/login/'

# Two-factor auth
TWO_FACTOR_REMEMBER_COOKIE_AGE = None  # Don't remember — always ask on new session

# Application Settings
COMPANY_NAME = config('COMPANY_NAME', default='Shamrock Computer Services')

# Pagination
ITEMS_PER_PAGE = 25

# Date/time formatting
DATE_FORMAT = 'M d, Y'
TIME_FORMAT = 'H:i'
DATETIME_FORMAT = 'M d, Y H:i'
