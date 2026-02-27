"""
Root URL configuration.

API layout:
  /api/v1/auth/         → Authentication (login, register, MFA)
  /api/v1/admin/        → Admin panel (users, roles, billing, audit)
  /api/v1/app/          → App endpoints (projects, tasks, calendar, notes…)
  /api/v1/public/       → Public endpoints (no auth required)
  /api/v1/support/      → Support tickets
  /api/schema/          → OpenAPI schema
  /api/docs/            → Swagger UI
  /api/redoc/           → ReDoc
  /api/health/          → Health check
"""
from django.contrib import admin
from django.urls import path, include
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)
from core.views import health_check

urlpatterns = [
    # Django admin
    path('admin/', admin.site.urls),

    # Authentication
    path('api/v1/auth/', include('apps.auth_app.urls')),

    # Admin API (tenant management)
    path('api/v1/admin/', include([
        path('users/', include('apps.auth_app.admin_urls')),
        path('roles/', include('apps.rbac.urls')),
        path('billing/', include('apps.subscriptions.urls')),
        path('audit-logs/', include('apps.audit.urls')),
    ])),

    # App API (per-user resources)
    path('api/v1/app/', include([
        path('projects/', include('apps.projects.urls')),
        path('tasks/', include('apps.tasks.urls')),
        path('calendar/', include('apps.calendar_app.urls')),
        path('notes/', include('apps.notes.urls')),
        path('contacts/', include('apps.contacts.urls')),
        path('bookmarks/', include('apps.bookmarks.urls')),
        path('env-vars/', include('apps.env_vars.urls')),
        path('ssh-keys/', include('apps.ssh_keys.urls')),
        path('ssl-certs/', include('apps.ssl_certs.urls')),
        path('snippets/', include('apps.snippets.urls')),
        path('forms/', include('apps.forms_app.urls')),
        path('digital/', include('apps.digital_services.urls')),
        path('reports/', include('apps.analytics.urls')),
        path('sharing/', include('apps.sharing.urls')),
    ])),

    # Public endpoints (no auth)
    path('api/v1/public/', include('apps.digital_services.public_urls')),

    # Support
    path('api/v1/support/', include('apps.support.urls')),

    # Health check
    path('api/health/', health_check, name='health_check'),

    # OpenAPI / Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]
