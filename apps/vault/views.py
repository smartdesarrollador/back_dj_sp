"""
Vault views — personal encrypted secrets gated by a master password.

URL namespace: /api/v1/app/vault/

Auth model: `IsAuthenticated` + per-user scoping (no seeded `vault.*` RBAC perms,
same approach as apps/chat and apps/support). Sensitive operations additionally
require an **unlocked** vault: the client sends the opaque unlock token in the
`X-Vault-Token` header; the server looks up the cached DEK or returns 423 Locked.
"""
import json
import secrets

from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import check_plan_limit
from apps.vault import crypto
from apps.vault.models import VaultItem, VaultKey
from apps.vault.serializers import (
    MasterPasswordChangeSerializer,
    MasterPasswordSetupSerializer,
    RecoverSerializer,
    UnlockSerializer,
    VaultItemCreateUpdateSerializer,
    VaultItemListSerializer,
)
from core.mixins import AuditMixin
from utils.encryption import decrypt_value, encrypt_value

_NOT_FOUND = Response({'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404)
_LOCKED = Response(
    {'error': {'code': 'vault_locked', 'message': 'La bóveda está bloqueada.'}},
    status=status.HTTP_423_LOCKED,
)

_UNLOCK_TTL = getattr(settings, 'VAULT_UNLOCK_TTL', 900)  # 15 min
_MAX_UNLOCK_FAILS = 5
_FAIL_WINDOW = 300  # 5 min lockout window


def _dek_key(user_id, token) -> str:
    return f'vault:dek:{user_id}:{token}'


def _fails_key(user_id) -> str:
    return f'vault:fails:{user_id}'


def _store_dek(user_id, dek: bytes) -> str:
    """Cache the DEK (encrypted with the global key) under a fresh unlock token."""
    token = secrets.token_urlsafe(32)
    cache.set(_dek_key(user_id, token), encrypt_value(crypto.dek_to_str(dek)), _UNLOCK_TTL)
    return token


def _get_dek(request) -> bytes | None:
    """Resolve the DEK from the X-Vault-Token header, or None if locked/invalid."""
    token = request.headers.get('X-Vault-Token')
    if not token:
        return None
    stored = cache.get(_dek_key(request.user.id, token))
    if not stored:
        return None
    return crypto.dek_from_str(decrypt_value(stored))


class MasterPasswordView(AuditMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-vault'], summary='Master password status')
    def get(self, request):
        configured = VaultKey.objects.filter(user=request.user).exists()
        return Response({
            'is_configured': configured,
            'is_unlocked': bool(configured and _get_dek(request)),
        })

    @extend_schema(tags=['app-vault'], summary='Set up the vault master password')
    def post(self, request):
        if VaultKey.objects.filter(user=request.user).exists():
            return Response(
                {'error': {'code': 'already_configured', 'message': 'La bóveda ya está configurada.'}},
                status=status.HTTP_409_CONFLICT,
            )
        serializer = MasterPasswordSetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        master = serializer.validated_data['master_password']

        dek = crypto.generate_dek()
        salt = crypto.generate_salt()
        recovery_code = secrets.token_urlsafe(24)
        recovery_salt = crypto.generate_salt()

        VaultKey.objects.create(
            user=request.user,
            tenant=request.tenant,
            salt=salt,
            wrapped_dek=crypto.wrap_dek(dek, crypto.derive_kek(master, salt)),
            master_verifier=make_password(master),
            recovery_salt=recovery_salt,
            wrapped_dek_recovery=crypto.wrap_dek(dek, crypto.derive_kek(recovery_code, recovery_salt)),
            recovery_verifier=make_password(recovery_code),
        )
        self.log_action(request, 'vault.master_password.setup', 'VaultKey', request.user.id, {})
        return Response({'recovery_code': recovery_code}, status=status.HTTP_201_CREATED)

    @extend_schema(tags=['app-vault'], summary='Change the vault master password')
    def put(self, request):
        vk = VaultKey.objects.filter(user=request.user).first()
        if not vk:
            return _NOT_FOUND
        serializer = MasterPasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        current = serializer.validated_data['current_master_password']
        new = serializer.validated_data['new_master_password']
        try:
            dek = crypto.unwrap_dek(vk.wrapped_dek, crypto.derive_kek(current, vk.salt))
        except crypto.VaultCryptoError:
            return Response(
                {'error': {'code': 'invalid_password', 'message': 'Contraseña maestra incorrecta.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        new_salt = crypto.generate_salt()
        vk.salt = new_salt
        vk.wrapped_dek = crypto.wrap_dek(dek, crypto.derive_kek(new, new_salt))
        vk.master_verifier = make_password(new)
        vk.save(update_fields=['salt', 'wrapped_dek', 'master_verifier', 'updated_at'])
        self.log_action(request, 'vault.master_password.change', 'VaultKey', request.user.id, {})
        return Response({'status': 'ok'})


class VaultUnlockView(AuditMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-vault'], summary='Unlock the vault')
    def post(self, request):
        vk = VaultKey.objects.filter(user=request.user).first()
        if not vk:
            return _NOT_FOUND
        fails = cache.get(_fails_key(request.user.id)) or 0
        if fails >= _MAX_UNLOCK_FAILS:
            self.log_action(request, 'vault.unlock_locked_out', 'VaultKey', request.user.id, {})
            return Response(
                {'error': {'code': 'locked_out', 'message': 'Demasiados intentos. Espera unos minutos.'}},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        serializer = UnlockSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        master = serializer.validated_data['master_password']
        try:
            dek = crypto.unwrap_dek(vk.wrapped_dek, crypto.derive_kek(master, vk.salt))
        except crypto.VaultCryptoError:
            cache.set(_fails_key(request.user.id), fails + 1, _FAIL_WINDOW)
            self.log_action(request, 'vault.unlock_failed', 'VaultKey', request.user.id, {})
            return Response(
                {'error': {'code': 'invalid_password', 'message': 'Contraseña maestra incorrecta.'}},
                status=status.HTTP_401_UNAUTHORIZED,
            )
        cache.delete(_fails_key(request.user.id))
        token = _store_dek(request.user.id, dek)
        self.log_action(request, 'vault.unlock', 'VaultKey', request.user.id, {})
        return Response({'unlock_token': token, 'expires_in': _UNLOCK_TTL})


class VaultLockView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-vault'], summary='Lock the vault')
    def post(self, request):
        token = request.headers.get('X-Vault-Token')
        if token:
            cache.delete(_dek_key(request.user.id, token))
        return Response({'status': 'ok'})


class VaultRecoverView(AuditMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-vault'], summary='Recover the vault with a recovery code')
    def post(self, request):
        vk = VaultKey.objects.filter(user=request.user).first()
        if not vk:
            return _NOT_FOUND
        serializer = RecoverSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        code = serializer.validated_data['recovery_code']
        new = serializer.validated_data['new_master_password']
        if not check_password(code, vk.recovery_verifier):
            self.log_action(request, 'vault.recover_failed', 'VaultKey', request.user.id, {})
            return Response(
                {'error': {'code': 'invalid_recovery', 'message': 'Código de recuperación inválido.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        dek = crypto.unwrap_dek(vk.wrapped_dek_recovery, crypto.derive_kek(code, vk.recovery_salt))
        # Reset master password.
        new_salt = crypto.generate_salt()
        vk.salt = new_salt
        vk.wrapped_dek = crypto.wrap_dek(dek, crypto.derive_kek(new, new_salt))
        vk.master_verifier = make_password(new)
        # Rotate the recovery code (single-use).
        new_recovery = secrets.token_urlsafe(24)
        new_recovery_salt = crypto.generate_salt()
        vk.recovery_salt = new_recovery_salt
        vk.wrapped_dek_recovery = crypto.wrap_dek(dek, crypto.derive_kek(new_recovery, new_recovery_salt))
        vk.recovery_verifier = make_password(new_recovery)
        vk.recovery_used_at = timezone.now()
        vk.save()
        self.log_action(request, 'vault.recover', 'VaultKey', request.user.id, {})
        return Response({'recovery_code': new_recovery})


class VaultItemListCreateView(AuditMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-vault'], summary='List vault items (titles, no secrets)')
    def get(self, request):
        qs = VaultItem.objects.filter(tenant=request.tenant, user=request.user)
        item_type = request.query_params.get('item_type')
        search = request.query_params.get('search')
        if item_type:
            qs = qs.filter(item_type=item_type)
        if search:
            qs = qs.filter(title__icontains=search)
        data = VaultItemListSerializer(qs, many=True).data
        return Response({'items': data, 'count': len(data)})

    @extend_schema(tags=['app-vault'], summary='Create a vault item (requires unlock)')
    def post(self, request):
        dek = _get_dek(request)
        if dek is None:
            return _LOCKED
        count = VaultItem.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'vault_items', count)
        serializer = VaultItemCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        item = VaultItem.objects.create(
            tenant=request.tenant,
            user=request.user,
            title=v['title'],
            item_type=v['item_type'],
            data_ciphertext=crypto.encrypt_blob(json.dumps(v['data']), dek),
            favorite=v.get('favorite', False),
        )
        self.log_action(request, 'vault.item.create', 'VaultItem', item.id, {'item_type': item.item_type})
        return Response(VaultItemListSerializer(item).data, status=status.HTTP_201_CREATED)


class VaultItemDetailView(AuditMixin, APIView):
    permission_classes = [IsAuthenticated]

    def _get_object(self, pk, request):
        return VaultItem.objects.filter(pk=pk, tenant=request.tenant, user=request.user).first()

    @extend_schema(tags=['app-vault'], summary='Reveal a vault item (requires unlock)')
    def get(self, request, pk):
        item = self._get_object(pk, request)
        if not item:
            return _NOT_FOUND
        dek = _get_dek(request)
        if dek is None:
            return _LOCKED
        data = json.loads(crypto.decrypt_blob(item.data_ciphertext, dek))
        self.log_action(request, 'vault.item.reveal', 'VaultItem', item.id, {})
        payload = VaultItemListSerializer(item).data
        payload['data'] = data
        return Response(payload)

    @extend_schema(tags=['app-vault'], summary='Update a vault item (requires unlock)')
    def patch(self, request, pk):
        item = self._get_object(pk, request)
        if not item:
            return _NOT_FOUND
        dek = _get_dek(request)
        if dek is None:
            return _LOCKED
        serializer = VaultItemCreateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        if 'title' in v:
            item.title = v['title']
        if 'item_type' in v:
            item.item_type = v['item_type']
        if 'favorite' in v:
            item.favorite = v['favorite']
        if 'data' in v:
            item.data_ciphertext = crypto.encrypt_blob(json.dumps(v['data']), dek)
        item.save()
        self.log_action(request, 'vault.item.update', 'VaultItem', item.id, {})
        return Response(VaultItemListSerializer(item).data)

    @extend_schema(tags=['app-vault'], summary='Delete a vault item')
    def delete(self, request, pk):
        item = self._get_object(pk, request)
        if not item:
            return _NOT_FOUND
        item.delete()
        self.log_action(request, 'vault.item.delete', 'VaultItem', pk, {})
        return Response(status=status.HTTP_204_NO_CONTENT)
