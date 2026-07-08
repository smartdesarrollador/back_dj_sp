from django.urls import path

from apps.vault.views import (
    MasterPasswordView,
    VaultItemDetailView,
    VaultItemListCreateView,
    VaultItemShareDetailView,
    VaultItemShareView,
    VaultLockView,
    VaultRecoverView,
    VaultSharedItemRevealView,
    VaultSharedWithMeListView,
    VaultUnlockView,
)

urlpatterns = [
    path('master-password/', MasterPasswordView.as_view(), name='vault-master-password'),
    path('unlock/', VaultUnlockView.as_view(), name='vault-unlock'),
    path('lock/', VaultLockView.as_view(), name='vault-lock'),
    path('recover/', VaultRecoverView.as_view(), name='vault-recover'),
    path('items/', VaultItemListCreateView.as_view(), name='vault-item-list'),
    path('items/<uuid:pk>/', VaultItemDetailView.as_view(), name='vault-item-detail'),
    path('items/<uuid:pk>/share/', VaultItemShareView.as_view(), name='vault-item-share'),
    path(
        'items/<uuid:pk>/share/<uuid:share_id>/',
        VaultItemShareDetailView.as_view(),
        name='vault-item-share-detail',
    ),
    path('shared-with-me/', VaultSharedWithMeListView.as_view(), name='vault-shared-with-me'),
    path(
        'shared-with-me/<uuid:share_id>/',
        VaultSharedItemRevealView.as_view(),
        name='vault-shared-item-reveal',
    ),
]
