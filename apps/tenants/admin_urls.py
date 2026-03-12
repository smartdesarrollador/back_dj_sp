from django.urls import path
from apps.tenants.admin_views import ClientListView, SuspendClientView

urlpatterns = [
    path('', ClientListView.as_view(), name='admin-client-list'),
    path('<uuid:pk>/suspend/', SuspendClientView.as_view(), name='admin-client-suspend'),
]
