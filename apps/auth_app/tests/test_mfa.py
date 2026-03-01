"""
Tests for Paso 15 — MFA (TOTP) endpoints.
Covers: enable, verify-setup, validate, disable, recovery.
"""
import pyotp
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

from apps.tenants.models import Tenant
from apps.auth_app.models import MFARecoveryCode
from apps.auth_app.tokens import (
    TenantRefreshToken,
    create_mfa_session_token,
)

User = get_user_model()

MFA_ENABLE_URL = '/api/v1/auth/mfa/enable'
MFA_VERIFY_SETUP_URL = '/api/v1/auth/mfa/verify-setup'
MFA_VALIDATE_URL = '/api/v1/auth/mfa/validate'
MFA_DISABLE_URL = '/api/v1/auth/mfa/disable'
MFA_RECOVERY_URL = '/api/v1/auth/mfa/recovery'


def _create_tenant(slug='mfa-corp'):
    return Tenant.objects.create(name='MFA Corp', slug=slug, subdomain=slug)


def _create_user(tenant, email='mfa@test.com', password='ValidPass1!', verified=True):
    user = User.objects.create_user(
        email=email, name='MFA User', password=password, tenant=tenant
    )
    if verified:
        user.email_verified = True
        user.save(update_fields=['email_verified'])
    return user


def _auth_client(user):
    client = APIClient()
    refresh = TenantRefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(refresh.access_token)}')
    return client


