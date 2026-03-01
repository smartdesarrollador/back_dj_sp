"""
Digital Services public views (no authentication required).

URL namespace: /api/v1/public/

Endpoints:
  GET  profiles/<username>/                   → Public profile + digital card
  GET  landing/<username>/                    → Public landing page
  GET  portafolio/<username>/                 → Public portfolio list
  GET  portafolio/<username>/<slug>/          → Single portfolio item
  GET  cv/<username>/                         → Public CV
"""
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.digital_services.models import (
    CVDocument,
    DigitalCard,
    LandingTemplate,
    PortfolioItem,
    PublicProfile,
)
from apps.digital_services.serializers import (
    CVDocumentSerializer,
    DigitalCardSerializer,
    LandingTemplateSerializer,
    PortfolioItemSerializer,
    PublicProfileSerializer,
)

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Profile not found or not public.'}},
    status=404,
)


def _get_public_profile(username: str) -> PublicProfile | None:
    """Return a public profile or None if not found / not public."""
    try:
        return PublicProfile.objects.get(username=username, is_public=True)
    except PublicProfile.DoesNotExist:
        return None


class PublicProfileDetailView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=['public'],
        summary='Get public profile with digital card',
        auth=[],
    )
    def get(self, request, username: str):
        profile = _get_public_profile(username)
        if not profile:
            return _NOT_FOUND
        card = DigitalCard.objects.filter(profile=profile).first()
        return Response({
            'profile': PublicProfileSerializer(profile).data,
            'digital_card': DigitalCardSerializer(card).data if card else None,
        })


class PublicLandingView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=['public'],
        summary='Get public landing page',
        auth=[],
    )
    def get(self, request, username: str):
        profile = _get_public_profile(username)
        if not profile:
            return _NOT_FOUND
        landing = LandingTemplate.objects.filter(profile=profile).first()
        if not landing:
            return _NOT_FOUND
        return Response({
            'profile': PublicProfileSerializer(profile).data,
            'landing': LandingTemplateSerializer(landing).data,
        })


class PublicPortfolioView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=['public'],
        summary='List public portfolio items',
        auth=[],
    )
    def get(self, request, username: str):
        profile = _get_public_profile(username)
        if not profile:
            return _NOT_FOUND
        items = PortfolioItem.objects.filter(profile=profile)
        return Response({
            'profile': PublicProfileSerializer(profile).data,
            'items': PortfolioItemSerializer(items, many=True).data,
        })


class PublicPortfolioItemView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=['public'],
        summary='Get single public portfolio item',
        auth=[],
    )
    def get(self, request, username: str, slug: str):
        profile = _get_public_profile(username)
        if not profile:
            return _NOT_FOUND
        try:
            item = PortfolioItem.objects.get(profile=profile, slug=slug)
        except PortfolioItem.DoesNotExist:
            return _NOT_FOUND
        return Response({'item': PortfolioItemSerializer(item).data})


class PublicCVView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=['public'],
        summary='Get public CV document',
        auth=[],
    )
    def get(self, request, username: str):
        profile = _get_public_profile(username)
        if not profile:
            return _NOT_FOUND
        cv = CVDocument.objects.filter(profile=profile).first()
        if not cv:
            return _NOT_FOUND
        return Response({
            'profile': PublicProfileSerializer(profile).data,
            'cv': CVDocumentSerializer(cv).data,
        })
