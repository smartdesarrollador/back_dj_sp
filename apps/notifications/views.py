from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationSerializer

_ADMIN_CATEGORIES = ['security', 'users', 'billing', 'system', 'roles']
_HUB_CATEGORIES = ['billing', 'security', 'services', 'system']

_NOT_FOUND = {'error': {'code': 'not_found', 'message': 'Notification not found.'}}
_TENANT_REQUIRED = {'error': {'code': 'tenant_required'}}


class _BaseNotificationListView(APIView):
    permission_classes = [IsAuthenticated]
    categories: list = []
    page_size: int = 50

    def get(self, request):
        tenant = getattr(request, 'tenant', None)
        if not tenant:
            return Response(_TENANT_REQUIRED, status=status.HTTP_400_BAD_REQUEST)

        qs = Notification.objects.filter(tenant=tenant, category__in=self.categories)
        total = qs.count()

        try:
            page = max(1, int(request.query_params.get('page', 1)))
        except (ValueError, TypeError):
            page = 1

        offset = (page - 1) * self.page_size
        notifications = qs[offset:offset + self.page_size]

        return Response({
            'notifications': NotificationSerializer(notifications, many=True).data,
            'pagination': {'page': page, 'per_page': self.page_size, 'total': total},
        })


class AdminNotificationListView(_BaseNotificationListView):
    categories = _ADMIN_CATEGORIES
    page_size = 100

    @extend_schema(
        tags=['admin-notifications'],
        summary='Lista notificaciones del panel admin (paginada, 100/página)',
        responses={200: OpenApiResponse(description='{ notifications, pagination }')},
    )
    def get(self, request):
        return super().get(request)


class AdminNotificationMarkReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['admin-notifications'], summary='Marca notificación como leída')
    def post(self, request, pk):
        tenant = getattr(request, 'tenant', None)
        try:
            notif = Notification.objects.get(pk=pk, tenant=tenant)
        except Notification.DoesNotExist:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)

        notif.read = True
        notif.save(update_fields=['read', 'updated_at'])
        return Response(NotificationSerializer(notif).data)


class AdminNotificationMarkAllReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['admin-notifications'], summary='Marca todas las notificaciones como leídas')
    def post(self, request):
        tenant = getattr(request, 'tenant', None)
        updated = Notification.objects.filter(tenant=tenant, read=False).update(read=True)
        return Response({'updated_count': updated})


class HubNotificationListView(_BaseNotificationListView):
    categories = _HUB_CATEGORIES
    page_size = 20

    @extend_schema(
        tags=['hub-notifications'],
        summary='Lista notificaciones Hub (billing, security, services, system)',
        responses={200: OpenApiResponse(description='{ notifications, pagination }')},
    )
    def get(self, request):
        return super().get(request)
