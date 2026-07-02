from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import ListCreateAPIView, RetrieveUpdateDestroyAPIView
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from utils.cache import cache_result, make_cache_key

from .models import Announcement
from .serializers import AnnouncementSerializer, AnnouncementWriteSerializer


def _require_staff(request: Request) -> Response | None:
    if not request.user.is_staff:
        return Response({'detail': 'Staff access required.'}, status=status.HTTP_403_FORBIDDEN)
    return None


def _invalidate_announcement_cache() -> None:
    for placement in ('home', 'dashboard'):
        cache.delete(make_cache_key('announcement_active', placement))
        for limit in range(1, 6):
            cache.delete(f'announcement_top:{placement}:{limit}')


@cache_result(timeout=300, key_prefix='announcement_active')
def get_active_announcement(placement: str) -> Announcement | None:
    now = timezone.now()
    qs = (
        Announcement.objects.filter(is_active=True)
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        .filter(Q(placement=placement) | Q(placement='both'))
        .order_by('-priority', '-created_at')
    )
    return qs.first()


class AdminAnnouncementListCreateView(ListCreateAPIView):
    """
    GET  /api/v1/admin/announcements/ — lista todos los anuncios (staff)
    POST /api/v1/admin/announcements/ — crea anuncio con imagen (multipart/form-data, staff)
    """
    permission_classes = [IsAuthenticated]
    pagination_class = None

    def get_serializer_class(self):
        return AnnouncementWriteSerializer if self.request.method == 'POST' else AnnouncementSerializer

    def get_queryset(self):
        return Announcement.objects.all()

    def list(self, request: Request, *args, **kwargs) -> Response:
        if err := _require_staff(request):
            return err
        return super().list(request, *args, **kwargs)

    def create(self, request: Request, *args, **kwargs) -> Response:
        if err := _require_staff(request):
            return err
        response = super().create(request, *args, **kwargs)
        _invalidate_announcement_cache()
        return response


class AdminAnnouncementDetailView(RetrieveUpdateDestroyAPIView):
    """
    GET    /api/v1/admin/announcements/{id}/ — detalle (staff)
    PATCH  /api/v1/admin/announcements/{id}/ — editar con imagen opcional (staff)
    DELETE /api/v1/admin/announcements/{id}/ — eliminar + borrar imagen del disco (staff)
    """
    permission_classes = [IsAuthenticated]
    queryset = Announcement.objects.all()
    http_method_names = ['get', 'patch', 'delete', 'head', 'options']

    def get_serializer_class(self):
        return AnnouncementWriteSerializer if self.request.method == 'PATCH' else AnnouncementSerializer

    def retrieve(self, request: Request, *args, **kwargs) -> Response:
        if err := _require_staff(request):
            return err
        return super().retrieve(request, *args, **kwargs)

    def partial_update(self, request: Request, *args, **kwargs) -> Response:
        if err := _require_staff(request):
            return err
        kwargs['partial'] = True
        response = super().update(request, *args, **kwargs)
        _invalidate_announcement_cache()
        return response

    def destroy(self, request: Request, *args, **kwargs) -> Response:
        if err := _require_staff(request):
            return err
        instance: Announcement = self.get_object()
        if instance.image:
            instance.image.delete(save=False)
        instance.delete()
        _invalidate_announcement_cache()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PublicActiveAnnouncementView(APIView):
    """GET /api/v1/public/announcements/active/?placement=home — anuncio activo (sin auth)."""
    permission_classes = [AllowAny]

    def get(self, request: Request) -> Response:
        placement = request.query_params.get('placement', 'home')
        item = get_active_announcement(placement)
        if not item:
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = AnnouncementSerializer(item, context={'request': request})
        return Response(serializer.data)


class HubActiveAnnouncementView(APIView):
    """GET /api/v1/app/announcements/active/?placement=dashboard — anuncio activo (autenticado)."""
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        placement = request.query_params.get('placement', 'dashboard')
        item = get_active_announcement(placement)
        if not item:
            return Response(status=status.HTTP_204_NO_CONTENT)
        serializer = AnnouncementSerializer(item, context={'request': request})
        return Response(serializer.data)


class HubAnnouncementsTopView(APIView):
    """GET /api/v1/app/announcements/top/?placement=dashboard&limit=2 — top N anuncios activos (autenticado)."""
    permission_classes = [IsAuthenticated]

    def get(self, request: Request) -> Response:
        placement = request.query_params.get('placement', 'dashboard')
        try:
            limit = min(max(int(request.query_params.get('limit', 2)), 1), 5)
        except (ValueError, TypeError):
            limit = 2

        cache_key = f'announcement_top:{placement}:{limit}'
        cached = cache.get(cache_key)
        if cached is not None:
            serializer = AnnouncementSerializer(cached, many=True, context={'request': request})
            return Response(serializer.data)

        now = timezone.now()
        items = list(
            Announcement.objects.filter(is_active=True)
            .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now))
            .filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
            .filter(Q(placement=placement) | Q(placement='both'))
            .order_by('-priority', '-created_at')[:limit]
        )
        cache.set(cache_key, items, 300)
        serializer = AnnouncementSerializer(items, many=True, context={'request': request})
        return Response(serializer.data)
