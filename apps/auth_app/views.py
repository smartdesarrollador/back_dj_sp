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
from django.db import transaction
from drf_spectacular.utils import OpenApiResponse, extend_schema

from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated

from apps.rbac.permissions import HasFeature
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from core.exceptions import InvalidToken
from utils.throttles import (
    ForgotPasswordRateThrottle,
    LoginRateThrottle,
    MFARateThrottle,
    RegisterRateThrottle,
)
from utils.uploads import validate_upload
from .models import MFARecoveryCode
from .serializers import (
    AcceptInviteSerializer,
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
    consume_payment_upload_token,
    create_email_verification_token,
    create_mfa_session_token,
    create_password_reset_token,
    create_payment_upload_token,
    peek_payment_upload_token,
    verify_email_token,
    verify_mfa_session_token,
    verify_password_reset_token,
)

User = get_user_model()


class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['auth'],
        summary='Get current user profile',
        responses={200: OpenApiResponse(description='User profile with tenant plan')},
    )
    def get(self, request):
        return Response({
            'user': UserSerializer(request.user).data,
            'tenant': TenantSerializer(request.user.tenant).data,
        })


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
    throttle_classes = [RegisterRateThrottle]

    @extend_schema(
        tags=['auth'],
        summary='Register new tenant + user',
        request=RegisterSerializer,
        responses={
            201: OpenApiResponse(description='Account created, verification email sent'),
            400: OpenApiResponse(description='Validation error'),
        },
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, tenant, plan, is_trial = serializer.save()

        import threading
        import requests as _requests
        from django.utils import timezone as _tz

        def _notify_n8n():
            url = getattr(settings, 'N8N_WEBHOOK_REGISTRO_URL', '')
            if not url:
                return
            try:
                _requests.post(url, json={
                    'event': 'tenant.registered',
                    'user': {'id': str(user.id), 'name': user.name, 'email': user.email},
                    'tenant': {'id': str(tenant.id), 'name': tenant.name,
                               'slug': tenant.slug, 'subdomain': tenant.subdomain},
                    'plan': plan,
                    'timestamp': _tz.now().isoformat(),
                }, timeout=5)
            except Exception:
                pass

        threading.Thread(target=_notify_n8n, daemon=True).start()

        token = create_email_verification_token(str(user.id))
        verify_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
        send_mail(
            subject='Verify your email',
            message=f'Click the link to verify your email: {verify_url}',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=True,
        )

        base_response = {
            'user': {'id': str(user.id), 'name': user.name, 'email': user.email},
            'tenant': {
                'id': str(tenant.id), 'name': tenant.name,
                'slug': tenant.slug, 'subdomain': tenant.subdomain,
            },
            'message': 'Account created. Please check your email to verify your account.',
        }

        if plan == 'professional' and is_trial:
            from datetime import timedelta
            from django.utils import timezone as _tz
            trial_end = (_tz.now() + timedelta(days=30)).isoformat()
            return Response(
                {**base_response, 'requires_payment': False,
                 'trial_active': True, 'trial_end': trial_end},
                status=status.HTTP_201_CREATED,
            )

        if plan in ('starter', 'professional', 'enterprise'):
            upload_token = create_payment_upload_token(str(tenant.id))
            return Response(
                {**base_response, 'requires_payment': True,
                 'payment_upload_token': upload_token, 'plan': plan},
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {**base_response, 'requires_payment': False},
            status=status.HTTP_201_CREATED,
        )


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [LoginRateThrottle]

    @extend_schema(
        tags=['auth'],
        summary='Login with email/password',
        request=LoginSerializer,
        responses={
            200: OpenApiResponse(description='JWT tokens or MFA challenge'),
            400: OpenApiResponse(description='Validation error'),
            401: OpenApiResponse(description='Invalid credentials'),
        },
    )
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

    @extend_schema(
        tags=['auth'],
        summary='Refresh access token',
        responses={
            200: OpenApiResponse(description='New access and refresh tokens'),
            401: OpenApiResponse(description='Invalid or expired refresh token'),
        },
    )
    def post(self, request):
        raw_token = request.data.get('refresh_token')
        if not raw_token:
            raise InvalidToken()
        try:
            refresh = RefreshToken(raw_token)
            user = User.objects.select_related('tenant').get(pk=refresh['user_id'])
            data = {
                'access_token': str(refresh.access_token),
                'refresh_token': str(refresh),
                'user': UserSerializer(user).data,
                'tenant': TenantSerializer(user.tenant).data,
            }
        except (TokenError, User.DoesNotExist) as exc:
            raise InvalidToken() from exc
        return Response(data)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['auth'],
        summary='Logout (blacklist token)',
        request=LogoutSerializer,
        responses={
            204: OpenApiResponse(description='Logged out successfully'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    )
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

    @extend_schema(
        tags=['auth'],
        summary='Verify email address',
        request=VerifyEmailSerializer,
        responses={
            200: OpenApiResponse(description='Email verified successfully'),
            400: OpenApiResponse(description='Invalid or expired token'),
        },
    )
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


class AcceptInviteView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['auth'],
        summary='Accept invitation and set password',
        request=AcceptInviteSerializer,
        responses={
            200: OpenApiResponse(description='Account activated successfully'),
            400: OpenApiResponse(description='Invalid token or validation error'),
        },
    )
    def post(self, request):
        serializer = AcceptInviteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user_id = verify_email_token(serializer.validated_data['token'])
        if not user_id:
            raise InvalidToken()

        try:
            user = User.objects.get(pk=user_id, is_active=False)
        except User.DoesNotExist:
            raise InvalidToken()

        user.set_password(serializer.validated_data['password'])
        user.is_active = True
        user.email_verified = True
        user.save(update_fields=['password', 'is_active', 'email_verified'])

        return Response({'message': 'Account activated successfully.'})


class ForgotPasswordView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ForgotPasswordRateThrottle]

    @extend_schema(
        tags=['auth'],
        summary='Request password reset',
        request=ForgotPasswordSerializer,
        responses={
            200: OpenApiResponse(description='Reset link sent if email exists'),
            400: OpenApiResponse(description='Validation error'),
        },
    )
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
    throttle_classes = [ForgotPasswordRateThrottle]

    @extend_schema(
        tags=['auth'],
        summary='Reset password with token',
        request=ResetPasswordSerializer,
        responses={
            200: OpenApiResponse(description='Password reset successfully'),
            400: OpenApiResponse(description='Invalid token or validation error'),
        },
    )
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
    permission_classes = [IsAuthenticated, HasFeature('mfa')]

    @extend_schema(
        tags=['auth'],
        summary='Enable MFA (get QR code)',
        responses={
            200: OpenApiResponse(description='Provisioning URI and QR code (base64 PNG)'),
            401: OpenApiResponse(description='Not authenticated'),
        },
    )
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
    permission_classes = [IsAuthenticated, HasFeature('mfa')]

    @extend_schema(
        tags=['auth'],
        summary='Verify MFA setup with TOTP',
        request=MFAVerifySetupSerializer,
        responses={
            200: OpenApiResponse(description='MFA enabled, recovery codes returned'),
            400: OpenApiResponse(description='Invalid TOTP code or expired session'),
        },
    )
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
    throttle_classes = [MFARateThrottle]

    @extend_schema(
        tags=['auth'],
        summary='Validate MFA in login',
        request=MFAValidateSerializer,
        responses={
            200: OpenApiResponse(description='JWT tokens'),
            400: OpenApiResponse(description='Invalid TOTP code'),
        },
    )
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
    permission_classes = [IsAuthenticated, HasFeature('mfa')]

    @extend_schema(
        tags=['auth'],
        summary='Disable MFA',
        request=MFADisableSerializer,
        responses={
            200: OpenApiResponse(description='MFA disabled'),
            400: OpenApiResponse(description='Invalid password'),
        },
    )
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


