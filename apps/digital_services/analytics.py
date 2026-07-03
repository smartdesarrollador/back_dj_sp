"""
Analytics tracking helpers for public digital-service pages.

Shared between public_views.py (writes: track_view / track_share) and
views.py (reads: DigitalAnalyticsView via build_service_analytics).

Privacy: never persists raw IP or User-Agent. session_hash is a daily-rotating
SHA-256 peppered with SECRET_KEY, so it cannot be recomputed offline by anyone
without access to the server's secret.
"""
import hashlib
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from apps.digital_services.models import PageEvent, PublicProfile


def _client_ip(request) -> str:
    """Same pattern as apps/contact/views.py::_get_client_ip."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '')


def _session_hash(request) -> str:
    ip = _client_ip(request)
    ua = request.META.get('HTTP_USER_AGENT', '')
    day = timezone.now().date().isoformat()
    raw = f'{settings.SECRET_KEY}:{ip}:{ua}:{day}'
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _referrer_source(request) -> str:
    raw = request.META.get('HTTP_REFERER', '')
    if not raw:
        return ''
    return urlparse(raw).netloc


def track_view(request, profile: PublicProfile, service: str) -> None:
    PageEvent.objects.create(
        profile=profile,
        service=service,
        event_type=PageEvent.EVENT_VIEW,
        session_hash=_session_hash(request),
        referrer=_referrer_source(request),
    )


def track_share(profile: PublicProfile, service: str) -> None:
    PageEvent.objects.create(profile=profile, service=service, event_type=PageEvent.EVENT_SHARE)


def build_service_analytics(profile: PublicProfile, service: str, days: int) -> dict:
    now = timezone.now()
    start = now - timedelta(days=days)
    prev_start = start - timedelta(days=days)

    views_qs = PageEvent.objects.filter(
        profile=profile, service=service, event_type=PageEvent.EVENT_VIEW,
    )
    current = views_qs.filter(created_at__gte=start)

    total_views = current.count()
    unique_views = current.values('session_hash').distinct().count()
    shares = PageEvent.objects.filter(
        profile=profile, service=service, event_type=PageEvent.EVENT_SHARE, created_at__gte=start,
    ).count()

    prev_total = views_qs.filter(created_at__gte=prev_start, created_at__lt=start).count()
    change_percent = (
        round((total_views - prev_total) / prev_total * 100, 1) if prev_total > 0 else None
    )

    daily = (
        current.annotate(date=TruncDate('created_at'))
        .values('date')
        .annotate(views=Count('id'), unique_views=Count('session_hash', distinct=True))
        .order_by('date')
    )
    data = [
        {'date': row['date'].isoformat(), 'views': row['views'], 'unique_views': row['unique_views']}
        for row in daily
    ]

    referrer_rows = (
        current.exclude(referrer='')
        .values('referrer')
        .annotate(count=Count('id'))
        .order_by('-count')[:10]
    )
    referrers = [{'source': row['referrer'], 'visits': row['count']} for row in referrer_rows]

    return {
        'service': service,
        'total_views': total_views,
        'unique_views': unique_views,
        'shares': shares,
        'change_percent': change_percent,
        'data': data,
        'referrers': referrers,
    }
