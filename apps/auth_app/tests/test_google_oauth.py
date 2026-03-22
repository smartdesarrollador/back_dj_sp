"""Tests for Google OAuth 2.0 views (Hub Client Portal only)."""
import json
import urllib.parse
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse


class GoogleOAuthInitViewTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_missing_next_returns_400(self):
        response = self.client.get('/api/v1/auth/google/')
        self.assertEqual(response.status_code, 400)

    def test_wrong_next_returns_400(self):
        response = self.client.get('/api/v1/auth/google/?next=admin')
        self.assertEqual(response.status_code, 400)

    def test_next_hub_redirects_to_google(self):
        response = self.client.get('/api/v1/auth/google/?next=hub')
        self.assertEqual(response.status_code, 302)
        location = response['Location']
        self.assertIn('accounts.google.com', location)
        self.assertIn('state=', location)
        self.assertIn('openid', location)

    def test_state_saved_in_cache(self):
        response = self.client.get('/api/v1/auth/google/?next=hub')
        self.assertEqual(response.status_code, 302)
        location = response['Location']
        parsed = urllib.parse.urlparse(location)
        params = urllib.parse.parse_qs(parsed.query)
        state = params['state'][0]
        self.assertEqual(cache.get(f'google_oauth_state:{state}'), 'hub')


class GoogleOAuthCallbackViewTests(TestCase):
    CALLBACK_URL = '/api/v1/auth/google/callback/'

    def setUp(self):
        cache.clear()

    def _set_state(self, state: str) -> None:
        cache.set(f'google_oauth_state:{state}', 'hub', timeout=300)

    def test_invalid_state_redirects_to_hub_with_error(self):
        response = self.client.get(self.CALLBACK_URL + '?state=bad&code=x')
        self.assertEqual(response.status_code, 302)
        location = response['Location']
        self.assertIn('localhost:5175', location)
        self.assertIn('error=', location)
        self.assertIn('invalid_state', location)

    def test_google_error_param_redirects_to_hub_with_error(self):
        response = self.client.get(self.CALLBACK_URL + '?error=access_denied&state=x')
        self.assertEqual(response.status_code, 302)
        self.assertIn('error=', response['Location'])

    @patch('apps.auth_app.google_oauth_views.requests.get')
    @patch('apps.auth_app.google_oauth_views.requests.post')
    def test_valid_callback_new_user_creates_tenant_and_redirects(
        self, mock_post, mock_get
    ):
        state = 'validstate123'
        self._set_state(state)

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'access_token': 'goog_tok'},
        )
        mock_post.return_value.raise_for_status = lambda: None

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                'email': 'newuser@example.com',
                'name': 'New User',
                'email_verified': True,
            },
        )
        mock_get.return_value.raise_for_status = lambda: None

        response = self.client.get(
            self.CALLBACK_URL + f'?state={state}&code=authcode'
        )
        self.assertEqual(response.status_code, 302)
        location = response['Location']
        self.assertIn('localhost:5175', location)
        self.assertIn('access_token=', location)
        self.assertIn('refresh_token=', location)
        self.assertIn('user=', location)
        self.assertIn('tenant=', location)

        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.get(email='newuser@example.com')
        self.assertTrue(user.email_verified)
        self.assertIsNotNone(user.tenant)

    @patch('apps.auth_app.google_oauth_views.requests.get')
    @patch('apps.auth_app.google_oauth_views.requests.post')
    def test_valid_callback_existing_user_no_new_tenant(
        self, mock_post, mock_get
    ):
        from apps.tenants.models import Tenant
        from django.contrib.auth import get_user_model
        User = get_user_model()

        tenant = Tenant.objects.create(
            name='Existing Co', slug='existing-co', subdomain='existing-co', plan='free'
        )
        user = User(email='existing@example.com', name='Existing', tenant=tenant, email_verified=True)
        user.set_unusable_password()
        user.save()

        tenant_count_before = Tenant.objects.count()

        state = 'existingstate'
        self._set_state(state)

        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {'access_token': 'goog_tok'},
        )
        mock_post.return_value.raise_for_status = lambda: None
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                'email': 'existing@example.com',
                'name': 'Existing',
                'email_verified': True,
            },
        )
        mock_get.return_value.raise_for_status = lambda: None

        response = self.client.get(
            self.CALLBACK_URL + f'?state={state}&code=authcode'
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('access_token=', response['Location'])
        # No new tenant created
        self.assertEqual(Tenant.objects.count(), tenant_count_before)

    @patch('apps.auth_app.google_oauth_views.requests.get')
    @patch('apps.auth_app.google_oauth_views.requests.post')
    def test_unverified_email_redirects_with_error(self, mock_post, mock_get):
        state = 'unverifstate'
        self._set_state(state)

        mock_post.return_value = MagicMock(
            status_code=200, json=lambda: {'access_token': 'tok'}
        )
        mock_post.return_value.raise_for_status = lambda: None
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                'email': 'unverified@example.com',
                'name': 'No Verify',
                'email_verified': False,
            },
        )
        mock_get.return_value.raise_for_status = lambda: None

        response = self.client.get(
            self.CALLBACK_URL + f'?state={state}&code=authcode'
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn('email_not_verified', response['Location'])