class ResendVerificationView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ForgotPasswordRateThrottle]

    @extend_schema(
        tags=['auth'],
        summary='Resend email verification link',
        responses={
            200: OpenApiResponse(description='Link sent if email exists and is unverified'),
        },
    )
    def post(self, request):
        email = request.data.get('email', '').lower().strip()
        try:
            user = User.objects.get(email=email, email_verified=False)
            token = create_email_verification_token(str(user.id))
            verify_url = f"{settings.FRONTEND_URL}/verify-email?token={token}"
            send_mail(
                subject='Verify your email',
                message=f'Click the link to verify your email: {verify_url}',
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                fail_silently=True,
            )
        except User.DoesNotExist:
            pass
        return Response({'message': 'If your email is registered and unverified, a new link has been sent.'})


class MFARecoveryView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [MFARateThrottle]

    @extend_schema(
        tags=['auth'],
        summary='Use recovery code',
        request=MFARecoverySerializer,
        responses={
            200: OpenApiResponse(description='JWT tokens'),
            400: OpenApiResponse(description='Invalid or used recovery code'),
        },
    )
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


class YapePaymentProofView(APIView):
    """
    Upload a Yape payment screenshot after registration with a paid plan.
    Uses a short-lived Redis token (payment_upload_token) from the register response
    to identify the tenant without requiring full JWT authentication.
    """
    permission_classes  = [AllowAny]
    authentication_classes = []
    parser_classes      = [MultiPartParser, FormParser]

    @extend_schema(
        tags=['auth'],
        summary='Upload Yape payment proof screenshot',
        responses={
            201: OpenApiResponse(description='Proof received, pending admin review'),
            400: OpenApiResponse(description='Invalid token, missing file, invalid plan or promo code'),
        },
    )
    def post(self, request):
        from decimal import Decimal

        from apps.promotions.models import PromotionRedemption
        from apps.promotions.services import (
            REASON_MESSAGES,
            compute_discount,
            find_valid_promotion,
            get_plan_price,
        )
        from apps.subscriptions.models import Subscription, YapePaymentProof
        from apps.subscriptions.tasks import notify_yape_payment

        upload_token = request.data.get('payment_upload_token', '').strip()
        if not upload_token:
            return Response({'detail': 'payment_upload_token is required.'}, status=400)

        # peek: el token solo se consume tras crear el proof — un submit que
        # falla una validación (ej. cupón agotado) debe poder reintentarse.
        tenant_id = peek_payment_upload_token(upload_token)
        if not tenant_id:
            return Response({'detail': 'Invalid or expired upload token.'}, status=400)

        screenshot = request.FILES.get('screenshot')
        if not screenshot:
            return Response({'detail': 'screenshot file is required.'}, status=400)
        validate_upload(screenshot, category='payment_proof')

        plan = request.data.get('plan', '').strip()
        if plan not in ('starter', 'professional', 'enterprise'):
            return Response({'detail': 'Invalid plan.'}, status=400)

        try:
            subscription = Subscription.objects.select_related('tenant').get(tenant_id=tenant_id)
        except Subscription.DoesNotExist:
            return Response({'detail': 'Subscription not found.'}, status=400)

        # El monto se calcula SIEMPRE en servidor; el amount del cliente se ignora.
        promo_code = str(request.data.get('promo_code', '')).strip()
        promotion = None
        if promo_code:
            promotion, reason = find_valid_promotion(
                promo_code, plan, tenant=subscription.tenant,
            )
            if promotion is None:
                return Response(
                    {'detail': REASON_MESSAGES[reason], 'promo_reason': reason},
                    status=400,
                )
            amounts = compute_discount(promotion, plan)
        else:
            price = get_plan_price(plan)
            amounts = {'original': price, 'discount': Decimal('0.00'), 'final': price}

        admin_token = secrets.token_urlsafe(48)
        with transaction.atomic():
            proof = YapePaymentProof.objects.create(
                subscription=subscription,
                screenshot=screenshot,
                plan=plan,
                amount=amounts['final'],
                admin_token=admin_token,
            )
            if promotion is not None:
                PromotionRedemption.objects.create(
                    promotion=promotion,
                    tenant=subscription.tenant,
                    yape_proof=proof,
                    plan=plan,
                    original_amount=amounts['original'],
                    discount_amount=amounts['discount'],
                    final_amount=amounts['final'],
                )

        consume_payment_upload_token(upload_token)
        notify_yape_payment.delay(str(proof.id))

        return Response(
            {'message': 'Payment proof submitted. We will review it shortly.', 'proof_id': str(proof.id)},
            status=status.HTTP_201_CREATED,
        )


