"""
Tests for PASO 20 — Rate Limiting (throttle classes + functional 429 responses).
Groups:
  1. Throttle configuration on views (3 tests)
  2. Functional rate limit — 429 response (5 tests)
  3. PlanBasedUserThrottle unit tests (2 tests)
"""
from unittest.mock import MagicMock

from django.core.cache import cache
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.auth_app.views import (
    LoginView,
    MFAValidateView,
    RegisterView,
)
from utils.throttles import (
    LoginRateThrottle,
    MFARateThrottle,
    PlanBasedUserThrottle,
    RegisterRateThrottle,
)

BASE_AUTH = '/api/v1/auth/'

_LOCMEM_CACHE = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
    }
}


# ─── Group 1: Throttle configuration on views ─────────────────────────────────

class ThrottleConfigTest(TestCase):
    def test_login_view_has_login_rate_throttle(self):
        self.assertIn(LoginRateThrottle, LoginView.throttle_classes)

    def test_register_view_has_register_rate_throttle(self):
        self.assertIn(RegisterRateThrottle, RegisterView.throttle_classes)

    def test_mfa_validate_has_mfa_rate_throttle(self):
        self.assertIn(MFARateThrottle, MFAValidateView.throttle_classes)


# ─── Group 2: Functional 429 responses ────────────────────────────────────────

@override_settings(
    CACHES=_LOCMEM_CACHE,
    REST_FRAMEWORK={
        'DEFAULT_THROTTLE_CLASSES': [
            'rest_framework.throttling.AnonRateThrottle',
            'utils.throttles.PlanBasedUserThrottle',
        ],
        'DEFAULT_THROTTLE_RATES': {
            'anon': '60/minute',
            'login': '2/minute',
            'register': '2/hour',
            'mfa': '2/minute',
            'forgot_password': '2/hour',
        },
        'DEFAULT_AUTHENTICATION_CLASSES': [
            'rest_framework_simplejwt.authentication.JWTAuthentication',
        ],
        'DEFAULT_PERMISSION_CLASSES': [
            'rest_framework.permissions.IsAuthenticated',
        ],
        'EXCEPTION_HANDLER': 'core.exceptions.custom_exception_handler',
    },
)
class RateLimitFunctionalTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        cache.clear()

    def test_login_rate_limit_returns_429(self):
        url = f'{BASE_AUTH}login'
        payload = {'email': 'a@test.com', 'password': 'x'}
        # First 2 requests: throttle allows them (returns 400 bad credentials, not 429)
        self.client.post(url, payload)
        self.client.post(url, payload)
        # 3rd request must be throttled
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_register_rate_limit_returns_429(self):
        url = f'{BASE_AUTH}register'
        payload = {'email': 'a@test.com', 'password': 'x', 'name': 'A'}
        self.client.post(url, payload)
        self.client.post(url, payload)
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_forgot_password_rate_limit_returns_429(self):
        url = f'{BASE_AUTH}forgot-password'
        payload = {'email': 'a@test.com'}
        self.client.post(url, payload)
        self.client.post(url, payload)
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_mfa_rate_limit_returns_429(self):
        url = f'{BASE_AUTH}mfa/validate'
        payload = {'mfa_token': 'fake', 'totp_code': '123456'}
        self.client.post(url, payload)
        self.client.post(url, payload)
        response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_rate_limit_response_has_retry_after_header(self):
        url = f'{BASE_AUTH}login'
        payload = {'email': 'b@test.com', 'password': 'x'}
        # Override to 1/minute to trip on second request
        with override_settings(
            REST_FRAMEWORK={
                'DEFAULT_THROTTLE_CLASSES': [
                    'rest_framework.throttling.AnonRateThrottle',
                    'utils.throttles.PlanBasedUserThrottle',
                ],
                'DEFAULT_THROTTLE_RATES': {
                    'anon': '60/minute',
                    'login': '1/minute',
                    'register': '3/hour',
                    'mfa': '5/minute',
                    'forgot_password': '5/hour',
                },
                'DEFAULT_AUTHENTICATION_CLASSES': [
                    'rest_framework_simplejwt.authentication.JWTAuthentication',
                ],
                'DEFAULT_PERMISSION_CLASSES': [
                    'rest_framework.permissions.IsAuthenticated',
                ],
                'EXCEPTION_HANDLER': 'core.exceptions.custom_exception_handler',
            }
        ):
            self.client.post(url, payload)
            response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn('Retry-After', response)


# ─── Group 3: PlanBasedUserThrottle unit tests ────────────────────────────────

@override_settings(CACHES=_LOCMEM_CACHE)
class PlanBasedUserThrottleTest(TestCase):
    def setUp(self):
        cache.clear()

    def _make_request(self, plan: str) -> MagicMock:
        tenant = MagicMock()
        tenant.plan = plan
        user = MagicMock()
        user.is_authenticated = True
        user.pk = 1
        user.tenant = tenant
        request = MagicMock()
        request.user = user
        request.META = {'REMOTE_ADDR': '127.0.0.1'}
        return request

    def test_plan_enterprise_is_unlimited(self):
        throttle = PlanBasedUserThrottle()
        request = self._make_request('enterprise')
        result = throttle.allow_request(request, None)
        self.assertTrue(result)

    def test_plan_free_has_1000_per_hour_rate(self):
        throttle = PlanBasedUserThrottle()
        request = self._make_request('free')
        throttle.allow_request(request, None)
        self.assertEqual(throttle.rate, '1000/hour')
        self.assertEqual(throttle.num_requests, 1000)
