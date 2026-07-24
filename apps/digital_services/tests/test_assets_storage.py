"""
Contabilización de las imágenes de Vista (DigitalAsset) hacia la cuota storage_gb
del tenant — get_tenant_storage_bytes() (utils/storage.py).
"""
import uuid

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from apps.digital_services.models import DigitalAsset, PublicProfile
from apps.tenants.models import Tenant
from utils.storage import get_tenant_storage_bytes

User = get_user_model()


def _tenant(plan='free'):
    slug = f'ten-{uuid.uuid4().hex[:8]}'
    return Tenant.objects.create(name=slug, slug=slug, subdomain=slug, plan=plan)


def _profile(tenant, username=None):
    user = User.objects.create_user(
        email=f'{uuid.uuid4().hex[:8]}@test.com', name='Test', password='x', tenant=tenant,
    )
    return PublicProfile.objects.create(
        user=user,
        username=username or f'user-{uuid.uuid4().hex[:8]}',
        display_name='Test User',
    )


def _asset(profile, size, slot='avatar'):
    return DigitalAsset.objects.create(
        profile=profile,
        slot=slot,
        # El contenido no importa para la suma: get_tenant_storage_bytes lee el campo `size`.
        file=SimpleUploadedFile(f'{uuid.uuid4().hex}.png', b'\x89PNG\r\n'),
        size=size,
        original_name='foto.png',
    )


class DigitalAssetStorageAccountingTest(TestCase):
    def test_assets_are_included_in_tenant_storage(self):
        tenant = _tenant()
        profile = _profile(tenant)
        self.assertEqual(get_tenant_storage_bytes(tenant), 0)

        _asset(profile, size=1000)
        _asset(profile, size=2500, slot='portfolio_cover')

        self.assertEqual(get_tenant_storage_bytes(tenant), 3500)

    def test_isolation_between_tenants(self):
        tenant_a = _tenant()
        tenant_b = _tenant()
        _asset(_profile(tenant_a), size=5000)

        # Los assets de A no suman al total de B.
        self.assertEqual(get_tenant_storage_bytes(tenant_b), 0)
        self.assertEqual(get_tenant_storage_bytes(tenant_a), 5000)

    def test_deleting_asset_frees_quota(self):
        tenant = _tenant()
        profile = _profile(tenant)
        asset = _asset(profile, size=4096)
        self.assertEqual(get_tenant_storage_bytes(tenant), 4096)

        asset.delete()
        self.assertEqual(get_tenant_storage_bytes(tenant), 0)
