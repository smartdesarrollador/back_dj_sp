"""
Tests for PASO 9 — Projects CRUD + AES-256 Encryption.
Covers: list, create, detail, update, delete, sections, reorder, items,
        fields, password encryption, reveal + audit log, cross-tenant isolation,
        members add/remove.
"""
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.projects.models import (
    Project,
    ProjectItem,
    ProjectItemField,
    ProjectMember,
    ProjectSection,
)
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}
_ENC_KEY = Fernet.generate_key().decode()

BASE_URL = '/api/v1/app/projects/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(
        email=email, name='Test User', password='pass123', tenant=tenant
    )
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE, ENCRYPTION_KEY=_ENC_KEY)
class TestProjectViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('proj-corp')
        self.user = _create_superuser(self.tenant, 'u@proj.com')
        self.client.force_authenticate(user=self.user)
        self.slug_header = {'HTTP_X_TENANT_SLUG': 'proj-corp'}

    # ── List ──────────────────────────────────────────────────────────────────

    def test_list_projects_empty(self):
        response = self.client.get(BASE_URL, **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['projects'], [])

    # ── Create ────────────────────────────────────────────────────────────────

    def test_create_project_success(self):
        data = {'name': 'My Vault', 'description': 'Test vault', 'color': '#ff0000'}
        response = self.client.post(BASE_URL + 'create/', data, **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        project = Project.objects.get(name='My Vault')
        self.assertEqual(project.tenant, self.tenant)

    def test_create_exceeds_plan_limit(self):
        with patch('apps.projects.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            data = {'name': 'Overflow', 'color': '#000000'}
            response = self.client.post(BASE_URL + 'create/', data, **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Detail ────────────────────────────────────────────────────────────────

    def test_project_detail_includes_sections(self):
        project = Project.objects.create(
            tenant=self.tenant, created_by=self.user, name='Detail Test'
        )
        ProjectSection.objects.create(project=project, name='Login Creds', order=0)
        response = self.client.get(f'{BASE_URL}{project.pk}/', **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sections = response.json()['project']['sections']
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0]['name'], 'Login Creds')

    # ── Update ────────────────────────────────────────────────────────────────

    def test_update_project(self):
        project = Project.objects.create(
            tenant=self.tenant, created_by=self.user, name='Old Name'
        )
        response = self.client.patch(
            f'{BASE_URL}{project.pk}/update/',
            {'name': 'New Name'},
            **self.slug_header,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        project.refresh_from_db()
        self.assertEqual(project.name, 'New Name')

    # ── Delete ────────────────────────────────────────────────────────────────

    def test_delete_project(self):
        project = Project.objects.create(
            tenant=self.tenant, created_by=self.user, name='To Delete'
        )
        response = self.client.delete(f'{BASE_URL}{project.pk}/delete/', **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Project.objects.filter(pk=project.pk).exists())

    # ── Sections ──────────────────────────────────────────────────────────────

    def test_create_section_success(self):
        project = Project.objects.create(
            tenant=self.tenant, created_by=self.user, name='SectionTest'
        )
        data = {'name': 'SSH Keys', 'color': '#00ff00', 'order': 0}
        response = self.client.post(
            f'{BASE_URL}{project.pk}/sections/', data, **self.slug_header
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(ProjectSection.objects.filter(project=project, name='SSH Keys').exists())

    def test_reorder_sections(self):
        project = Project.objects.create(
            tenant=self.tenant, created_by=self.user, name='ReorderTest'
        )
        s1 = ProjectSection.objects.create(project=project, name='A', order=0)
        s2 = ProjectSection.objects.create(project=project, name='B', order=1)
        payload = {'order': [{'id': str(s1.pk), 'order': 1}, {'id': str(s2.pk), 'order': 0}]}
        response = self.client.patch(
            f'{BASE_URL}{project.pk}/sections/reorder/', payload, format='json', **self.slug_header
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.order, 1)
        self.assertEqual(s2.order, 0)

    # ── Items ─────────────────────────────────────────────────────────────────

    def test_create_item_success(self):
        project = Project.objects.create(
            tenant=self.tenant, created_by=self.user, name='ItemProj'
        )
        section = ProjectSection.objects.create(project=project, name='Web', order=0)
        data = {'name': 'GitHub', 'url': 'https://github.com', 'username': 'dev'}
        response = self.client.post(
            f'{BASE_URL}{project.pk}/sections/{section.pk}/items/', data, **self.slug_header
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(ProjectItem.objects.filter(section=section, name='GitHub').exists())

    # ── Fields ────────────────────────────────────────────────────────────────

    def _setup_item(self, name='FieldProj'):
        project = Project.objects.create(
            tenant=self.tenant, created_by=self.user, name=name
        )
        section = ProjectSection.objects.create(project=project, name='S', order=0)
        item = ProjectItem.objects.create(section=section, name='Item', order=0)
        return project, section, item

    def test_create_field_text(self):
        project, section, item = self._setup_item('TextFieldProj')
        data = {'label': 'Username', 'value': 'admin', 'field_type': 'text'}
        url = f'{BASE_URL}{project.pk}/sections/{section.pk}/items/{item.pk}/fields/'
        response = self.client.post(url, data, **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        field = ProjectItemField.objects.get(item=item, label='Username')
        self.assertFalse(field.is_encrypted)
        self.assertEqual(field.value, 'admin')

    def test_create_field_password_auto_encrypted(self):
        project, section, item = self._setup_item('PassFieldProj')
        data = {'label': 'Password', 'value': 'secret123', 'field_type': 'password'}
        url = f'{BASE_URL}{project.pk}/sections/{section.pk}/items/{item.pk}/fields/'
        response = self.client.post(url, data, **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        field = ProjectItemField.objects.get(item=item, label='Password')
        self.assertTrue(field.is_encrypted)
        self.assertNotEqual(field.value, 'secret123')

    # ── Reveal Password ───────────────────────────────────────────────────────

    def test_reveal_password_success(self):
        project, section, item = self._setup_item('RevealProj')
        # Create encrypted field via model save
        field = ProjectItemField(item=item, label='Pass', value='mysecret', field_type='password')
        field.save()

        url = f'{BASE_URL}{project.pk}/sections/{section.pk}/items/{item.pk}/reveal/{field.pk}/'
        response = self.client.post(url, **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['value'], 'mysecret')

        # Audit log created
        self.assertTrue(
            AuditLog.objects.filter(
                action='credentials.reveal',
                resource_type='ProjectItemField',
                resource_id=str(field.pk),
            ).exists()
        )

    def test_reveal_requires_permission(self):
        project, section, item = self._setup_item('RevealPerm')
        field = ProjectItemField(item=item, label='P', value='s3cr3t', field_type='password')
        field.save()

        # Non-superuser without the permission
        other_user = User.objects.create_user(
            email='norev@proj.com', name='No Rev', password='x', tenant=self.tenant
        )
        self.client.force_authenticate(user=other_user)
        url = f'{BASE_URL}{project.pk}/sections/{section.pk}/items/{item.pk}/reveal/{field.pk}/'
        response = self.client.post(url, **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # ── Cross-tenant isolation ────────────────────────────────────────────────

    def test_cross_tenant_project_blocked(self):
        other_tenant = _create_tenant('other-corp')
        other_user = _create_superuser(other_tenant, 'o@other.com')
        project = Project.objects.create(
            tenant=other_tenant, created_by=other_user, name='OtherVault'
        )
        # Access with self.tenant slug → should 404
        response = self.client.get(f'{BASE_URL}{project.pk}/', **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # ── Members ───────────────────────────────────────────────────────────────

    def test_member_add_and_remove(self):
        project = Project.objects.create(
            tenant=self.tenant, created_by=self.user, name='MemberProj'
        )
        member_user = User.objects.create_user(
            email='member@proj.com', name='Member', password='x', tenant=self.tenant
        )
        # Add
        add_url = f'{BASE_URL}{project.pk}/members/add/'
        response = self.client.post(
            add_url, {'email': 'member@proj.com', 'role': 'editor'}, **self.slug_header
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        membership = ProjectMember.objects.get(project=project, user=member_user)
        self.assertEqual(membership.role, 'editor')

        # Remove
        remove_url = f'{BASE_URL}{project.pk}/members/{membership.pk}/'
        response = self.client.delete(remove_url, **self.slug_header)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(ProjectMember.objects.filter(pk=membership.pk).exists())
