"""
Base Django settings - shared across all environments.
"""
import environ
from pathlib import Path

# Build paths
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load environment variables
env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, ['localhost', '127.0.0.1']),
)
environ.Env.read_env(BASE_DIR / '.env')

# Core
SECRET_KEY = env('SECRET_KEY')
DEBUG = env('DEBUG')
ALLOWED_HOSTS = env('ALLOWED_HOSTS')

# Application definition
DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.postgres',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'django_filters',
    'drf_spectacular',
    'django_celery_beat',
    'django_celery_results',
]

LOCAL_APPS = [
    'core',
    'apps.tenants',
    'apps.auth_app',
    'apps.rbac',
    'apps.subscriptions',
    'apps.projects',
    'apps.tasks',
    'apps.calendar_app',
    'apps.notes',
    'apps.contacts',
    'apps.bookmarks',
    'apps.env_vars',
    'apps.ssh_keys',
    'apps.ssl_certs',
    'apps.snippets',
    'apps.forms_app',
    'apps.audit',
    'apps.analytics',
    'apps.sharing',
    'apps.digital_services',
    'apps.support',
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'apps.tenants.middleware.TenantMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

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
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# Database
DATABASES = {
    'default': env.db('DATABASE_URL', default='postgres://rbac_user:rbac_pass@db:5432/rbac_db')
}

# Cache (Redis)
CACHES = {
    'default': {
        'BACKEND': 'django_redis.cache.RedisCache',
        'LOCATION': env('REDIS_URL', default='redis://redis:6379/0'),
        'OPTIONS': {
            'CLIENT_CLASS': 'django_redis.client.DefaultClient',
        },
        'KEY_PREFIX': 'rbac',
        'TIMEOUT': 300,
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 8}},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# Custom user model
AUTH_USER_MODEL = 'auth_app.User'

# Password hashers - Argon2id first
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.Argon2PasswordHasher',
    'django.contrib.auth.hashers.PBKDF2PasswordHasher',
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static / Media files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ─── Django REST Framework ────────────────────────────────────────────────────
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.CursorPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'utils.throttles.PlanBasedUserThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '60/minute',
        # Rates nombrados por endpoint sensible
        'login': '5/minute',
        'register': '3/hour',
        'mfa': '5/minute',
        'forgot_password': '5/hour',
    },
    'EXCEPTION_HANDLER': 'core.exceptions.custom_exception_handler',
}

# ─── JWT Settings ─────────────────────────────────────────────────────────────
from datetime import timedelta

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=env.int('JWT_ACCESS_TOKEN_LIFETIME_MINUTES', default=15)),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=env.int('JWT_REFRESH_TOKEN_LIFETIME_DAYS', default=7)),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
    'UPDATE_LAST_LOGIN': True,
    'ALGORITHM': 'HS256',
    'SIGNING_KEY': env('JWT_SECRET', default=SECRET_KEY),
    'AUTH_HEADER_TYPES': ('Bearer',),
    'USER_ID_FIELD': 'id',
    'USER_ID_CLAIM': 'user_id',
}

# ─── CORS ─────────────────────────────────────────────────────────────────────
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[
    'http://localhost:5173',
    'http://localhost:3000',
])
CORS_ALLOW_CREDENTIALS = True

# ─── Celery ───────────────────────────────────────────────────────────────────
CELERY_BROKER_URL = env('CELERY_BROKER_URL', default='redis://redis:6379/1')
CELERY_RESULT_BACKEND = env('CELERY_RESULT_BACKEND', default='redis://redis:6379/2')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

from celery.schedules import crontab  # noqa: E402

CELERY_BEAT_SCHEDULE = {
    'purge-old-audit-logs': {
        'task': 'apps.audit.tasks.purge_old_audit_logs',
        'schedule': crontab(hour=2, minute=0),  # Daily at 2:00 AM UTC
    },
}

