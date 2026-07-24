"""
Tests del validador central de subidas (utils/uploads.py): extensión permitida,
tipo real por contenido, topes por plan / topes duros y cuota de almacenamiento.
"""
import io
import uuid

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework.exceptions import ValidationError

from core.exceptions import PlanLimitExceeded
from utils.uploads import MB, UPLOAD_CATEGORIES, validate_upload

_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_tenant(plan: str = 'free'):
    from apps.tenants.models import Tenant
    slug = f'tenant-{uuid.uuid4().hex[:8]}'
    return Tenant.objects.create(name=slug, slug=slug, subdomain=slug, plan=plan)


def png_bytes(size: tuple[int, int] = (1, 1)) -> bytes:
    """PNG real y decodificable — no basta con la firma para pasar Pillow.verify()."""
    buffer = io.BytesIO()
    Image.new('RGB', size).save(buffer, format='PNG')
    return buffer.getvalue()


def upload(name: str, content: bytes, size: int | None = None) -> SimpleUploadedFile:
    """
    UploadedFile de prueba. `size` permite simular un archivo grande sin materializar
    los bytes: el validador compara contra `.size`, no contra el contenido real.
    """
    file = SimpleUploadedFile(name, content)
    if size is not None:
        file.size = size
    return file


def png_upload(name: str = 'foto.png', size: int | None = None) -> SimpleUploadedFile:
    return upload(name, png_bytes(), size=size)


# ─── Extensión ────────────────────────────────────────────────────────────────

class UploadExtensionTest(TestCase):
    def test_extension_not_in_whitelist_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_upload(upload('virus.exe', b'MZ...'), category='platform_image')

    def test_file_without_extension_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_upload(upload('sinextension', png_bytes()), category='platform_image')

    def test_double_extension_resolves_to_the_last_one(self):
        # 'factura.png.exe' es un .exe: no debe colarse por el '.png' intermedio.
        with self.assertRaises(ValidationError):
            validate_upload(upload('factura.png.exe', png_bytes()), category='platform_image')

    def test_extension_is_case_insensitive(self):
        validate_upload(png_upload('FOTO.PNG'), category='platform_image')

    def test_error_message_lists_accepted_formats(self):
        with self.assertRaises(ValidationError) as ctx:
            validate_upload(upload('doc.pdf', b'%PDF-1.4'), category='platform_image')
        self.assertIn('JPEG, JPG, PNG, WEBP', str(ctx.exception))

    def test_svg_is_rejected_in_every_category(self):
        # XML con capacidad de ejecutar JS, servido desde el /media/ público.
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        tenant = make_tenant('professional')
        for category in UPLOAD_CATEGORIES:
            with self.subTest(category=category):
                with self.assertRaises(ValidationError):
                    validate_upload(upload('logo.svg', svg), category=category, tenant=tenant)


# ─── Tipo real por contenido ──────────────────────────────────────────────────

class UploadContentTypeTest(TestCase):
    def test_real_png_passes(self):
        validate_upload(png_upload(), category='platform_image')

    def test_executable_renamed_to_png_is_rejected(self):
        # El caso que motiva no confiar en el nombre ni en el content_type del cliente.
        fake = upload('inocente.png', b'MZ\x90\x00\x03\x00\x00\x00\x04\x00\x00\x00')
        with self.assertRaises(ValidationError):
            validate_upload(fake, category='platform_image')

    def test_text_renamed_to_png_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_upload(upload('nota.png', b'hola mundo'), category='platform_image')

    def test_real_pdf_passes(self):
        tenant = make_tenant('professional')
        validate_upload(upload('doc.pdf', b'%PDF-1.4\n%bytes'), category='chat_attachment',
                        tenant=tenant)

    def test_fake_pdf_is_rejected(self):
        tenant = make_tenant('professional')
        with self.assertRaises(ValidationError):
            validate_upload(upload('doc.pdf', b'no soy un pdf'), category='chat_attachment',
                            tenant=tenant)

    def test_real_zip_passes(self):
        tenant = make_tenant('professional')
        validate_upload(upload('archivo.zip', b'PK\x03\x04\x14\x00\x00\x00'),
                        category='chat_attachment', tenant=tenant)

    def test_docx_accepts_zip_signature(self):
        tenant = make_tenant('professional')
        validate_upload(upload('informe.docx', b'PK\x03\x04\x14\x00\x06\x00'),
                        category='chat_attachment', tenant=tenant)

    def test_utf8_text_passes(self):
        tenant = make_tenant('professional')
        validate_upload(upload('notas.txt', 'hola, ñandú'.encode()),
                        category='chat_attachment', tenant=tenant)

    def test_binary_content_in_txt_is_rejected(self):
        tenant = make_tenant('professional')
        with self.assertRaises(ValidationError):
            validate_upload(upload('notas.txt', b'\xff\xfe\x00\x01\x02\x03'),
                            category='chat_attachment', tenant=tenant)

    def test_file_pointer_is_rewound_after_validation(self):
        # Si el validador deja el puntero avanzado, el guardado posterior truncaría el archivo.
        file = png_upload()
        validate_upload(file, category='platform_image')
        self.assertEqual(file.tell(), 0)


