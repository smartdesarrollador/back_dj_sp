"""
Admin views for platform-wide (cross-tenant) business metrics.

URL namespace: /api/v1/admin/reports/

Endpoints:
  GET /admin/reports/summary/            → MRR/ARR/churn/user metrics across all OTHER tenants
  GET /admin/reports/service-adoption/   → acquired vs. activated tenants per catalog service
  GET /admin/reports/vista-traffic/      → views/unique visitors/shares per public-page service
  GET /admin/reports/desktop-licenses/   → license-level sent/activated/pending/revoked funnel

Payment context: the only live payment method is Yape (manual proof-of-payment,
see apps.subscriptions.services.activate_yape_proof). There is no task that expires
a paid Subscription when its period lapses without renewal, so Subscription.status
is not a reliable "currently paying" signal — a tenant who paid once and never
renewed still shows status='active' forever. All revenue metrics here are therefore
computed from Invoice (status='paid', period_start/period_end), never from
Subscription.status.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count, OuterRef, Q, Subquery
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermission, IsStaffUser

User = get_user_model()


def _latest_paid_invoices(tenant_ids):
    """One row per tenant: its most-recent status='paid' Invoice by period_end."""
    from apps.subscriptions.models import Invoice

    return (
        Invoice.objects.filter(
            tenant_id__in=tenant_ids, status='paid', period_end__isnull=False,
        )
        .order_by('tenant_id', '-period_end')
        .distinct('tenant_id')
    )


def _compute_admin_summary(own_tenant_id, period_days: int) -> dict:
    from apps.subscriptions.models import Invoice
    from apps.tenants.models import Tenant

    now = timezone.now()
    cutoff = now - timedelta(days=period_days)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    other_tenant_ids = list(
        Tenant.objects.exclude(id=own_tenant_id).values_list('id', flat=True)
    )
    other_users = User.objects.exclude(tenant_id=own_tenant_id)

    total_users = other_users.count()
    active_users = other_users.filter(last_login__gte=cutoff).count()
    new_users_this_month = other_users.filter(created_at__gte=month_start).count()

    # ── MRR: tenants whose LATEST paid invoice still covers `now` ──────────────
    latest_paid = list(_latest_paid_invoices(other_tenant_ids))
    covered = [inv for inv in latest_paid if inv.period_end >= now]
    covered_tenant_ids = {inv.tenant_id for inv in covered}
    mrr = round(sum(inv.amount_cents for inv in covered) / 100, 2)
    arr = round(mrr * 12, 2)
    avg_revenue_per_user = round(mrr / total_users, 2) if total_users else 0.0

    # ── Churn: tenants covered at `cutoff` that are NOT covered `now` ──────────
    base_tenant_ids = set(
        Invoice.objects.filter(
            tenant_id__in=other_tenant_ids, status='paid',
            period_start__lte=cutoff, period_end__gte=cutoff,
        ).values_list('tenant_id', flat=True).distinct()
    )
    lapsed_count = len(base_tenant_ids - covered_tenant_ids)
    churn_rate = round(lapsed_count / len(base_tenant_ids) * 100, 1) if base_tenant_ids else 0.0

    # ── Trial conversions (approximation — see module docstring reasoning): ────
    # trial_start/trial_end are nulled on every terminal transition (paid or
    # lapsed), so they can't be correlated here. Use each tenant's FIRST-EVER
    # paid invoice date instead — a renewal invoice never counts, only a
    # tenant's very first payment does.
    first_invoice_date = Subquery(
        Invoice.objects.filter(tenant_id=OuterRef('pk'), status='paid')
        .order_by('invoice_date').values('invoice_date')[:1]
    )
    trial_conversions = (
        Tenant.objects.filter(id__in=other_tenant_ids)
        .annotate(first_paid_at=first_invoice_date)
        .filter(first_paid_at__gte=cutoff, first_paid_at__lte=now)
        .count()
    )

    return {
        'period_days': period_days,
        'total_users': total_users,
        'active_users': active_users,
        'new_users_this_month': new_users_this_month,
        'mrr': mrr,
        'arr': arr,
        'avg_revenue_per_user': avg_revenue_per_user,
        'churn_rate': churn_rate,
        'trial_conversions': trial_conversions,
    }


def _sso_activated_tenant_ids(service_slug, tenant_ids):
    """Tenants that launched this service at least once via SSO (workspace/vista)."""
    from apps.auth_app.models import SSOToken

    return set(
        SSOToken.objects.filter(
            service=service_slug, used_at__isnull=False, tenant_id__in=tenant_ids,
        ).values_list('tenant_id', flat=True)
    )


def _desktop_activated_tenant_ids(_service_slug, tenant_ids):
    """
    Desktop never goes through SSO (Hub renders a "Descargar" button instead of
    SSOLaunchButton for it — see ServiceCard.tsx). Activation = a license with
    both hardware_id and activated_at set (DesktopAppLicense.is_activated), joined
    through user__tenant since a license is per-user, not per-tenant. A later-
    revoked license (is_active=False) still counts — "ever activated" is the
    intended reading here, symmetric with SSO's used_at being a historical signal.
    """
    from apps.licenses.models import DesktopAppLicense

    return set(
        DesktopAppLicense.objects.exclude(hardware_id='').filter(
            activated_at__isnull=False, user__tenant_id__in=tenant_ids,
        ).values_list('user__tenant_id', flat=True)
    )


# Services with an activation signal other than SSO. Adding a 4th service with a
# non-SSO activation path requires a new entry here; anything absent falls
# through to _sso_activated_tenant_ids.
_ACTIVATION_RESOLVERS = {
    'desktop': _desktop_activated_tenant_ids,
}


def _compute_service_adoption(own_tenant_id) -> list[dict]:
    from apps.services.models import Service, TenantService
    from apps.tenants.models import Tenant

    other_tenant_ids = list(
        Tenant.objects.exclude(id=own_tenant_id).values_list('id', flat=True)
    )

    results = []
    for service in Service.objects.filter(is_active=True).order_by('slug'):
        acquired_tenant_ids = set(
            TenantService.objects.filter(
                service=service, status='active', tenant_id__in=other_tenant_ids,
            ).values_list('tenant_id', flat=True)
        )
        resolver = _ACTIVATION_RESOLVERS.get(service.slug, _sso_activated_tenant_ids)
        # Intersect with acquired defensively — activated can never exceed acquired,
        # even if an SSOToken/DesktopAppLicense outlives its TenantService row.
        activated_tenant_ids = resolver(service.slug, acquired_tenant_ids) & acquired_tenant_ids

        acquired = len(acquired_tenant_ids)
        activated = len(activated_tenant_ids)
        results.append({
            'service': service.slug,
            'name': service.name,
            'acquired': acquired,
            'activated': activated,
            'activation_rate': round(activated / acquired * 100, 1) if acquired else 0.0,
        })
    return results


def _compute_vista_traffic(own_tenant_id, period_days: int) -> dict:
    from apps.digital_services.models import PageEvent

    now = timezone.now()
    start = now - timedelta(days=period_days)

    # One grouped query, not one query per service — same idiom as
    # _compute_admin_summary/_compute_service_adoption's preference for
    # aggregate/annotate over per-item loops.
    rows = (
        PageEvent.objects
        .exclude(profile__user__tenant_id=own_tenant_id)
        .filter(created_at__gte=start)
        .values('service')
        .annotate(
            views=Count('id', filter=Q(event_type=PageEvent.EVENT_VIEW)),
            unique_views=Count(
                'session_hash', filter=Q(event_type=PageEvent.EVENT_VIEW), distinct=True,
            ),
            shares=Count('id', filter=Q(event_type=PageEvent.EVENT_SHARE)),
        )
    )
    by_slug = {row['service']: row for row in rows}
    # Iterate the catalog (SERVICE_CHOICES), not the DB rows, so every service
    # appears with zeros instead of being silently absent — same pattern as
    # _compute_service_adoption backfilling from the Service catalog.
    services = [
        {
            'service': slug,
            'views': by_slug.get(slug, {}).get('views', 0),
            'unique_views': by_slug.get(slug, {}).get('unique_views', 0),
            'shares': by_slug.get(slug, {}).get('shares', 0),
        }
        for slug, _label in PageEvent.SERVICE_CHOICES
    ]

    # Referrers: views only. Shares structurally never carry a referrer —
    # track_share() is called without a request and never sets one.
    referrer_rows = (
        PageEvent.objects
        .exclude(profile__user__tenant_id=own_tenant_id)
        .filter(event_type=PageEvent.EVENT_VIEW, created_at__gte=start)
        .exclude(referrer='')
        .values('referrer')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )
    referrers = [{'source': row['referrer'], 'visits': row['count']} for row in referrer_rows]

    return {
        'period_days': period_days,
        'services': services,
        'referrers': referrers,
    }


def _compute_desktop_license_funnel(own_tenant_id) -> dict:
    """
    License-level current-state funnel (sent/activated/pending/revoked) — a
    different question than _desktop_activated_tenant_ids above. That function
    asks "did this tenant ever activate desktop" (ever-activated, tenant-deduped,
    a later-revoked license still counts). This asks "what's the current state
    of every license issued" — buckets are mutually exclusive and match
    DesktopAppLicense.status's own priority order (revoked wins over activated),
    same semantics the existing staff "Licencias" page already filters by.
    """
    from apps.licenses.models import DesktopAppLicense

    qs = DesktopAppLicense.objects.exclude(user__tenant_id=own_tenant_id)
    counts = qs.aggregate(
        total=Count('id'),
        sent=Count('id', filter=Q(sent_at__isnull=False)),
        revoked=Count('id', filter=Q(is_active=False)),
        activated=Count(
            'id', filter=Q(is_active=True) & ~Q(hardware_id='') & Q(activated_at__isnull=False),
        ),
        pending=Count(
            'id', filter=Q(is_active=True) & (Q(hardware_id='') | Q(activated_at__isnull=True)),
        ),
    )
    activation_rate = (
        round(counts['activated'] / counts['sent'] * 100, 1) if counts['sent'] else 0.0
    )
    return {**counts, 'activation_rate': activation_rate}


class AdminSummaryView(APIView):
    # IsStaffUser is required in addition to the RBAC permission: 'customers.analytics'
    # is also granted to the tenant-scoped system 'Owner' role (every tenant's
    # registrant), but this view aggregates every OTHER tenant's billing data —
    # never gate cross-tenant data on RBAC alone (see IsStaffUser docstring).
    permission_classes = [IsStaffUser, HasPermission('customers.analytics')]

    @extend_schema(
        tags=['admin-reports'],
        summary='Get platform-wide business metrics (MRR, ARR, churn)',
        parameters=[
            OpenApiParameter('period', OpenApiTypes.INT, description='Period in days (default: 30, max: 365)'),
        ],
    )
    def get(self, request):
        try:
            period_days = min(int(request.query_params.get('period', 30)), 365)
        except (ValueError, TypeError):
            period_days = 30

        cache_key = f'admin:reports:summary:{request.tenant.pk}:{period_days}'
        data = cache.get(cache_key)
        if data is None:
            data = _compute_admin_summary(request.tenant.id, period_days)
            cache.set(cache_key, data, 300)
        return Response(data)


class ServiceAdoptionView(APIView):
    # Same reasoning as AdminSummaryView: IsStaffUser required in addition to the
    # RBAC permission, since 'customers.analytics' is also granted to the
    # tenant-scoped system 'Owner' role.
    permission_classes = [IsStaffUser, HasPermission('customers.analytics')]

    @extend_schema(
        tags=['admin-reports'],
        summary='Get acquired vs. activated tenant counts per catalog service',
    )
    def get(self, request):
        cache_key = f'admin:reports:service-adoption:{request.tenant.pk}'
        data = cache.get(cache_key)
        if data is None:
            data = {'services': _compute_service_adoption(request.tenant.id)}
            cache.set(cache_key, data, 300)
        return Response(data)


class VistaTrafficView(APIView):
    # Same reasoning as AdminSummaryView/ServiceAdoptionView.
    permission_classes = [IsStaffUser, HasPermission('customers.analytics')]

    @extend_schema(
        tags=['admin-reports'],
        summary='Get views/unique visitors/shares per public-page service, plus top referrers',
        parameters=[
            OpenApiParameter('period', OpenApiTypes.INT, description='Period in days (default: 30, max: 365)'),
        ],
    )
    def get(self, request):
        try:
            period_days = min(int(request.query_params.get('period', 30)), 365)
        except (ValueError, TypeError):
            period_days = 30

        cache_key = f'admin:reports:vista-traffic:{request.tenant.pk}:{period_days}'
        data = cache.get(cache_key)
        if data is None:
            data = _compute_vista_traffic(request.tenant.id, period_days)
            cache.set(cache_key, data, 300)
        return Response(data)


class DesktopLicenseFunnelView(APIView):
    # Same reasoning as AdminSummaryView/ServiceAdoptionView/VistaTrafficView.
    permission_classes = [IsStaffUser, HasPermission('customers.analytics')]

    @extend_schema(
        tags=['admin-reports'],
        summary='Get desktop license sent/activated/pending/revoked funnel',
    )
    def get(self, request):
        cache_key = f'admin:reports:desktop-licenses:{request.tenant.pk}'
        data = cache.get(cache_key)
        if data is None:
            data = _compute_desktop_license_funnel(request.tenant.id)
            cache.set(cache_key, data, 300)
        return Response(data)
