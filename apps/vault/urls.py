from django.urls import path

from apps.vault.views import (
    MasterPasswordView,
    VaultItemDetailView,
    VaultItemListCreateView,
    VaultLockView,
    VaultRecoverView,
    VaultUnlockView,
)

urlpatterns = [
    path('master-password/', MasterPasswordView.as_view(), name='vault-master-password'),
    path('unlock/', VaultUnlockView.as_view(), name='vault-unlock'),
    path('lock/', VaultLockView.as_view(), name='vault-lock'),
    path('recover/', VaultRecoverView.as_view(), name='vault-recover'),
    path('items/', VaultItemListCreateView.as_view(), name='vault-item-list'),
    path('items/<uuid:pk>/', VaultItemDetailView.as_view(), name='vault-item-detail'),
]
