"""
Tests for PASO 14 — Forms module.
Covers: list empty, create with questions, plan limit, public submit, export feature gate.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.forms_app.models import Form, FormQuestion, FormResponse
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/app/forms/'


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
class TestFormsViews(APITestCase):
    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('form-corp')
        self.user = _create_superuser(self.tenant, 'u@form.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'form-corp'}

    # ── List empty ────────────────────────────────────────────────────────────

    def test_list_forms_empty(self):
        response = self.client.get(BASE_URL, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['forms'], [])

    # ── Create with questions ─────────────────────────────────────────────────

    def test_create_form_with_questions(self):
        data = {
            'title': 'Customer Feedback',
            'description': 'Tell us what you think',
            'questions': [
                {'label': 'Name', 'question_type': 'text', 'order': 0, 'required': True},
                {
                    'label': 'Rating',
                    'question_type': 'multiple_choice',
                    'order': 1,
                    'options': ['1', '2', '3', '4', '5'],
                },
            ],
        }
        response = self.client.post(BASE_URL, data, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()['form']
        self.assertEqual(body['title'], 'Customer Feedback')
        self.assertIsNotNone(body['public_url_slug'])
        self.assertNotEqual(body['public_url_slug'], '')
        self.assertEqual(len(body['questions']), 2)
        form = Form.objects.get(tenant=self.tenant, title='Customer Feedback')
        self.assertEqual(FormQuestion.objects.filter(form=form).count(), 2)

    # ── Plan limit ────────────────────────────────────────────────────────────

    def test_create_form_exceeds_plan_limit(self):
        with patch('apps.forms_app.views.check_plan_limit') as mock_limit:
            from core.exceptions import PlanLimitExceeded
            mock_limit.side_effect = PlanLimitExceeded()
            data = {'title': 'Overflow Form'}
            response = self.client.post(BASE_URL, data, format='json', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Public submit increments response_count ───────────────────────────────

    def test_public_submit_increments_response_count(self):
        form = Form.objects.create(
            tenant=self.tenant,
            user=self.user,
            title='Public Form',
            status='active',
        )
        submit_url = f'{BASE_URL}public/{form.public_url_slug}/submit/'
        response = self.client.post(submit_url, {'data': {'name': 'Alice'}}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        form.refresh_from_db()
        self.assertEqual(form.response_count, 1)
        self.assertEqual(FormResponse.objects.filter(form=form).count(), 1)

    # ── Export requires feature flag ──────────────────────────────────────────

    def test_form_export_requires_feature(self):
        free_tenant = _create_tenant('free-form', plan='free')
        free_user = _create_superuser(free_tenant, 'u@free-form.com')
        self.client.force_authenticate(user=free_user)
        form = Form.objects.create(
            tenant=free_tenant,
            user=free_user,
            title='Free Form',
            status='active',
        )
        export_url = f'{BASE_URL}{form.pk}/export/'
        response = self.client.get(export_url, **{'HTTP_X_TENANT_SLUG': 'free-form'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
