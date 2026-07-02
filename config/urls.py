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
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve as media_serve
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)
from core.views import health_check
from apps.rbac.views import FeaturesView

urlpatterns = [
    # Django admin
    path('admin/', admin.site.urls),

    # Authentication
    path('api/v1/auth/', include('apps.auth_app.urls')),

    # Admin API (tenant management)
    path('api/v1/admin/', include([
        path('users/', include('apps.auth_app.admin_urls')),
        path('roles/', include('apps.rbac.urls')),
        path('permissions/', include('apps.rbac.permission_urls')),
        path('billing/', include('apps.subscriptions.urls')),
        path('subscriptions/', include('apps.subscriptions.subscription_urls')),
        path('audit-logs/', include('apps.audit.urls')),
        path('notifications/', include('apps.notifications.admin_urls')),
        path('clients/', include('apps.tenants.admin_urls')),
        path('releases/', include('apps.releases.admin_urls')),
        path('licenses/', include('apps.licenses.admin_urls')),
        path('organization/', include('apps.tenants.organization_urls')),
        path('yape/', include('apps.subscriptions.yape_admin_urls')),
        path('knowledge-base/', include('apps.chat_assistant.admin_urls')),
        path('footer/', include('apps.site_config.urls')),
        path('contact/', include('apps.contact.admin_urls')),
        path('catalog/', include('apps.catalog.admin_urls')),
        path('announcements/', include('apps.announcements.admin_urls')),
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
        path('services/', include('apps.services.urls')),
        path('referrals/', include('apps.referrals.urls')),
        path('notifications/', include('apps.notifications.hub_urls')),
        path('announcements/', include('apps.announcements.hub_urls')),
        path('team/', include('apps.auth_app.team_urls')),
        path('desktop-license/', include('apps.licenses.app_urls')),
        path('chat/', include('apps.chat.urls')),
        path('vault/', include('apps.vault.urls')),
        path('search/', include('apps.search.urls')),
        path('workspace/', include('apps.exports.urls')),
    ])),

    # Public endpoints (no auth)
    path('api/v1/public/', include([
        path('', include('apps.digital_services.public_urls')),
        path('plans/', include('apps.subscriptions.public_urls')),
        path('desktop/', include('apps.releases.public_urls')),
        path('desktop-license/', include('apps.licenses.public_urls')),
        path('branding/', include('apps.tenants.public_branding_urls')),
        path('yape-payment/', include('apps.subscriptions.yape_public_urls')),
        path('chat/', include('apps.chat_assistant.public_urls')),
        path('contact/', include('apps.contact.public_urls')),
        path('', include('apps.site_config.public_urls')),
        path('catalog/', include('apps.catalog.public_urls')),
        path('announcements/', include('apps.announcements.public_urls')),
    ])),

    # Support
    path('api/v1/support/', include('apps.support.urls')),

    # Plan features
    path('api/v1/features/', FeaturesView.as_view(), name='plan-features'),

    # Health check
    path('api/health/', health_check, name='health_check'),

    # OpenAPI / Documentation
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
    path('api/redoc/', SpectacularRedocView.as_view(url_name='schema'), name='redoc'),
]

# Serve media files directly via re_path (works regardless of DEBUG)
urlpatterns += [
    re_path(r'^media/(?P<path>.*)$', media_serve, {'document_root': settings.MEDIA_ROOT}),
]

# Django Debug Toolbar URLs (dev only) — required so the 'djdt' namespace
# resolves; otherwise the toolbar raises NoReverseMatch while rendering.
if settings.DEBUG and 'debug_toolbar' in settings.INSTALLED_APPS:
    import debug_toolbar

    urlpatterns += [path('__debug__/', include(debug_toolbar.urls))]
