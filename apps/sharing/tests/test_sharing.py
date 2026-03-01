"""
Tests for PASO 10 — Sharing & Collaboration Module.
Covers: create, cascade, plan limits, list, update propagation,
        delete cascade, shared-with-me, audit logs, cross-tenant isolation,
        permission checks.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.projects.models import Project, ProjectItem, ProjectSection
from apps.sharing.models import Share
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/app/sharing/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(
        email=email, name='Test User', password='x', tenant=tenant
    )
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestSharingViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('corp', plan='professional')
        self.owner = _create_superuser(self.tenant, 'owner@corp.com')
        self.other = User.objects.create_user(
            email='alice@corp.com', name='Alice', password='x', tenant=self.tenant
        )
        self.project = Project.objects.create(
            tenant=self.tenant, created_by=self.owner, name='Vault'
        )
        self.client.force_authenticate(user=self.owner)
        self.slug = {'HTTP_X_TENANT_SLUG': 'corp'}

    # ─── Create ───────────────────────────────────────────────────────────────

    def test_create_share_project_success(self):
        data = {
            'resource_type': 'project',
            'resource_id': str(self.project.pk),
            'shared_with_email': 'alice@corp.com',
            'permission_level': 'editor',
        }
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Share.objects.filter(
                tenant=self.tenant,
                resource_type='project',
                resource_id=self.project.pk,
                shared_with=self.other,
            ).exists()
        )

    def test_create_share_cascades_to_sections_and_items(self):
        section = ProjectSection.objects.create(project=self.project, name='S1', order=0)
        item = ProjectItem.objects.create(section=section, name='Item1', order=0)

        data = {
            'resource_type': 'project',
            'resource_id': str(self.project.pk),
            'shared_with_email': 'alice@corp.com',
            'permission_level': 'viewer',
        }
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        self.assertTrue(
            Share.objects.filter(
                resource_type='section', resource_id=section.pk,
                shared_with=self.other, is_inherited=True,
            ).exists()
        )
        self.assertTrue(
            Share.objects.filter(
                resource_type='item', resource_id=item.pk,
                shared_with=self.other, is_inherited=True,
            ).exists()
        )

    def test_create_share_free_plan_blocked(self):
        free_tenant = _create_tenant('free-co', plan='free')
        free_owner = _create_superuser(free_tenant, 'freeowner@free.com')
        free_project = Project.objects.create(
            tenant=free_tenant, created_by=free_owner, name='FreeVault'
        )
        User.objects.create_user(
            email='bob@free.com', name='Bob', password='x', tenant=free_tenant
        )
        self.client.force_authenticate(user=free_owner)
        data = {
            'resource_type': 'project',
            'resource_id': str(free_project.pk),
            'shared_with_email': 'bob@free.com',
            'permission_level': 'viewer',
        }
        response = self.client.post(BASE_URL, data, HTTP_X_TENANT_SLUG='free-co')
        # HasFeature returns False → DRF responds 403 Forbidden (not 402)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_create_share_plan_limit_exceeded(self):
        with patch('apps.sharing.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            data = {
                'resource_type': 'project',
                'resource_id': str(self.project.pk),
                'shared_with_email': 'alice@corp.com',
                'permission_level': 'viewer',
            }
            response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    def test_create_duplicate_share_idempotent(self):
        data = {
            'resource_type': 'project',
            'resource_id': str(self.project.pk),
            'shared_with_email': 'alice@corp.com',
            'permission_level': 'viewer',
        }
        self.client.post(BASE_URL, data, **self.slug)
        self.client.post(BASE_URL, data, **self.slug)
        count = Share.objects.filter(
            resource_type='project', resource_id=self.project.pk, shared_with=self.other
        ).count()
        self.assertEqual(count, 1)

    # ─── List ─────────────────────────────────────────────────────────────────

    def test_list_shares_filtered_by_resource(self):
        Share.objects.create(
            tenant=self.tenant, resource_type='project', resource_id=self.project.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
        )
        url = f'{BASE_URL}?resource_type=project&resource_id={self.project.pk}'
        response = self.client.get(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        shares = response.json()['shares']
        self.assertEqual(len(shares), 1)
        self.assertEqual(shares[0]['resource_type'], 'project')

    # ─── Update ───────────────────────────────────────────────────────────────

    def test_update_share_permission_propagates_to_inherited(self):
        section = ProjectSection.objects.create(project=self.project, name='S', order=0)
        item = ProjectItem.objects.create(section=section, name='I', order=0)

        project_share = Share.objects.create(
            tenant=self.tenant, resource_type='project', resource_id=self.project.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
        )
        section_share = Share.objects.create(
            tenant=self.tenant, resource_type='section', resource_id=section.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
            is_inherited=True,
        )
        item_share = Share.objects.create(
            tenant=self.tenant, resource_type='item', resource_id=item.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
            is_inherited=True,
        )

        url = f'{BASE_URL}{project_share.pk}/'
        response = self.client.patch(url, {'permission_level': 'editor'}, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        section_share.refresh_from_db()
        item_share.refresh_from_db()
        self.assertEqual(section_share.permission_level, 'editor')
        self.assertEqual(item_share.permission_level, 'editor')

    def test_update_share_local_override_preserved(self):
        """Local (non-inherited) section share is NOT overwritten by project update."""
        section = ProjectSection.objects.create(project=self.project, name='S', order=0)
        project_share = Share.objects.create(
            tenant=self.tenant, resource_type='project', resource_id=self.project.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
        )
        local_section_share = Share.objects.create(
            tenant=self.tenant, resource_type='section', resource_id=section.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='admin',
            is_inherited=False,
        )

        url = f'{BASE_URL}{project_share.pk}/'
        self.client.patch(url, {'permission_level': 'editor'}, **self.slug)

        local_section_share.refresh_from_db()
        self.assertEqual(local_section_share.permission_level, 'admin')

    # ─── Delete ───────────────────────────────────────────────────────────────

    def test_delete_share_cascades(self):
        section = ProjectSection.objects.create(project=self.project, name='S', order=0)
        item = ProjectItem.objects.create(section=section, name='I', order=0)

        project_share = Share.objects.create(
            tenant=self.tenant, resource_type='project', resource_id=self.project.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
        )
        Share.objects.create(
            tenant=self.tenant, resource_type='section', resource_id=section.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
            is_inherited=True,
        )
        Share.objects.create(
            tenant=self.tenant, resource_type='item', resource_id=item.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
            is_inherited=True,
        )

        url = f'{BASE_URL}{project_share.pk}/delete/'
        response = self.client.delete(url, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        self.assertFalse(Share.objects.filter(pk=project_share.pk).exists())
        self.assertFalse(
            Share.objects.filter(resource_type='section', resource_id=section.pk).exists()
        )
        self.assertFalse(
            Share.objects.filter(resource_type='item', resource_id=item.pk).exists()
        )

    # ─── Shared With Me ───────────────────────────────────────────────────────

    def test_shared_with_me_returns_received_shares(self):
        Share.objects.create(
            tenant=self.tenant, resource_type='project', resource_id=self.project.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
        )
        self.client.force_authenticate(user=self.other)
        response = self.client.get(f'{BASE_URL}shared-with-me/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.json()['shares']), 1)

    def test_shared_with_me_filtered_by_resource_type(self):
        Share.objects.create(
            tenant=self.tenant, resource_type='project', resource_id=self.project.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
        )
        section = ProjectSection.objects.create(project=self.project, name='S', order=0)
        Share.objects.create(
            tenant=self.tenant, resource_type='section', resource_id=section.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
            is_inherited=True,
        )
        self.client.force_authenticate(user=self.other)
        response = self.client.get(
            f'{BASE_URL}shared-with-me/?resource_type=project', **self.slug
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        shares = response.json()['shares']
        self.assertEqual(len(shares), 1)
        self.assertEqual(shares[0]['resource_type'], 'project')

    # ─── Audit Logs ───────────────────────────────────────────────────────────

    def test_create_audit_log_on_share(self):
        data = {
            'resource_type': 'project',
            'resource_id': str(self.project.pk),
            'shared_with_email': 'alice@corp.com',
            'permission_level': 'viewer',
        }
        self.client.post(BASE_URL, data, **self.slug)
        self.assertTrue(
            AuditLog.objects.filter(
                action='share.created',
                resource_type='project',
                resource_id=str(self.project.pk),
            ).exists()
        )

    def test_revoke_audit_log(self):
        share = Share.objects.create(
            tenant=self.tenant, resource_type='project', resource_id=self.project.pk,
            shared_by=self.owner, shared_with=self.other, permission_level='viewer',
        )
        self.client.delete(f'{BASE_URL}{share.pk}/delete/', **self.slug)
        self.assertTrue(
            AuditLog.objects.filter(
                action='share.revoked',
                resource_type='project',
                resource_id=str(self.project.pk),
            ).exists()
        )

    # ─── Security ─────────────────────────────────────────────────────────────

    def test_cross_tenant_share_blocked(self):
        other_tenant = _create_tenant('other-co', plan='professional')
        User.objects.create_user(
            email='bob@other.com', name='Bob', password='x', tenant=other_tenant
        )
        data = {
            'resource_type': 'project',
            'resource_id': str(self.project.pk),
            'shared_with_email': 'bob@other.com',
            'permission_level': 'viewer',
        }
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_share_requires_permission_codename(self):
        non_privileged = User.objects.create_user(
            email='plain@corp.com', name='Plain', password='x', tenant=self.tenant
        )
        self.client.force_authenticate(user=non_privileged)
        data = {
            'resource_type': 'project',
            'resource_id': str(self.project.pk),
            'shared_with_email': 'alice@corp.com',
            'permission_level': 'viewer',
        }
        response = self.client.post(BASE_URL, data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
