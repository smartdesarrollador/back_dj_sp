from django.urls import path
from apps.tenants.admin_views import OrganizationView

urlpatterns = [
    path('', OrganizationView.as_view(), name='admin-organization'),
]
