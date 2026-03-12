from django.conf import settings
from django.db.models import Sum
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermission
from apps.subscriptions.models import Subscription

from .models import Referral, ReferralCode
from .serializers import ReferralDashboardSerializer


class ReferralView(APIView):
    """GET /api/v1/app/referrals/ — Código, stats y lista de referidos."""
    permission_classes = [HasPermission('referrals.read')]

    @extend_schema(
        tags=['hub-referrals'],
        summary='Dashboard de referidos: código, stats y lista de referidos',
        responses={
            200: OpenApiResponse(description='{ code, referral_url, stats, referrals }'),
            403: OpenApiResponse(description='Sin permiso referrals.read'),
        },
    )
    def get(self, request: Request) -> Response:
        tenant = request.tenant
        ref_code, _ = ReferralCode.objects.get_or_create(
            tenant=tenant,
            defaults={'code': ReferralCode.generate_code(tenant)},
        )

        referrals_qs = Referral.objects.filter(
            referrer=tenant,
        ).select_related('referred').order_by('-created_at')

        active_referrals = referrals_qs.filter(status='active')
        credits_earned = active_referrals.aggregate(
            total=Sum('credit_amount'),
        )['total'] or 0

        try:
            subscription = Subscription.objects.get(tenant=tenant)
            available_credits = subscription.credit_balance
        except Subscription.DoesNotExist:
            available_credits = 0

        base_url = getattr(settings, 'REFERRAL_BASE_URL', 'https://hub.app')
        referral_url = f'{base_url}/register?ref={ref_code.code}'

        data = {
            'code': ref_code.code,
            'referral_url': referral_url,
            'stats': {
                'referred': referrals_qs.count(),
                'credits_earned': credits_earned,
                'available_credits': available_credits,
            },
            'referrals': referrals_qs,
        }
        serializer = ReferralDashboardSerializer(data)
        return Response(serializer.data)