class YapeActivateFreeView(APIView):
    """
    Activación directa cuando un cupón deja el monto en $0 (descuento 100%):
    no hay comprobante que subir. Revalida el cupón y el monto en servidor —
    no confía en que el Hub haya decidido omitir el paso de pago.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=['auth'],
        summary='Activate a paid plan directly via a 100%-discount promo code',
        responses={
            200: OpenApiResponse(description='Plan activated, no payment required'),
            400: OpenApiResponse(description='Invalid token, plan or promo code, or amount not zero'),
        },
    )
    def post(self, request):
        from apps.promotions.models import PromotionRedemption
        from apps.promotions.services import (
            REASON_MESSAGES,
            compute_discount,
            confirm_redemption,
            find_valid_promotion,
        )
        from apps.subscriptions.models import Subscription
        from apps.subscriptions.services import activate_subscription_plan

        upload_token = str(request.data.get('payment_upload_token', '')).strip()
        if not upload_token:
            return Response({'detail': 'payment_upload_token is required.'}, status=400)

        tenant_id = peek_payment_upload_token(upload_token)
        if not tenant_id:
            return Response({'detail': 'Invalid or expired upload token.'}, status=400)

        plan = str(request.data.get('plan', '')).strip()
        if plan not in ('starter', 'professional', 'enterprise'):
            return Response({'detail': 'Invalid plan.'}, status=400)

        promo_code = str(request.data.get('promo_code', '')).strip()
        if not promo_code:
            return Response({'detail': 'promo_code is required.'}, status=400)

        try:
            subscription = Subscription.objects.select_related('tenant').get(tenant_id=tenant_id)
        except Subscription.DoesNotExist:
            return Response({'detail': 'Subscription not found.'}, status=400)

        promotion, reason = find_valid_promotion(promo_code, plan, tenant=subscription.tenant)
        if promotion is None:
            return Response(
                {'detail': REASON_MESSAGES[reason], 'promo_reason': reason},
                status=400,
            )

        amounts = compute_discount(promotion, plan)
        if amounts['final'] != 0:
            return Response(
                {'detail': 'El cupón no cubre el 100% del plan.', 'promo_reason': 'not_free'},
                status=400,
            )

        with transaction.atomic():
            redemption = PromotionRedemption.objects.create(
                promotion=promotion,
                tenant=subscription.tenant,
                yape_proof=None,
                plan=plan,
                original_amount=amounts['original'],
                discount_amount=amounts['discount'],
                final_amount=amounts['final'],
            )
            confirm_redemption(redemption)
            activate_subscription_plan(
                subscription, plan,
                amount=amounts['final'],
                invoice_ref=f'promo_{redemption.id}',
            )

        consume_payment_upload_token(upload_token)

        return Response({
            'message': 'Plan activated with a 100% discount promo code.',
            'activated': True,
        })
