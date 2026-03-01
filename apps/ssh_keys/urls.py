from django.urls import path

from apps.ssh_keys.views import SSHKeyDetailView, SSHKeyListCreateView

urlpatterns = [
    path('', SSHKeyListCreateView.as_view(), name='ssh-key-list-create'),
    path('<uuid:pk>/', SSHKeyDetailView.as_view(), name='ssh-key-detail'),
]
