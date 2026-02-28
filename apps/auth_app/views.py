"""Auth API views — Register, Login, Refresh, Logout, VerifyEmail, ForgotPassword, ResetPassword."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from core.exceptions import InvalidToken
from .serializers import (
    ForgotPasswordSerializer,
    LoginSerializer,
    LogoutSerializer,
    RegisterSerializer,
    ResetPasswordSerializer,
    TenantSerializer,
    UserSerializer,
    VerifyEmailSerializer,
)
from .tokens import (
    TenantRefreshToken,
    create_email_verification_token,
    create_password_reset_token,
    verify_email_token,
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
            from .tokens import create_email_verification_token as create_mfa_token
            mfa_token = create_mfa_token(str(user.id))
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
