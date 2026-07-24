"""
Tests de integración de la API de imágenes gestionadas de Vista (DigitalAsset):
subida que cuenta hacia la cuota, bloqueo 402 por plan/cuota, 400 por tipo/slot,
borrado que libera cuota y aislamiento por dueño.
"""
import io
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from apps.digital_services.models import DigitalAsset, PublicProfile
from apps.subscriptions.models import Plan
from apps.tenants.models import Tenant
from utils.storage import get_tenant_storage_bytes

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

ASSETS_URL = '/api/v1/app/digital/assets/'


def _tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _user(tenant, email):
    return User.objects.create_user(email=email, name='Test User', password='x', tenant=tenant)


def _profile(user, username=None):
    return PublicProfile.objects.create(
        user=user,
        username=username or f'user-{uuid.uuid4().hex[:8]}',
        display_name='Test User',
    )


def png_bytes(size: tuple[int, int] = (1, 1)) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', size).save(buffer, format='PNG')
    return buffer.getvalue()


def png_file(name='avatar.png', size=None):
    """PNG real (pasa Pillow.verify). `size` fuerza el peso reportado sin materializar bytes."""
    f = SimpleUploadedFile(name, png_bytes(), content_type='image/png')
    if size is not None:
        f.size = size
    return f


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class DigitalAssetUploadApiTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _tenant('assets-corp')
        self.user = _user(self.tenant, 'owner@assets.com')
        self.profile = _profile(self.user)
        self.client.force_authenticate(user=self.user)
        self.hdr = {'HTTP_X_TENANT_SLUG': 'assets-corp'}

    def _post(self, data, **extra):
        return self.client.post(ASSETS_URL, data, format='multipart', **self.hdr, **extra)

    # ── Subida OK + contabilización ──────────────────────────────────────────

    def test_upload_creates_asset_and_counts_toward_storage(self):
        res = self._post({'file': png_file(), 'slot': 'avatar'})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        body = res.json()
        self.assertIn('url', body)
        self.assertEqual(body['slot'], 'avatar')

        asset = DigitalAsset.objects.get(id=body['id'])
        self.assertEqual(asset.profile, self.profile)
        self.assertGreater(asset.size, 0)
        self.assertEqual(get_tenant_storage_bytes(self.tenant), asset.size)

    def test_upload_works_without_tenant_header(self):
        # Vista autentica solo con Bearer (sin X-Tenant-Slug): el tenant sale de request.user.
        res = self.client.post(
            ASSETS_URL, {'file': png_file(), 'slot': 'avatar'}, format='multipart',
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(get_tenant_storage_bytes(self.tenant), res.json()['size'])

    def test_list_returns_own_assets(self):
        self._post({'file': png_file(), 'slot': 'avatar'})
        res = self.client.get(ASSETS_URL, **self.hdr)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.json()['assets']), 1)

    # ── 400: entrada inválida ────────────────────────────────────────────────

    def test_missing_file_is_400(self):
        res = self._post({'slot': 'avatar'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_invalid_slot_is_400(self):
        res = self._post({'file': png_file(), 'slot': 'no_existe'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_executable_renamed_to_png_is_rejected(self):
        fake = SimpleUploadedFile('avatar.png', b'MZ\x90\x00', content_type='image/png')
        res = self._post({'file': fake, 'slot': 'avatar'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_without_profile_is_400(self):
        other = _user(self.tenant, 'noprofile@assets.com')
        self.client.force_authenticate(user=other)
        res = self._post({'file': png_file(), 'slot': 'avatar'})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.json()['error']['code'], 'profile_required')

    # ── 402: plan / cuota ────────────────────────────────────────────────────

    def test_over_plan_image_limit_is_402(self):
        # El peso reportado por el cliente multipart es el real del PNG (no se puede falsear
        # vía .size en un POST HTTP), así que se baja el tope de imagen del plan a 0 MB para
        # que cualquier imagen lo supere y dispare el 402 por límite de plan.
        cache.clear()
        Plan.objects.create(id='professional', display_name='Pro', limits={'max_image_upload_mb': 0})
        res = self._post({'file': png_file(), 'slot': 'avatar'})
        self.assertEqual(res.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    def test_over_storage_quota_is_402(self):
        cache.clear()
        Plan.objects.create(id='professional', display_name='Pro', limits={'storage_gb': 0})
        res = self._post({'file': png_file(), 'slot': 'avatar'})
        self.assertEqual(res.status_code, status.HTTP_402_PAYMENT_REQUIRED)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class DigitalAssetDeleteApiTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _tenant('del-corp')
        self.user = _user(self.tenant, 'owner@del.com')
        self.profile = _profile(self.user)
        self.asset = DigitalAsset.objects.create(
            profile=self.profile, slot='avatar',
            file=SimpleUploadedFile('a.png', png_bytes(), content_type='image/png'),
            size=4096, original_name='a.png',
        )
        self.client.force_authenticate(user=self.user)
        self.hdr = {'HTTP_X_TENANT_SLUG': 'del-corp'}

    def test_owner_delete_frees_quota(self):
        self.assertEqual(get_tenant_storage_bytes(self.tenant), 4096)
        res = self.client.delete(f'{ASSETS_URL}{self.asset.id}/', **self.hdr)
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(DigitalAsset.objects.filter(id=self.asset.id).exists())
        self.assertEqual(get_tenant_storage_bytes(self.tenant), 0)

    def test_cannot_delete_asset_of_another_tenant(self):
        cache.clear()
        other_tenant = _tenant('other-corp')
        other_user = _user(other_tenant, 'intruder@other.com')
        _profile(other_user)
        self.client.force_authenticate(user=other_user)
        res = self.client.delete(
            f'{ASSETS_URL}{self.asset.id}/', HTTP_X_TENANT_SLUG='other-corp',
        )
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(DigitalAsset.objects.filter(id=self.asset.id).exists())