# ─── Topes de tamaño ──────────────────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM_CACHE)
class UploadSizeLimitTest(TestCase):
    def setUp(self):
        cache.clear()  # get_effective_plan_limits cachea por plan — evitar fugas entre tests

    def test_limit_per_plan_for_chat_attachments(self):
        expected = {'free': 5, 'starter': 10, 'professional': 25, 'enterprise': 100}
        for plan, limit_mb in expected.items():
            with self.subTest(plan=plan):
                cache.clear()
                tenant = make_tenant(plan)
                validate_upload(
                    upload('doc.pdf', b'%PDF-1.4', size=limit_mb * MB),
                    category='chat_attachment', tenant=tenant,
                )
                with self.assertRaises((PlanLimitExceeded, ValidationError)):
                    validate_upload(
                        upload('doc.pdf', b'%PDF-1.4', size=limit_mb * MB + 1),
                        category='chat_attachment', tenant=tenant,
                    )

    def test_over_plan_limit_raises_402_with_upgrade_message(self):
        tenant = make_tenant('free')
        with self.assertRaises(PlanLimitExceeded) as ctx:
            validate_upload(upload('doc.pdf', b'%PDF-1.4', size=6 * MB),
                            category='chat_attachment', tenant=tenant)
        self.assertIn('5 MB', str(ctx.exception))
        self.assertIn('Cambia a un plan superior', str(ctx.exception))

    def test_enterprise_at_hard_cap_raises_400_not_402(self):
        # Enterprise coincide con el tope duro (100 MB): invitarle a un upgrade no tiene sentido.
        tenant = make_tenant('enterprise')
        with self.assertRaises(ValidationError) as ctx:
            validate_upload(upload('doc.pdf', b'%PDF-1.4', size=101 * MB),
                            category='chat_attachment', tenant=tenant)
        self.assertNotIsInstance(ctx.exception, PlanLimitExceeded)

    def test_unlimited_plan_override_is_capped_by_hard_max(self):
        from apps.subscriptions.models import Plan

        # Un admin pone "sin límite" (null); el tope duro de la categoría sigue rigiendo.
        Plan.objects.create(id='free', display_name='Free', limits={'max_file_upload_mb': None})
        tenant = make_tenant('free')
        validate_upload(upload('doc.pdf', b'%PDF-1.4', size=100 * MB),
                        category='chat_attachment', tenant=tenant)
        with self.assertRaises(ValidationError):
            validate_upload(upload('doc.pdf', b'%PDF-1.4', size=101 * MB),
                            category='chat_attachment', tenant=tenant)

    def test_admin_override_takes_priority_over_code_default(self):
        from apps.subscriptions.models import Plan

        Plan.objects.create(id='free', display_name='Free', limits={'max_file_upload_mb': 1})
        tenant = make_tenant('free')
        with self.assertRaises(PlanLimitExceeded):
            validate_upload(upload('doc.pdf', b'%PDF-1.4', size=2 * MB),
                            category='chat_attachment', tenant=tenant)

    def test_platform_image_uses_fixed_limit_regardless_of_plan(self):
        validate_upload(png_upload(size=2 * MB), category='platform_image')
        with self.assertRaises(ValidationError):
            validate_upload(png_upload(size=3 * MB), category='platform_image')


# ─── payment_proof: nunca bloquea una conversión a pago ───────────────────────

