"""
Tenant serializers for the Admin Panel clients view.
"""
from rest_framework import serializers

from apps.tenants.models import Tenant
from utils.plans import PLAN_FEATURES

PLAN_NAME_MAP = {
    'free': 'Free',
    'starter': 'Starter',
    'professional': 'Professional',
    'enterprise': 'Enterprise',
}

PLAN_MRR_MAP = {
    'free': 0,
    'starter': 29,
    'professional': 99,
    'enterprise': 299,
}

STATUS_MAP = {
    'trialing': 'trial',
    'canceled': 'cancelled',
}


class ClientUserSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    email = serializers.EmailField()
    is_active = serializers.BooleanField()
    roles = serializers.SerializerMethodField()

    def get_roles(self, obj) -> list[str]:
        return list(obj.user_roles.values_list('role__name', flat=True))


class ClientSubscriptionSerializer(serializers.Serializer):
    status = serializers.SerializerMethodField()
    plan = serializers.CharField(source='plan')
    plan_name = serializers.SerializerMethodField()
    mrr = serializers.SerializerMethodField()
    trial_ends_at = serializers.SerializerMethodField()

    def get_status(self, obj) -> str:
        raw = obj.status
        return STATUS_MAP.get(raw, raw)

    def get_plan_name(self, obj) -> str:
        return PLAN_NAME_MAP.get(obj.plan, obj.plan.title())

    def get_mrr(self, obj) -> int:
        return PLAN_MRR_MAP.get(obj.plan, 0)

    def get_trial_ends_at(self, obj):
        if obj.status == 'trialing' and obj.trial_end:
            return obj.trial_end
        return None


class ClientListSerializer(serializers.ModelSerializer):
    primary_color = serializers.SerializerMethodField()
    admin_email = serializers.SerializerMethodField()
    subscription = serializers.SerializerMethodField()
    usage = serializers.SerializerMethodField()
    recent_users = serializers.SerializerMethodField()

    class Meta:
        model = Tenant
        fields = [
            'id', 'name', 'slug', 'subdomain', 'is_active',
            'primary_color', 'created_at',
            'admin_email', 'subscription', 'usage', 'recent_users',
        ]

    def get_primary_color(self, obj) -> str | None:
        return obj.branding.get('primary_color') if obj.branding else None

    def get_admin_email(self, obj) -> str:
        from apps.rbac.models import UserRole
        user_role = (
            UserRole.objects.filter(
                role__name='Owner',
                role__is_system_role=True,
                user__tenant=obj,
                user__is_active=True,
            )
            .select_related('user')
            .first()
        )
        if user_role:
            return user_role.user.email
        # Fallback: first active user in tenant
        first_user = obj.users.filter(is_active=True).first()
        return first_user.email if first_user else ''

    def get_subscription(self, obj) -> dict:
        try:
            sub = obj.subscription
            return ClientSubscriptionSerializer(sub).data
        except Exception:
            return {
                'status': 'active',
                'plan': obj.plan,
                'plan_name': PLAN_NAME_MAP.get(obj.plan, obj.plan.title()),
                'mrr': PLAN_MRR_MAP.get(obj.plan, 0),
                'trial_ends_at': None,
            }

    def get_usage(self, obj) -> dict:
        plan = obj.plan
        plan_cfg = PLAN_FEATURES.get(plan, PLAN_FEATURES['free'])
        return {
            'users': {
                'current': obj.users.count(),
                'limit': plan_cfg['max_users'],
            },
            'storage': {
                'current_gb': 0,
                'limit_gb': plan_cfg['storage_gb'],
            },
            'api_calls': {
                'current': 0,
                'limit': plan_cfg['api_calls_per_month'],
            },
        }

    def get_recent_users(self, obj) -> list:
        users = obj.users.order_by('-created_at')[:5]
        return ClientUserSerializer(users, many=True).data
