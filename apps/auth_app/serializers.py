"""Serializers for auth_app."""
import uuid

from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.utils.text import slugify
from rest_framework import serializers

from apps.tenants.models import Tenant
from utils.validators import validate_password_strength

User = get_user_model()


class TenantSerializer(serializers.ModelSerializer):
    logo_url    = serializers.SerializerMethodField()
    favicon_url = serializers.SerializerMethodField()

    class Meta:
        model  = Tenant
        fields = ['id', 'name', 'slug', 'subdomain', 'plan', 'logo_url', 'favicon_url']

    def _abs(self, field_file):
        if not field_file:
            return None
        url = field_file.url
        if url.startswith(('http://', 'https://')):
            return url
        from django.conf import settings as _s
        base = getattr(_s, 'APP_BASE_URL', '').rstrip('/')
        if base:
            return f"{base}{url}"
        request = self.context.get('request')
        if request:
            return request.build_absolute_uri(url)
        return None

    def get_logo_url(self, obj):    return self._abs(obj.logo)
    def get_favicon_url(self, obj): return self._abs(obj.favicon)


class UserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    permissions = serializers.SerializerMethodField()
    tenant_plan = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'name', 'email', 'tenant_id', 'email_verified',
                  'mfa_enabled', 'is_staff', 'created_at', 'roles', 'permissions', 'tenant_plan']

    def get_roles(self, obj):
        return list(
            obj.user_roles.values_list('role__name', flat=True)
        )

    def get_permissions(self, obj):
        return list(
            obj.user_roles.values_list(
                'role__role_permissions__permission__codename', flat=True
            ).distinct()
        )

    def get_tenant_plan(self, obj):
        return obj.tenant.plan if obj.tenant_id else 'free'


class RegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    organization_name = serializers.CharField(max_length=255)
    ref_code = serializers.CharField(required=False, allow_blank=True, default='')
    plan = serializers.ChoiceField(
        choices=['free', 'starter', 'professional', 'enterprise'],
        required=False,
        default='free',
    )

    def validate_email(self, value):
        value = value.lower()
        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate_password(self, value):
        try:
            validate_password_strength(value)
        except Exception as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value

    def _unique_slug(self, base: str) -> str:
        slug = slugify(base)
        if not Tenant.objects.filter(slug=slug).exists():
            return slug
        return f'{slug}-{str(uuid.uuid4())[:8]}'

    @transaction.atomic
    def save(self):
        data = self.validated_data
        plan = data.get('plan', 'free')
        slug = self._unique_slug(data['organization_name'])
        tenant = Tenant.objects.create(
            name=data['organization_name'],
            slug=slug,
            subdomain=slug,
            plan=plan,
        )
        user = User.objects.create_user(
            email=data['email'],
            name=data['name'],
            password=data['password'],
            tenant=tenant,
        )
        if settings.DEBUG:
            user.email_verified = True
            user.save(update_fields=['email_verified'])

        # For paid plans: update the auto-created subscription (created by signal with free/trialing)
        # and block login until admin confirms payment.
        if plan in ('starter', 'professional', 'enterprise'):
            from apps.subscriptions.models import Subscription
            Subscription.objects.filter(tenant=tenant).update(
                plan=plan,
                status='unpaid',
                trial_start=None,
                trial_end=None,
            )
            user.is_active = False
            user.save(update_fields=['is_active'])

        from apps.rbac.models import Role, UserRole
        try:
            owner_role = Role.objects.get(name='Owner', is_system_role=True)
            UserRole.objects.create(user=user, role=owner_role)
        except Role.DoesNotExist:
            pass

        from apps.referrals.models import ReferralCode, Referral
        ReferralCode.objects.create(
            tenant=tenant,
            code=ReferralCode.generate_code(tenant),
        )

        raw_ref = self.validated_data.get('ref_code', '').strip()
        if raw_ref:
            try:
                referrer_code = ReferralCode.objects.get(code=raw_ref)
                Referral.objects.create(
                    referrer=referrer_code.tenant,
                    referred=tenant,
                    status='pending',
                )
            except (ReferralCode.DoesNotExist, Exception):
                pass

        return user, tenant, plan


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)
    totp_code = serializers.CharField(required=False, allow_blank=True)

    def validate(self, attrs):
        request = self.context.get('request')
        user = authenticate(
            request=request,
            username=attrs['email'].lower(),
            password=attrs['password'],
        )
        if not user:
            raise serializers.ValidationError('Invalid email or password.')
        if not user.is_active:
            raise serializers.ValidationError('This account has been deactivated.')
        if not user.email_verified:
            raise serializers.ValidationError('email_not_verified')
        attrs['user'] = user
        return attrs


class LogoutSerializer(serializers.Serializer):
    refresh_token = serializers.CharField()


class VerifyEmailSerializer(serializers.Serializer):
    token = serializers.CharField()


class ForgotPasswordSerializer(serializers.Serializer):
    email = serializers.EmailField()


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate_password(self, value):
        try:
            validate_password_strength(value)
        except Exception as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value


class AcceptInviteSerializer(serializers.Serializer):
    token = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate_password(self, value: str) -> str:
        try:
            validate_password_strength(value)
        except Exception as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return value


# ─── Admin serializers ────────────────────────────────────────────────────────

class AdminUserListSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'email', 'name', 'is_active', 'email_verified', 'roles', 'created_at']

    def get_roles(self, obj) -> list[str]:
        return list(obj.user_roles.values_list('role__name', flat=True))


class AdminUserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ['email', 'name', 'password']

    def create(self, validated_data: dict) -> 'User':
        return User.objects.create_user(
            email=validated_data['email'],
            name=validated_data['name'],
            password=validated_data['password'],
            tenant=self.context['request'].tenant,
        )


class AdminUserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['name', 'email']


class InviteUserSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role_id = serializers.UUIDField(required=False)


class UserRoleAssignSerializer(serializers.Serializer):
    role_id = serializers.UUIDField()


# ─── MFA serializers ──────────────────────────────────────────────────────────

class MFAVerifySetupSerializer(serializers.Serializer):
    totp_code = serializers.CharField(max_length=6)


class MFAValidateSerializer(serializers.Serializer):
    mfa_token = serializers.CharField()
    totp_code = serializers.CharField(max_length=6)


class MFADisableSerializer(serializers.Serializer):
    password = serializers.CharField(write_only=True)


class MFARecoverySerializer(serializers.Serializer):
    mfa_token = serializers.CharField()
    recovery_code = serializers.CharField()


# ─── SSO serializers ───────────────────────────────────────────────────────────

class SSOTokenRequestSerializer(serializers.Serializer):
    service = serializers.SlugField(max_length=50)


class SSOValidateRequestSerializer(serializers.Serializer):
    sso_token = serializers.CharField(max_length=64)