class MFAEnableViewTest(TestCase):
    def setUp(self):
        self.tenant = _create_tenant('enable-corp')
        self.user = _create_user(self.tenant, email='enable@test.com')
        self.client = _auth_client(self.user)

    def test_mfa_enable_returns_qr(self):
        response = self.client.post(MFA_ENABLE_URL)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('provisioning_uri', data)
        self.assertIn('qr_code_base64', data)
        self.assertIn('otpauth://', data['provisioning_uri'])

    def test_mfa_enable_requires_auth(self):
        client = APIClient()
        response = client.post(MFA_ENABLE_URL)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class MFAVerifySetupViewTest(TestCase):
    def setUp(self):
        self.tenant = _create_tenant('setup-corp')
        self.user = _create_user(self.tenant, email='setup@test.com')
        self.client = _auth_client(self.user)

    def test_mfa_verify_setup_activates_mfa(self):
        # First enable to store secret in cache
        enable_resp = self.client.post(MFA_ENABLE_URL)
        self.assertEqual(enable_resp.status_code, status.HTTP_200_OK)

        # Extract secret from provisioning_uri to generate valid TOTP
        from django.core.cache import cache
        mfa_secret = cache.get(f'mfa_setup:{self.user.id}')
        self.assertIsNotNone(mfa_secret)

        totp_code = pyotp.TOTP(mfa_secret).now()
        response = self.client.post(MFA_VERIFY_SETUP_URL, {'totp_code': totp_code}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('recovery_codes', data)
        self.assertEqual(len(data['recovery_codes']), 10)

        self.user.refresh_from_db()
        self.assertTrue(self.user.mfa_enabled)
        self.assertEqual(self.user.mfa_secret, mfa_secret)
        self.assertEqual(MFARecoveryCode.objects.filter(user=self.user).count(), 10)

    def test_mfa_verify_setup_invalid_code(self):
        self.client.post(MFA_ENABLE_URL)
        response = self.client.post(MFA_VERIFY_SETUP_URL, {'totp_code': '000000'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mfa_verify_setup_no_session(self):
        response = self.client.post(MFA_VERIFY_SETUP_URL, {'totp_code': '123456'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class MFAValidateViewTest(TestCase):
    def setUp(self):
        self.tenant = _create_tenant('validate-corp')
        self.user = _create_user(self.tenant, email='validate@test.com')
        mfa_secret = pyotp.random_base32()
        self.user.mfa_secret = mfa_secret
        self.user.mfa_enabled = True
        self.user.save(update_fields=['mfa_secret', 'mfa_enabled'])
        self.mfa_secret = mfa_secret

    def test_mfa_validate_with_valid_totp(self):
        mfa_token = create_mfa_session_token(str(self.user.id))
        totp_code = pyotp.TOTP(self.mfa_secret).now()
        response = self.client.post(MFA_VALIDATE_URL, {
            'mfa_token': mfa_token,
            'totp_code': totp_code,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()
        self.assertIn('access_token', data)
        self.assertIn('refresh_token', data)

    def test_mfa_validate_invalid_totp(self):
        mfa_token = create_mfa_session_token(str(self.user.id))
        response = self.client.post(MFA_VALIDATE_URL, {
            'mfa_token': mfa_token,
            'totp_code': '000000',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mfa_validate_invalid_session_token(self):
        response = self.client.post(MFA_VALIDATE_URL, {
            'mfa_token': 'invalid-token',
            'totp_code': '123456',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class MFADisableViewTest(TestCase):
    def setUp(self):
        self.tenant = _create_tenant('disable-corp')
        self.user = _create_user(self.tenant, email='disable@test.com')
        mfa_secret = pyotp.random_base32()
        self.user.mfa_secret = mfa_secret
        self.user.mfa_enabled = True
        self.user.save(update_fields=['mfa_secret', 'mfa_enabled'])
        MFARecoveryCode.objects.create(
            user=self.user,
            code_hash='dummy_hash',
        )
        self.auth_client = _auth_client(self.user)

    def test_mfa_disable_with_correct_password(self):
        response = self.auth_client.post(MFA_DISABLE_URL, {'password': 'ValidPass1!'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertFalse(self.user.mfa_enabled)
        self.assertEqual(self.user.mfa_secret, '')
        self.assertEqual(MFARecoveryCode.objects.filter(user=self.user).count(), 0)

    def test_mfa_disable_wrong_password(self):
        response = self.auth_client.post(MFA_DISABLE_URL, {'password': 'WrongPass1!'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.mfa_enabled)

    def test_mfa_disable_requires_auth(self):
        client = APIClient()
        response = client.post(MFA_DISABLE_URL, {'password': 'ValidPass1!'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)


class MFARecoveryViewTest(TestCase):
    def setUp(self):
        self.tenant = _create_tenant('recovery-corp')
        self.user = _create_user(self.tenant, email='recovery@test.com')
        mfa_secret = pyotp.random_base32()
        self.user.mfa_secret = mfa_secret
        self.user.mfa_enabled = True
        self.user.save(update_fields=['mfa_secret', 'mfa_enabled'])

        from django.contrib.auth.hashers import make_password
        self.plain_code = 'abcd1234efgh5678'
        MFARecoveryCode.objects.create(
            user=self.user,
            code_hash=make_password(self.plain_code),
        )

    def test_mfa_recovery_code_one_time_use(self):
        # First use — should succeed
        mfa_token = create_mfa_session_token(str(self.user.id))
        response = self.client.post(MFA_RECOVERY_URL, {
            'mfa_token': mfa_token,
            'recovery_code': self.plain_code,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access_token', response.json())

        # Second use — same code should fail (is_used=True now)
        mfa_token2 = create_mfa_session_token(str(self.user.id))
        response2 = self.client.post(MFA_RECOVERY_URL, {
            'mfa_token': mfa_token2,
            'recovery_code': self.plain_code,
        }, format='json')
        self.assertEqual(response2.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mfa_recovery_invalid_code(self):
        mfa_token = create_mfa_session_token(str(self.user.id))
        response = self.client.post(MFA_RECOVERY_URL, {
            'mfa_token': mfa_token,
            'recovery_code': 'wrong_code_here',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_mfa_recovery_invalid_session_token(self):
        response = self.client.post(MFA_RECOVERY_URL, {
            'mfa_token': 'bad-token',
            'recovery_code': self.plain_code,
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
