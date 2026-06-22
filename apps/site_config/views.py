from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.site_config.models import FooterConfig, FooterLink
from apps.site_config.serializers import (
    FooterConfigSerializer,
    FooterConfigUpdateSerializer,
    FooterLinkSerializer,
)


def _get_footer() -> FooterConfig:
    config, _ = FooterConfig.objects.get_or_create(pk=1)
    return config


# ── Public ──────────────────────────────────────────────────────────────────

class FooterPublicView(APIView):
    permission_classes    = [AllowAny]
    authentication_classes = []

    @extend_schema(tags=['public'], summary='Get footer configuration', auth=[])
    def get(self, request):
        config = _get_footer()
        return Response(FooterConfigSerializer(config).data)


# ── Admin ────────────────────────────────────────────────────────────────────

class FooterAdminView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(tags=['admin'], summary='Get footer configuration (admin)')
    def get(self, request):
        config = _get_footer()
        return Response(FooterConfigSerializer(config).data)

    @extend_schema(tags=['admin'], summary='Update footer configuration')
    def put(self, request):
        config = _get_footer()
        serializer = FooterConfigUpdateSerializer(config, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(FooterConfigSerializer(config).data)


class FooterLinkCreateView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]

    @extend_schema(tags=['admin'], summary='Add footer link')
    def post(self, request):
        config = _get_footer()
        serializer = FooterLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = serializer.save(config=config)
        return Response(FooterLinkSerializer(link).data, status=201)


class FooterLinkDetailView(APIView):
    permission_classes = [IsAuthenticated, IsAdminUser]

    def _get_link(self, pk: int) -> FooterLink | None:
        try:
            return FooterLink.objects.get(pk=pk, config_id=1)
        except FooterLink.DoesNotExist:
            return None

    @extend_schema(tags=['admin'], summary='Update footer link')
    def patch(self, request, pk: int):
        link = self._get_link(pk)
        if not link:
            return Response({'detail': 'Not found.'}, status=404)
        serializer = FooterLinkSerializer(link, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(FooterLinkSerializer(link).data)

    @extend_schema(tags=['admin'], summary='Delete footer link')
    def delete(self, request, pk: int):
        link = self._get_link(pk)
        if not link:
            return Response({'detail': 'Not found.'}, status=404)
        link.delete()
        return Response(status=204)
