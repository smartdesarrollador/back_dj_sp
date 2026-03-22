"""Google OAuth 2.0 views — Hub Client Portal only."""
import base64
import json
import secrets
import urllib.parse

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import transaction
from django.http import HttpResponseRedirect
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.auth_app.serializers import TenantSerializer, UserSerializer
from apps.auth_app.tokens import TenantRefreshToken

User = get_user_model()

GOOGLE_AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
GOOGLE_TOKEN_URL = 'https://oauth2.googleapis.com/token'
GOOGLE_USERINFO_URL = 'https://www.googleapis.com/oauth2/v3/userinfo'
CACHE_KEY_PREFIX = 'google_oauth_state:'
CACHE_TTL = 300  # 5 minutes


def _hub_error_redirect(error: str) -> HttpResponseRedirect:
    hub_url = settings.FRONTEND_HUB_URL
    return HttpResponseRedirect(f'{hub_url}/auth/google/callback?error={urllib.parse.quote(error)}')


class GoogleOAuthInitView(APIView):
    """
    GET /api/v1/auth/google/?next=hub

    Generates a state token, saves it to Redis, and redirects to Google consent page.
    Only accepts ?next=hub — the Admin Panel no longer uses Google OAuth.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request: Request) -> HttpResponseRedirect:
        next_param = request.query_params.get('next', '')
        if next_param != 'hub':
            from rest_framework.response import Response
            from rest_framework import status
            return Response(
                {'detail': 'Only ?next=hub is supported.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        state_token = secrets.token_urlsafe(32)
        cache.set(f'{CACHE_KEY_PREFIX}{state_token}', 'hub', timeout=CACHE_TTL)

        params = urllib.parse.urlencode({
            'client_id': settings.GOOGLE_CLIENT_ID,
            'redirect_uri': settings.GOOGLE_REDIRECT_URI,
            'response_type': 'code',
            'scope': 'openid email profile',
            'state': state_token,
            'access_type': 'online',
        })
        return HttpResponseRedirect(f'{GOOGLE_AUTH_URL}?{params}')


class GoogleOAuthCallbackView(APIView):
    """
    GET /api/v1/auth/google/callback/

    Validates state, exchanges code for Google tokens, fetches user info,
    creates or retrieves a User+Tenant, generates our own JWTs, and
    redirects to the Hub with tokens in query params.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request: Request) -> HttpResponseRedirect:
        state = request.query_params.get('state', '')
        code = request.query_params.get('code', '')
        error = request.query_params.get('error', '')

        if error:
            return _hub_error_redirect(error)

        # Validate state from Redis
        cache_key = f'{CACHE_KEY_PREFIX}{state}'
        stored = cache.get(cache_key)
        if not stored:
            return _hub_error_redirect('invalid_state')
        cache.delete(cache_key)

        # Exchange code for Google access token
        try:
            token_resp = requests.post(
                GOOGLE_TOKEN_URL,
                data={
                    'code': code,
                    'client_id': settings.GOOGLE_CLIENT_ID,
                    'client_secret': settings.GOOGLE_CLIENT_SECRET,
                    'redirect_uri': settings.GOOGLE_REDIRECT_URI,
                    'grant_type': 'authorization_code',
                },
                timeout=10,
            )
            token_resp.raise_for_status()
            token_data = token_resp.json()
        except Exception:
            return _hub_error_redirect('token_exchange_failed')

        google_access_token = token_data.get('access_token', '')

        # Fetch user info from Google
        try:
            userinfo_resp = requests.get(
                GOOGLE_USERINFO_URL,
                headers={'Authorization': f'Bearer {google_access_token}'},
                timeout=10,
            )
            userinfo_resp.raise_for_status()
            userinfo = userinfo_resp.json()
        except Exception:
            return _hub_error_redirect('userinfo_failed')

        if not userinfo.get('email_verified', False):
            return _hub_error_redirect('email_not_verified')

        email: str = userinfo.get('email', '').lower()
        name: str = userinfo.get('name', email.split('@')[0])

        if not email:
            return _hub_error_redirect('missing_email')

        # Get or create user
        try:
            user, tenant = self._get_or_create_user(email, name)
        except Exception:
            return _hub_error_redirect('user_creation_failed')

        if not user.is_active:
            return _hub_error_redirect('account_suspended')

        # Generate our own JWTs
        refresh = TenantRefreshToken.for_user(user)
        access_token = str(refresh.access_token)
        refresh_token = str(refresh)

        # Serialize user and tenant as base64 JSON
        user_b64 = base64.b64encode(
            json.dumps(UserSerializer(user).data).encode()
        ).decode()
        tenant_b64 = base64.b64encode(
            json.dumps(TenantSerializer(tenant).data).encode()
        ).decode()

        params = urllib.parse.urlencode({
            'access_token': access_token,
            'refresh_token': refresh_token,
            'user': user_b64,
            'tenant': tenant_b64,
        })
        hub_url = settings.FRONTEND_HUB_URL
        return HttpResponseRedirect(f'{hub_url}/auth/google/callback?{params}')

    @staticmethod
    def _get_or_create_user(email: str, name: str):
        from apps.tenants.models import Tenant
        from apps.rbac.models import Role, UserRole
        from apps.referrals.models import ReferralCode
        from django.utils.text import slugify
        import uuid

        existing = User.objects.filter(email=email).select_related('tenant').first()
        if existing:
            if not existing.email_verified:
                User.objects.filter(pk=existing.pk).update(email_verified=True)
                existing.email_verified = True
            return existing, existing.tenant

        # New user — create tenant + user atomically
        with transaction.atomic():
            base_slug = slugify(email.split('@')[0])
            slug = base_slug
            if Tenant.objects.filter(slug=slug).exists():
                slug = f'{base_slug}-{str(uuid.uuid4())[:8]}'

            tenant = Tenant.objects.create(
                name=name,
                slug=slug,
                subdomain=slug,
                plan='free',
            )
            user = User(
                email=email,
                name=name,
                tenant=tenant,
                email_verified=True,
            )
            user.set_unusable_password()
            user.save()

            try:
                owner_role = Role.objects.get(name='Owner', is_system_role=True)
                UserRole.objects.create(user=user, role=owner_role)
            except Role.DoesNotExist:
                pass

            try:
                ReferralCode.objects.create(
                    tenant=tenant,
                    code=ReferralCode.generate_code(tenant),
                )
            except Exception:
                pass

        return user, tenant
