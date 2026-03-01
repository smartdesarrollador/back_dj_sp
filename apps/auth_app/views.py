"""Auth API views — Register, Login, Refresh, Logout, VerifyEmail, ForgotPassword, ResetPassword, MFA."""
import base64
import io
import secrets

import pyotp
import qrcode
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.cache import cache
from django.core.mail import send_mail
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from core.exceptions import InvalidToken
from .models import MFARecoveryCode
from .serializers import (
    ForgotPasswordSerializer,
    LoginSerializer,
    LogoutSerializer,
    MFADisableSerializer,
    MFARecoverySerializer,
    MFAValidateSerializer,
    MFAVerifySetupSerializer,
    RegisterSerializer,
    ResetPasswordSerializer,
    TenantSerializer,
    UserSerializer,
    VerifyEmailSerializer,
)
from .tokens import (
    TenantRefreshToken,
    create_email_verification_token,
    create_mfa_session_token,
    create_password_reset_token,
    verify_email_token,
    verify_mfa_session_token,
    verify_password_reset_token,
)

User = get_user_model()


def _build_token_response(user) -> dict:
    refresh = TenantRefreshToken.for_user(user)
    return {
        'access_token': str(refresh.access_token),
        'refresh_token': str(refresh),
        'user': UserSerializer(user).data,
        'tenant': TenantSerializer(user.tenant).data,
    }


class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, tenant = serializer.save()

        token = create_email_verification_token(str(user.id))
        verify_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
        send_mail(
            subject='Verify your email',
            message=f'Click the link to verify your email: {verify_url}',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,
        )

        return Response(
            {
                'user': {'id': str(user.id), 'name': user.name, 'email': user.email},
                'tenant': {'id': str(tenant.id), 'name': tenant.name, 'slug': tenant.slug, 'subdomain': tenant.subdomain},
                'message': 'Account created. Please check your email to verify your account.',
            },
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = LoginSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']

        if user.mfa_enabled:
            mfa_token = create_mfa_session_token(str(user.id))
            return Response({'mfa_required': True, 'mfa_token': mfa_token})

        return Response(_build_token_response(user))


class RefreshTokenView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        raw_token = request.data.get('refresh_token')
        if not raw_token:
            raise InvalidToken()
        try:
            refresh = RefreshToken(raw_token)
            data = {
                'access_token': str(refresh.access_token),
                'refresh_token': str(refresh),
            }
        except TokenError as exc:
            raise InvalidToken() from exc
        return Response(data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data['refresh_token'])
            token.blacklist()
        except TokenError:
            pass  # already invalidated — silently ignore
        return Response(status=status.HTTP_204_NO_CONTENT)


class VerifyEmailView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = VerifyEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = verify_email_token(serializer.validated_data['token'])
        if not user_id:
            raise InvalidToken()
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise InvalidToken()
        user.email_verified = True
        user.save(update_fields=['email_verified'])
        return Response({'message': 'Email verified successfully.'})


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = User.objects.get(email=serializer.validated_data['email'].lower())
            token = create_password_reset_token(str(user.id))
            reset_url = f"{settings.FRONTEND_URL}/reset-password?token={token}"
            send_mail(
                subject='Reset your password',
                message=f'Click the link to reset your password: {reset_url}',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
        except User.DoesNotExist:
            pass  # do not reveal whether email exists
        return Response({'message': 'If an account with that email exists, a reset link has been sent.'})


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_id = verify_password_reset_token(serializer.validated_data['token'])
        if not user_id:
            raise InvalidToken()
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise InvalidToken()
        user.set_password(serializer.validated_data['password'])
        user.save(update_fields=['password'])
        return Response({'message': 'Password reset successfully.'})


# ─── MFA views ────────────────────────────────────────────────────────────────

class MFAEnableView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        mfa_secret = pyotp.random_base32()
        cache.set(f'mfa_setup:{user.id}', mfa_secret, timeout=600)

        totp = pyotp.TOTP(mfa_secret)
        provisioning_uri = totp.provisioning_uri(name=user.email, issuer_name='RBAC Platform')

        qr = qrcode.make(provisioning_uri)
        buffer = io.BytesIO()
        qr.save(buffer, format='PNG')
        qr_b64 = base64.b64encode(buffer.getvalue()).decode()

        return Response({'provisioning_uri': provisioning_uri, 'qr_code_base64': qr_b64})


class MFAVerifySetupView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = MFAVerifySetupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        mfa_secret = cache.get(f'mfa_setup:{user.id}')
        if not mfa_secret:
            return Response(
                {'detail': 'MFA setup session expired. Please start over.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        totp = pyotp.TOTP(mfa_secret)
        if not totp.verify(serializer.validated_data['totp_code']):
            return Response({'detail': 'Invalid TOTP code.'}, status=status.HTTP_400_BAD_REQUEST)

        cache.delete(f'mfa_setup:{user.id}')
        user.mfa_secret = mfa_secret
        user.mfa_enabled = True
        user.save(update_fields=['mfa_secret', 'mfa_enabled'])

        MFARecoveryCode.objects.filter(user=user).delete()
        plain_codes = [secrets.token_hex(8) for _ in range(10)]
        MFARecoveryCode.objects.bulk_create([
            MFARecoveryCode(user=user, code_hash=make_password(code))
            for code in plain_codes
        ])

        return Response({'recovery_codes': plain_codes})


class MFAValidateView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = MFAValidateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_id = verify_mfa_session_token(serializer.validated_data['mfa_token'])
        if not user_id:
            raise InvalidToken()

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise InvalidToken()

        totp = pyotp.TOTP(user.mfa_secret)
        if not totp.verify(serializer.validated_data['totp_code']):
            return Response({'detail': 'Invalid TOTP code.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response(_build_token_response(user))


class MFADisableView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = MFADisableSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = request.user
        if not user.check_password(serializer.validated_data['password']):
            return Response({'detail': 'Invalid password.'}, status=status.HTTP_400_BAD_REQUEST)

        user.mfa_enabled = False
        user.mfa_secret = ''
        user.save(update_fields=['mfa_enabled', 'mfa_secret'])
        MFARecoveryCode.objects.filter(user=user).delete()

        return Response({'message': 'MFA has been disabled.'})


class MFARecoveryView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = MFARecoverySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_id = verify_mfa_session_token(serializer.validated_data['mfa_token'])
        if not user_id:
            raise InvalidToken()

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            raise InvalidToken()

        plain_code = serializer.validated_data['recovery_code']
        matched = None
        for rc in MFARecoveryCode.objects.filter(user=user, is_used=False):
            if check_password(plain_code, rc.code_hash):
                matched = rc
                break

        if not matched:
            return Response({'detail': 'Invalid or already used recovery code.'}, status=status.HTTP_400_BAD_REQUEST)

        matched.is_used = True
        matched.save(update_fields=['is_used'])

        return Response(_build_token_response(user))
