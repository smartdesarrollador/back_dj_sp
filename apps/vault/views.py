"""
Vault views — personal encrypted secrets gated by a master password.

URL namespace: /api/v1/app/vault/

Auth model: `IsAuthenticated` + per-user scoping (no seeded `vault.*` RBAC perms,
same approach as apps/chat and apps/support). Sensitive operations additionally
require an **unlocked** vault: the client sends the opaque unlock token in the
`X-Vault-Token` header; the server looks up the cached DEK or returns 423 Locked.
"""
import base64
import json
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasFeature, check_plan_limit
from apps.vault import crypto
from apps.vault.models import VaultItem, VaultKey, VaultShare
from apps.vault.serializers import (
    MasterPasswordChangeSerializer,
    MasterPasswordSetupSerializer,
    RecoverSerializer,
    SharedVaultItemListSerializer,
    UnlockSerializer,
    VaultItemCreateUpdateSerializer,
    VaultItemListSerializer,
    VaultShareCreateSerializer,
    VaultShareSerializer,
)
from core.mixins import AuditMixin
from utils.encryption import decrypt_value, encrypt_value

User = get_user_model()

_RECIPIENT_NO_VAULT = Response(
    {
        'error': {
            'code': 'recipient_no_vault_key',
            'message': 'El destinatario debe configurar su Bóveda antes de poder recibir un ítem compartido.',
        }
    },
    status=400,
)

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


def _store_dek(user_id, dek: bytes, private_key: bytes | None = None) -> str:
    """Cache the DEK (and, if available, the sharing private key) — both
    encrypted with the global app key — under a fresh unlock token."""
    token = secrets.token_urlsafe(32)
    material = {'dek': crypto.dek_to_str(dek)}
    if private_key is not None:
        material['priv'] = crypto.dek_to_str(private_key)
    cache.set(_dek_key(user_id, token), encrypt_value(json.dumps(material)), _UNLOCK_TTL)
    return token


def _get_unlock_material(request) -> dict | None:
    """Resolve the cached {dek, priv} payload from the X-Vault-Token header."""
    token = request.headers.get('X-Vault-Token')
    if not token:
        return None
    stored = cache.get(_dek_key(request.user.id, token))
    if not stored:
        return None
    return json.loads(decrypt_value(stored))


def _get_dek(request) -> bytes | None:
    """Resolve the DEK from the X-Vault-Token header, or None if locked/invalid."""
    material = _get_unlock_material(request)
    if not material:
        return None
    return crypto.dek_from_str(material['dek'])


def _get_private_key(request) -> bytes | None:
    """Resolve the sharing private key from the X-Vault-Token header, or None
    if locked/invalid, or if it predates the sharing feature (never backfilled)."""
    material = _get_unlock_material(request)
    if not material or 'priv' not in material:
        return None
    return crypto.dek_from_str(material['priv'])


