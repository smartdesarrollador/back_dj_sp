"""
Core shared views.
"""
from django.db import connection
from django.core.cache import cache
from drf_spectacular.utils import extend_schema
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status


@extend_schema(tags=['system'], summary='Health check', auth=[])
@api_view(['GET'])
@permission_classes([AllowAny])
def health_check(request):
    """
    Health check endpoint for load balancers and monitoring.
    Returns status of DB, Redis and basic app info.
    """
    health = {
        'status': 'ok',
        'db': False,
        'redis': False,
        'celery': False,
    }
    http_status = status.HTTP_200_OK

    # Check database
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
        health['db'] = True
    except Exception:
        health['status'] = 'degraded'
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE

    # Check Redis
    try:
        cache.set('health_check', 'ok', timeout=5)
        result = cache.get('health_check')
        health['redis'] = result == 'ok'
    except Exception:
        health['status'] = 'degraded'
        http_status = status.HTTP_503_SERVICE_UNAVAILABLE

    # Celery check (basic: inspect active workers)
    try:
        from celery.app.control import Inspect
        from config.celery import app as celery_app
        insp = celery_app.control.inspect(timeout=1)
        workers = insp.active()
        health['celery'] = bool(workers)
    except Exception:
        health['celery'] = False  # Not critical for health status

    return Response(health, status=http_status)
