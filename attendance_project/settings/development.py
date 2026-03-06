"""
Development settings for attendance_project.

Uses SQLite and enables debug mode.
"""

import os
from .base import *  # noqa: F401,F403
from .base import BASE_DIR

SECRET_KEY = os.environ.get(
    'DJANGO_SECRET_KEY',
    'django-insecure-dev-only-rqzuhc998cp!psi-(h45%jf585$jut)f11+0ll3#y00a0x)myh'
)

DEBUG = True

ALLOWED_HOSTS = ['localhost', '127.0.0.1', '172.16.16.105', '172.16.16.107']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# In development, log to console only (file handler may not have logs/ dir)
LOGGING['handlers'] = {  # noqa: F405
    'console': {
        'class': 'logging.StreamHandler',
        'formatter': 'verbose',
    },
}
for logger_config in LOGGING['loggers'].values():  # noqa: F405
    logger_config['handlers'] = ['console']