# ─── Email ────────────────────────────────────────────────────────────────────
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = env('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = env.int('EMAIL_PORT', default=587)
EMAIL_USE_TLS = env.bool('EMAIL_USE_TLS', default=True)
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL', default='noreply@plataforma.com')

# ─── Stripe ───────────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY = env('STRIPE_SECRET_KEY', default='')
STRIPE_PUBLISHABLE_KEY = env('STRIPE_PUBLISHABLE_KEY', default='')
STRIPE_WEBHOOK_SECRET = env('STRIPE_WEBHOOK_SECRET', default='')

# Stripe Price IDs per plan and billing cycle
STRIPE_PLAN_PRICES: dict[str, dict[str, str]] = {
    'starter': {
        'monthly': env('STRIPE_PRICE_STARTER_MONTHLY', default=''),
        'annual':  env('STRIPE_PRICE_STARTER_ANNUAL',  default=''),
    },
    'professional': {
        'monthly': env('STRIPE_PRICE_PRO_MONTHLY', default=''),
        'annual':  env('STRIPE_PRICE_PRO_ANNUAL',  default=''),
    },
    'enterprise': {
        'monthly': env('STRIPE_PRICE_ENT_MONTHLY', default=''),
        'annual':  env('STRIPE_PRICE_ENT_ANNUAL',  default=''),
    },
}

# ─── Encryption ───────────────────────────────────────────────────────────────
ENCRYPTION_KEY = env('ENCRYPTION_KEY', default='')

# ─── App URLs ─────────────────────────────────────────────────────────────────
APP_BASE_URL = env('APP_BASE_URL', default='http://localhost:8000')
FRONTEND_URL = env('FRONTEND_URL', default='http://localhost:5173')

# ─── Security Headers ─────────────────────────────────────────────────────────
SECURE_BROWSER_XSS_FILTER = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
SECURE_CONTENT_TYPE_NOSNIFF = True

# ─── OpenAPI / Spectacular ────────────────────────────────────────────────────
SPECTACULAR_SETTINGS = {
    'TITLE': 'RBAC Subscription Platform API',
    'DESCRIPTION': 'Multi-tenant RBAC system with subscription billing. Manages users, roles, permissions, projects and productivity services.',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'SCHEMA_PATH_PREFIX': '/api/v1/',
    'COMPONENT_SPLIT_REQUEST': True,
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,
    },
    'SECURITY': [{'jwtAuth': []}],
    'TAGS': [
        {'name': 'auth', 'description': 'Authentication and MFA'},
        {'name': 'admin-users', 'description': 'User management (admin)'},
        {'name': 'admin-roles', 'description': 'Role and permission management'},
        {'name': 'admin-billing', 'description': 'Subscriptions and invoices'},
        {'name': 'app-projects', 'description': 'Projects and encrypted storage'},
        {'name': 'app-tasks', 'description': 'Tasks and Kanban boards'},
        {'name': 'app-calendar', 'description': 'Calendar events'},
        {'name': 'app-notes', 'description': 'Notes service'},
        {'name': 'app-contacts', 'description': 'Contacts service'},
        {'name': 'app-bookmarks', 'description': 'Bookmarks service'},
        {'name': 'app-devops', 'description': 'EnvVars, SSH Keys, SSL, Snippets'},
        {'name': 'app-forms', 'description': 'Forms and submissions'},
        {'name': 'app-digital', 'description': 'Digital profile and portfolio'},
        {'name': 'public', 'description': 'Public endpoints (no auth)'},
        {'name': 'support', 'description': 'Support tickets'},
        {'name': 'audit', 'description': 'Audit logs'},
        {'name': 'reports', 'description': 'Analytics and reports'},
    ],
}

# ─── AWS S3 (optional) ────────────────────────────────────────────────────────
AWS_ACCESS_KEY_ID = env('AWS_ACCESS_KEY_ID', default='')
AWS_SECRET_ACCESS_KEY = env('AWS_SECRET_ACCESS_KEY', default='')
AWS_STORAGE_BUCKET_NAME = env('AWS_STORAGE_BUCKET_NAME', default='')
AWS_S3_REGION_NAME = env('AWS_S3_REGION_NAME', default='us-east-1')
AWS_S3_CUSTOM_DOMAIN = f'{AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com' if AWS_STORAGE_BUCKET_NAME else ''