def _reseal_shares(item: VaultItem, dek: bytes) -> None:
    """Re-encrypt `item`'s current plaintext for every existing recipient.
    Called right after the owner's own copy is re-encrypted on update, so
    shared items never go stale relative to the source."""
    shares = item.shares.select_related('shared_with__vault_key')
    plaintext = crypto.decrypt_blob(item.data_ciphertext, dek)
    for share in shares:
        recipient_key = getattr(share.shared_with, 'vault_key', None)
        if not recipient_key or not recipient_key.public_key:
            continue  # Recipient lost their vault key somehow — skip, don't fail the update.
        recipient_public = base64.b64decode(recipient_key.public_key)
        share.sealed_payload = crypto.seal_for_recipient(plaintext, recipient_public)
        share.save(update_fields=['sealed_payload', 'updated_at'])


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
        private_key, public_key = crypto.generate_keypair()

        VaultKey.objects.create(
            user=request.user,
            tenant=request.tenant,
            salt=salt,
            wrapped_dek=crypto.wrap_dek(dek, crypto.derive_kek(master, salt)),
            master_verifier=make_password(master),
            recovery_salt=recovery_salt,
            wrapped_dek_recovery=crypto.wrap_dek(dek, crypto.derive_kek(recovery_code, recovery_salt)),
            recovery_verifier=make_password(recovery_code),
            public_key=base64.b64encode(public_key).decode(),
            wrapped_private_key=crypto.wrap_dek(private_key, crypto.derive_kek(master, salt)),
            wrapped_private_key_recovery=crypto.wrap_dek(
                private_key, crypto.derive_kek(recovery_code, recovery_salt)
            ),
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
        old_kek = crypto.derive_kek(current, vk.salt)
        try:
            dek = crypto.unwrap_dek(vk.wrapped_dek, old_kek)
        except crypto.VaultCryptoError:
            return Response(
                {'error': {'code': 'invalid_password', 'message': 'Contraseña maestra incorrecta.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        private_key = (
            crypto.unwrap_dek(vk.wrapped_private_key, old_kek) if vk.wrapped_private_key else None
        )
        new_salt = crypto.generate_salt()
        new_kek = crypto.derive_kek(new, new_salt)
        vk.salt = new_salt
        vk.wrapped_dek = crypto.wrap_dek(dek, new_kek)
        vk.master_verifier = make_password(new)
        update_fields = ['salt', 'wrapped_dek', 'master_verifier', 'updated_at']
        if private_key is not None:
            vk.wrapped_private_key = crypto.wrap_dek(private_key, new_kek)
            update_fields.append('wrapped_private_key')
        vk.save(update_fields=update_fields)
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

        kek = crypto.derive_kek(master, vk.salt)
        if vk.wrapped_private_key:
            private_key = crypto.unwrap_dek(vk.wrapped_private_key, kek)
        else:
            # Lazy backfill: this VaultKey predates the sharing feature.
            private_key, public_key = crypto.generate_keypair()
            vk.public_key = base64.b64encode(public_key).decode()
            vk.wrapped_private_key = crypto.wrap_dek(private_key, kek)
            vk.save(update_fields=['public_key', 'wrapped_private_key', 'updated_at'])

        token = _store_dek(request.user.id, dek, private_key)
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
        recovery_kek = crypto.derive_kek(code, vk.recovery_salt)
        dek = crypto.unwrap_dek(vk.wrapped_dek_recovery, recovery_kek)
        private_key = (
            crypto.unwrap_dek(vk.wrapped_private_key_recovery, recovery_kek)
            if vk.wrapped_private_key_recovery
            else None
        )
        # Reset master password.
        new_salt = crypto.generate_salt()
        new_kek = crypto.derive_kek(new, new_salt)
        vk.salt = new_salt
        vk.wrapped_dek = crypto.wrap_dek(dek, new_kek)
        vk.master_verifier = make_password(new)
        # Rotate the recovery code (single-use).
        new_recovery = secrets.token_urlsafe(24)
        new_recovery_salt = crypto.generate_salt()
        new_recovery_kek = crypto.derive_kek(new_recovery, new_recovery_salt)
        vk.recovery_salt = new_recovery_salt
        vk.wrapped_dek_recovery = crypto.wrap_dek(dek, new_recovery_kek)
        vk.recovery_verifier = make_password(new_recovery)
        vk.recovery_used_at = timezone.now()
        if private_key is not None:
            vk.wrapped_private_key = crypto.wrap_dek(private_key, new_kek)
            vk.wrapped_private_key_recovery = crypto.wrap_dek(private_key, new_recovery_kek)
        vk.save()
        self.log_action(request, 'vault.recover', 'VaultKey', request.user.id, {})
        return Response({'recovery_code': new_recovery})


class VaultItemListCreateView(AuditMixin, APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['app-vault'],
        summary='List vault items (titles, no secrets)',
        parameters=[
            OpenApiParameter('item_type', OpenApiTypes.STR, description='Filter by item type'),
            OpenApiParameter('search', OpenApiTypes.STR, description='Search in title'),
            OpenApiParameter('page', OpenApiTypes.INT, description='Page number. Omit to get all results unpaginated.'),
            OpenApiParameter('per_page', OpenApiTypes.INT, description='Results per page (default: 20, max: 100)'),
        ],
    )
    def get(self, request):
        qs = VaultItem.objects.filter(tenant=request.tenant, user=request.user)
        item_type = request.query_params.get('item_type')
        search = request.query_params.get('search')
        if item_type:
            qs = qs.filter(item_type=item_type)
        if search:
            qs = qs.filter(title__icontains=search)

        raw_page = request.query_params.get('page')

        if raw_page is None:
            data = VaultItemListSerializer(qs, many=True).data
            return Response({'items': data, 'count': len(data)})

        total = qs.count()
        try:
            page = max(1, int(raw_page))
            per_page = min(100, max(1, int(request.query_params.get('per_page', 20))))
        except (ValueError, TypeError):
            page = 1
            per_page = 20

        offset = (page - 1) * per_page
        data = VaultItemListSerializer(qs[offset:offset + per_page], many=True).data
        return Response({
            'items': data,
            'pagination': {'page': page, 'per_page': per_page, 'total': total},
        })

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
            _reseal_shares(item, dek)
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


class VaultItemShareView(AuditMixin, APIView):
    """
    GET  /app/vault/items/<pk>/share/  → list who this item is shared with (owner only)
    POST /app/vault/items/<pk>/share/  → share this item with another user, by email
    """
    permission_classes = [IsAuthenticated, HasFeature('sharing')]

    def _get_own_item(self, pk, request):
        return VaultItem.objects.filter(pk=pk, tenant=request.tenant, user=request.user).first()

    @extend_schema(tags=['app-vault'], summary='List who a vault item is shared with')
    def get(self, request, pk):
        item = self._get_own_item(pk, request)
        if not item:
            return _NOT_FOUND
        shares = item.shares.select_related('shared_with')
        return Response({'shares': VaultShareSerializer(shares, many=True).data})

    @extend_schema(tags=['app-vault'], summary='Share a vault item with another user')
    def post(self, request, pk):
        item = self._get_own_item(pk, request)
        if not item:
            return _NOT_FOUND
        dek = _get_dek(request)
        if dek is None:
            return _LOCKED

        serializer = VaultShareCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['shared_with_email']

        recipient = User.objects.filter(email__iexact=email, is_active=True).first()
        if not recipient:
            return Response(
                {'error': {'code': 'user_not_found', 'message': 'No existe ningún usuario con ese email.'}},
                status=status.HTTP_404_NOT_FOUND,
            )
        if recipient.id == request.user.id:
            return Response(
                {'error': {'code': 'self_share', 'message': 'No puedes compartir un ítem contigo mismo.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        recipient_key = VaultKey.objects.filter(user=recipient).first()
        if not recipient_key or not recipient_key.public_key:
            return _RECIPIENT_NO_VAULT

        plaintext = crypto.decrypt_blob(item.data_ciphertext, dek)
        sealed = crypto.seal_for_recipient(plaintext, base64.b64decode(recipient_key.public_key))

        share, created = VaultShare.objects.update_or_create(
            item=item,
            shared_with=recipient,
            defaults={'tenant': request.tenant, 'shared_by': request.user, 'sealed_payload': sealed},
        )
        self.log_action(
            request, 'vault.item.share', 'VaultItem', item.id, {'shared_with': str(recipient.id)}
        )
        return Response(
            VaultShareSerializer(share).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class VaultItemShareDetailView(AuditMixin, APIView):
    """DELETE /app/vault/items/<pk>/share/<share_id>/ — revoke a share (owner only)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-vault'], summary='Revoke a vault item share')
    def delete(self, request, pk, share_id):
        share = VaultShare.objects.filter(
            pk=share_id, item_id=pk, item__user=request.user, tenant=request.tenant
        ).first()
        if not share:
            return _NOT_FOUND
        self.log_action(
            request, 'vault.item.unshare', 'VaultItem', pk, {'shared_with': str(share.shared_with_id)}
        )
        share.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class VaultSharedWithMeListView(APIView):
    """GET /app/vault/shared-with-me/ — items shared with me (metadata only)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-vault'], summary='List vault items shared with me')
    def get(self, request):
        shares = VaultShare.objects.filter(
            shared_with=request.user, tenant=request.tenant
        ).select_related('item', 'shared_by')
        return Response({'items': SharedVaultItemListSerializer(shares, many=True).data})


class VaultSharedItemRevealView(AuditMixin, APIView):
    """GET /app/vault/shared-with-me/<share_id>/ — reveal a shared item (requires MY OWN unlock)."""
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-vault'], summary='Reveal a vault item shared with me')
    def get(self, request, share_id):
        share = VaultShare.objects.filter(
            pk=share_id, shared_with=request.user, tenant=request.tenant
        ).select_related('item').first()
        if not share:
            return _NOT_FOUND
        private_key = _get_private_key(request)
        if private_key is None:
            return _LOCKED
        try:
            data = json.loads(crypto.unseal_with_private_key(share.sealed_payload, private_key))
        except crypto.VaultCryptoError:
            return Response(
                {'error': {'code': 'unseal_failed', 'message': 'No se pudo abrir el ítem compartido.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        self.log_action(request, 'vault.shared_item.reveal', 'VaultShare', share.id, {})
        return Response({
            'share_id': str(share.id),
            'item_id': str(share.item_id),
            'title': share.item.title,
            'item_type': share.item.item_type,
            'shared_by_name': share.shared_by.name,
            'data': data,
        })