@override_settings(CACHES=_LOCMEM_CACHE)
class PaymentProofLimitTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_free_tenant_can_upload_large_phone_screenshot(self):
        # Con el tope de imagen de Free (2 MB) un screenshot de móvil quedaría bloqueado
        # justo al intentar pagar el upgrade. payment_proof no se gatea por plan.
        tenant = make_tenant('free')
        validate_upload(png_upload('yape.png', size=8 * MB),
                        category='payment_proof', tenant=tenant)

    def test_still_capped_at_fixed_limit(self):
        tenant = make_tenant('free')
        with self.assertRaises(ValidationError):
            validate_upload(png_upload('yape.png', size=11 * MB),
                            category='payment_proof', tenant=tenant)


# ─── Cuota de almacenamiento ──────────────────────────────────────────────────

@override_settings(CACHES=_LOCMEM_CACHE)
class UploadStorageQuotaTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_chat_attachment_respects_tenant_storage_quota(self):
        from apps.subscriptions.models import Plan

        # storage_gb = 0 → cualquier byte adicional supera la cuota.
        Plan.objects.create(id='free', display_name='Free', limits={'storage_gb': 0})
        tenant = make_tenant('free')
        with self.assertRaises(PlanLimitExceeded):
            validate_upload(upload('doc.pdf', b'%PDF-1.4', size=1024),
                            category='chat_attachment', tenant=tenant)

    def test_platform_image_ignores_storage_quota(self):
        from apps.subscriptions.models import Plan

        # Contenido global de plataforma: no cuenta para la cuota del tenant.
        Plan.objects.create(id='free', display_name='Free', limits={'storage_gb': 0})
        validate_upload(png_upload(size=1024), category='platform_image')

    def test_payment_proof_ignores_storage_quota(self):
        from apps.subscriptions.models import Plan

        # Con la cuota llena el cliente igual debe poder subir el comprobante: si no,
        # no podría pagar el plan que le daría más espacio.
        Plan.objects.create(id='free', display_name='Free', limits={'storage_gb': 0})
        tenant = make_tenant('free')
        validate_upload(png_upload('yape.png', size=1024),
                        category='payment_proof', tenant=tenant)


# ─── digital_asset: imágenes de Vista que cuentan a la cuota ──────────────────

@override_settings(CACHES=_LOCMEM_CACHE)
class DigitalAssetUploadTest(TestCase):
    def setUp(self):
        cache.clear()

    def test_real_png_within_plan_limit_passes(self):
        tenant = make_tenant('free')  # tope de imagen Free = 2 MB
        validate_upload(png_upload('avatar.png', size=1 * MB),
                        category='digital_asset', tenant=tenant)

    def test_svg_is_rejected(self):
        tenant = make_tenant('free')
        with self.assertRaises(ValidationError):
            validate_upload(upload('logo.svg', b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'),
                            category='digital_asset', tenant=tenant)

    def test_executable_renamed_to_png_is_rejected(self):
        tenant = make_tenant('free')
        with self.assertRaises(ValidationError):
            validate_upload(upload('avatar.png', b'MZ\x90\x00'),
                            category='digital_asset', tenant=tenant)

    def test_over_plan_image_limit_raises_402(self):
        tenant = make_tenant('free')  # 2 MB
        with self.assertRaises(PlanLimitExceeded) as ctx:
            validate_upload(png_upload('avatar.png', size=3 * MB),
                            category='digital_asset', tenant=tenant)
        self.assertIn('2 MB', str(ctx.exception))

    def test_counts_toward_storage_quota(self):
        from apps.subscriptions.models import Plan

        # storage_gb = 0 → cualquier byte adicional supera la cuota → 402.
        Plan.objects.create(id='free', display_name='Free', limits={'storage_gb': 0})
        tenant = make_tenant('free')
        with self.assertRaises(PlanLimitExceeded):
            validate_upload(png_upload('avatar.png', size=1024),
                            category='digital_asset', tenant=tenant)

    def test_tenant_is_required(self):
        # Categoría gateada por plan sin tenant → error de programación (ValueError), no 400.
        with self.assertRaises(ValueError):
            validate_upload(png_upload('avatar.png'), category='digital_asset')


# ─── Contrato de la API ───────────────────────────────────────────────────────

class UploadApiContractTest(TestCase):
    def test_unknown_category_raises_value_error(self):
        with self.assertRaises(ValueError):
            validate_upload(png_upload(), category='no_existe')

    def test_plan_gated_category_without_tenant_raises_value_error(self):
        # Olvidar el tenant no debe saltarse el tope del plan en silencio.
        with self.assertRaises(ValueError):
            validate_upload(png_upload(), category='tenant_branding')
