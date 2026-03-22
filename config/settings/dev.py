"""
Development settings - enables debug mode, verbose logging, etc.
"""
from .base import *  # noqa: F401, F403

DEBUG = True

# Allow all hosts in development
ALLOWED_HOSTS = ['*']

# Dev-only apps
INSTALLED_APPS += [  # noqa: F405
    'debug_toolbar',
    'django_extensions',
]

MIDDLEWARE += [  # noqa: F405
    'debug_toolbar.middleware.DebugToolbarMiddleware',
]

INTERNAL_IPS = ['127.0.0.1', '0.0.0.0']

# Use real SMTP in dev (Mailtrap sandbox)
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

# Logging - verbose in dev
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '[{levelname}] {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'INFO',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'django.db.backends': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'apps': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
    },
}

# Relaxed JWT for dev
from datetime import timedelta  # noqa: E402
SIMPLE_JWT = {  # noqa: F405
    **SIMPLE_JWT,  # noqa: F405
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
}

# Throttle rates permisivos en desarrollo local
REST_FRAMEWORK['DEFAULT_THROTTLE_RATES'].update({  # noqa: F405
    'login': '100/minute',
    'register': '100/hour',
    'mfa': '100/minute',
    'forgot_password': '100/hour',
})
