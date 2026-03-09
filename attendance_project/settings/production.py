"""
Production settings for attendance_project.

Loads sensitive values from .env file for security.
Uses MySQL and enables security hardening.
"""

import os
from .base import *  # noqa: F401,F403
from .base import BASE_DIR, MIDDLEWARE

# ------------------------------------
# Load .env file
# ------------------------------------
_env_file = BASE_DIR / '.env'
_env_vars = {}
if _env_file.exists():
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                _env_vars[key.strip()] = value.strip().strip('"').strip("'")

# ------------------------------------
# Core settings from .env
# ------------------------------------
SECRET_KEY = _env_vars.get('SECRET_KEY', os.environ.get('DJANGO_SECRET_KEY', ''))
if not SECRET_KEY:
    raise ValueError("SECRET_KEY must be set in .env or DJANGO_SECRET_KEY environment variable")

ALLOWED_HOSTS = _env_vars.get(
    'ALLOWED_HOSTS',
    os.environ.get('ALLOWED_HOSTS', 'localhost,127.0.0.1')
).split(',')

DEBUG = False

# ------------------------------------
# Database
# ------------------------------------
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': _env_vars.get('DB_NAME', 'attendance_db'),
        'USER': _env_vars.get('DB_USER', 'attendance_user'),
        'PASSWORD': _env_vars.get('DB_PASSWORD', ''),
        'HOST': _env_vars.get('DB_HOST', 'localhost'),
        'PORT': _env_vars.get('DB_PORT', '3306'),
        'OPTIONS': {
            'charset': 'utf8mb4',
            'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
        },
        'CONN_MAX_AGE': 600,  # Keep connections alive for 10 minutes
    }
}

# ------------------------------------
# Middleware (add WhiteNoise for static files)
# ------------------------------------
MIDDLEWARE.insert(1, 'whitenoise.middleware.WhiteNoiseMiddleware')

# ------------------------------------
# Security hardening
# ------------------------------------
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
CSRF_COOKIE_SECURE = False    # Set True only when serving over HTTPS
SESSION_COOKIE_SECURE = False  # Set True only when serving over HTTPS
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# ------------------------------------
# Logging (production uses file handler)
# ------------------------------------
_log_dir = BASE_DIR / 'logs'
_log_dir.mkdir(exist_ok=True)
