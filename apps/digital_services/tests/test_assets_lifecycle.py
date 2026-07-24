"""
Ciclo de vida de DigitalAsset (Fase 3): limpieza física del archivo al borrar la fila,
cascade y recolección de huérfanos que libera cuota.
"""
import io
import tempfile
import uuid
from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from apps.digital_services.models import DigitalAsset, PortfolioItem, PublicProfile
from apps.digital_services.tasks import collect_orphan_digital_assets
from apps.tenants.models import Tenant
from utils.storage import get_tenant_storage_bytes

User = get_user_model()


def _tenant(slug='life-corp', plan='professional'):
    return Tenant.objects.create(name=slug, slug=slug, subdomain=slug, plan=plan)


def _user(tenant, email):
    return User.objects.create_user(email=email, name='Test', password='x', tenant=tenant)


def _profile(user, username=None):
    return PublicProfile.objects.create(
        user=user, username=username or f'u-{uuid.uuid4().hex[:8]}', display_name='Test',
    )


def png_bytes():
    buf = io.BytesIO()
    Image.new('RGB', (1, 1)).save(buf, format='PNG')
    return buf.getvalue()


def _asset(profile, slot='avatar', size=1000, name=None):
    return DigitalAsset.objects.create(
        profile=profile, slot=slot,
        file=SimpleUploadedFile(name or f'{uuid.uuid4().hex}.png', png_bytes(),
                                content_type='image/png'),
        size=size, original_name='foto.png',
    )


def _age(asset, hours=25):
    """Envejece un asset por debajo de la ventana del GC (evade auto_now_add)."""
    DigitalAsset.objects.filter(pk=asset.pk).update(
        created_at=timezone.now() - timedelta(hours=hours)
    )


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DigitalAssetSignalTest(TestCase):
    def test_deleting_asset_removes_physical_file(self):
        profile = _profile(_user(_tenant(), 'a@x.com'))
        asset = _asset(profile)
        name = asset.file.name
        self.assertTrue(default_storage.exists(name))

        asset.delete()
        self.assertFalse(default_storage.exists(name))

    def test_cascade_delete_of_profile_frees_quota(self):
        tenant = _tenant()
        profile = _profile(_user(tenant, 'b@x.com'))
        _asset(profile, size=2048)
        _asset(profile, slot='portfolio_cover', size=1024)
        self.assertEqual(get_tenant_storage_bytes(tenant), 3072)

        profile_id = profile.pk
        profile.delete()
        self.assertEqual(DigitalAsset.objects.filter(profile_id=profile_id).count(), 0)
        self.assertEqual(get_tenant_storage_bytes(tenant), 0)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class DigitalAssetGCTest(TestCase):
    def setUp(self):
        self.tenant = _tenant()
        self.profile = _profile(_user(self.tenant, 'gc@x.com'))

    def _portfolio_item(self, **kwargs):
        return PortfolioItem.objects.create(
            profile=self.profile, title='P', slug=f's-{uuid.uuid4().hex[:8]}',
            description_short='d', project_date=date.today(), **kwargs,
        )

    def test_gc_deletes_old_unreferenced_asset(self):
        asset = _asset(self.profile, slot='portfolio_gallery', size=5000)
        _age(asset)
        self.assertEqual(get_tenant_storage_bytes(self.tenant), 5000)

        result = collect_orphan_digital_assets()

        self.assertEqual(result['deleted'], 1)
        self.assertFalse(DigitalAsset.objects.filter(pk=asset.pk).exists())
        self.assertEqual(get_tenant_storage_bytes(self.tenant), 0)

    def test_gc_keeps_referenced_asset(self):
        asset = _asset(self.profile, slot='portfolio_gallery', size=5000)
        _age(asset)
        # Referenciado desde la galería de un item → no debe borrarse.
        self._portfolio_item(gallery_images=[f'http://host/media/{asset.file.name}'])

        result = collect_orphan_digital_assets()

        self.assertEqual(result['deleted'], 0)
        self.assertTrue(DigitalAsset.objects.filter(pk=asset.pk).exists())

    def test_gc_keeps_referenced_by_cover(self):
        asset = _asset(self.profile, slot='portfolio_cover', size=3000)
        _age(asset)
        self._portfolio_item(cover_image_url=f'/media/{asset.file.name}')

        result = collect_orphan_digital_assets()

        self.assertEqual(result['deleted'], 0)
        self.assertTrue(DigitalAsset.objects.filter(pk=asset.pk).exists())

    def test_gc_keeps_recent_asset(self):
        asset = _asset(self.profile, slot='portfolio_gallery', size=5000)  # <24 h

        result = collect_orphan_digital_assets()

        self.assertEqual(result['deleted'], 0)
        self.assertTrue(DigitalAsset.objects.filter(pk=asset.pk).exists())

    def test_gc_ignores_uncollectable_slots(self):
        # landing_image y cv_photo no están cableados: no se recolectan aunque sean huérfanos viejos.
        landing = _asset(self.profile, slot='landing_image', size=4000)
        cv = _asset(self.profile, slot='cv_photo', size=4000)
        _age(landing)
        _age(cv)

        result = collect_orphan_digital_assets()

        self.assertEqual(result['deleted'], 0)
        self.assertTrue(DigitalAsset.objects.filter(pk=landing.pk).exists())
        self.assertTrue(DigitalAsset.objects.filter(pk=cv.pk).exists())
