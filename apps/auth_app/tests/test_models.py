"""
Tests para los modelos fundacionales: Tenant y User.

Cubre: creación, campos por defecto, constraints de unicidad,
       hashing de contraseña, relación FK y comportamiento CASCADE.
"""
from django.db import IntegrityError
from django.test import TestCase

from apps.auth_app.models import User
from apps.tenants.models import Tenant


class TenantModelTest(TestCase):
    """Tests para el modelo Tenant."""

    def test_create_tenant(self):
        """Crea un tenant con todos los campos obligatorios y verifica defaults."""
        tenant = Tenant.objects.create(
            name='Test Corp',
            slug='test-corp',
            subdomain='testcorp',
        )
        self.assertEqual(tenant.name, 'Test Corp')
        self.assertEqual(tenant.slug, 'test-corp')
        self.assertEqual(tenant.subdomain, 'testcorp')
        self.assertEqual(tenant.plan, 'free')
        self.assertTrue(tenant.is_active)
        self.assertEqual(tenant.branding, {})
        self.assertEqual(tenant.settings, {})
        self.assertIsNotNone(tenant.id)
        self.assertIsNotNone(tenant.created_at)

    def test_tenant_str(self):
        """__str__ retorna 'Name (slug)'."""
        tenant = Tenant.objects.create(
            name='Acme Inc',
            slug='acme',
            subdomain='acme',
        )
        self.assertEqual(str(tenant), 'Acme Inc (acme)')

    def test_slug_unique_constraint(self):
        """Slug duplicado lanza IntegrityError."""
        Tenant.objects.create(name='Tenant A', slug='unique-slug', subdomain='tenanta')
        with self.assertRaises(IntegrityError):
            Tenant.objects.create(name='Tenant B', slug='unique-slug', subdomain='tenantb')

    def test_subdomain_unique_constraint(self):
        """Subdomain duplicado lanza IntegrityError."""
        Tenant.objects.create(name='Tenant A', slug='slug-a', subdomain='sharedsub')
        with self.assertRaises(IntegrityError):
            Tenant.objects.create(name='Tenant B', slug='slug-b', subdomain='sharedsub')


class UserModelTest(TestCase):
    """Tests para el modelo User y su manager."""

    def setUp(self):
        self.tenant = Tenant.objects.create(
            name='Test Corp',
            slug='test-corp',
            subdomain='testcorp',
        )

    def test_create_user(self):
        """Crea usuario con campos básicos y verifica valores por defecto."""
        user = User.objects.create_user(
            email='user@example.com',
            name='Test User',
            password='SecurePass1',
            tenant=self.tenant,
        )
        self.assertEqual(user.email, 'user@example.com')
        self.assertEqual(user.name, 'Test User')
        self.assertEqual(user.tenant, self.tenant)
        self.assertTrue(user.is_active)
        self.assertFalse(user.is_staff)
        self.assertFalse(user.email_verified)
        self.assertFalse(user.mfa_enabled)
        self.assertIsNotNone(user.id)
        self.assertIsNotNone(user.created_at)

    def test_create_superuser(self):
        """Superusuario tiene is_staff=True, is_superuser=True, email_verified=True."""
        user = User.objects.create_superuser(
            email='admin@example.com',
            name='Admin User',
            password='AdminPass1',
            tenant=self.tenant,
        )
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.email_verified)

    def test_create_superuser_auto_creates_system_tenant(self):
        """Sin pasar tenant, create_superuser crea el tenant 'system' automáticamente."""
        user = User.objects.create_superuser(
            email='sysadmin@example.com',
            name='Sys Admin',
            password='AdminPass1',
        )
        self.assertEqual(user.tenant.slug, 'system')
        self.assertTrue(Tenant.objects.filter(slug='system').exists())

    def test_email_unique_constraint(self):
        """Email duplicado lanza IntegrityError."""
        User.objects.create_user(
            email='duplicate@example.com',
            name='First User',
            password='Pass1234',
            tenant=self.tenant,
        )
        with self.assertRaises(IntegrityError):
            User.objects.create_user(
                email='duplicate@example.com',
                name='Second User',
                password='Pass5678',
                tenant=self.tenant,
            )

    def test_password_is_hashed(self):
        """La contraseña se almacena hasheada (Argon2); check_password() funciona."""
        user = User.objects.create_user(
            email='hashed@example.com',
            name='Hash User',
            password='MySecret1',
            tenant=self.tenant,
        )
        self.assertNotEqual(user.password, 'MySecret1')
        self.assertTrue(user.check_password('MySecret1'))

    def test_user_belongs_to_tenant(self):
        """El usuario referencia correctamente a su tenant via FK."""
        user = User.objects.create_user(
            email='member@example.com',
            name='Member',
            password='Pass1234',
            tenant=self.tenant,
        )
        self.assertEqual(user.tenant.slug, 'test-corp')
        self.assertIn(user, self.tenant.users.all())

    def test_tenant_cascade_deletes_users(self):
        """Al eliminar el tenant, los usuarios se eliminan en cascada."""
        user = User.objects.create_user(
            email='cascade@example.com',
            name='Cascade User',
            password='Pass1234',
            tenant=self.tenant,
        )
        user_id = user.id
        self.tenant.delete()
        self.assertFalse(User.objects.filter(id=user_id).exists())

    def test_create_user_requires_tenant(self):
        """create_user lanza ValueError si no se pasa tenant."""
        with self.assertRaises(ValueError):
            User.objects.create_user(
                email='notenant@example.com',
                name='No Tenant',
                password='Pass1234',
            )

    def test_create_user_requires_email(self):
        """create_user lanza ValueError si el email está vacío."""
        with self.assertRaises(ValueError):
            User.objects.create_user(
                email='',
                name='No Email',
                password='Pass1234',
                tenant=self.tenant,
            )
