"""
Digital Services public views (no authentication required).

URL namespace: /api/v1/public/

Endpoints:
  GET  profiles/<username>/                   → Public profile + digital card
  GET  landing/<username>/                    → Public landing page
  GET  portafolio/<username>/                 → Public portfolio list
  GET  portafolio/<username>/<slug>/          → Single portfolio item
  GET  cv/<username>/                         → Public CV
  POST track-share/<username>/                → Track a share event
"""
from collections import Counter

from drf_spectacular.utils import extend_schema
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.digital_services.analytics import track_share, track_view
from apps.digital_services.models import (
    CVDocument,
    DigitalCard,
    LandingTemplate,
    PageEvent,
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
        track_view(request, profile, 'tarjeta')
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
        track_view(request, profile, 'landing')
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
        track_view(request, profile, 'portafolio')
        items = PortfolioItem.objects.filter(profile=profile, is_published=True)
        portfolio_settings = getattr(profile, 'portfolio_settings', None)
        style_preset = portfolio_settings.style_preset if portfolio_settings else 'modern'
        theme_colors = portfolio_settings.theme_colors if portfolio_settings else {}
        hero_content = portfolio_settings.hero_content if portfolio_settings else {}
        contact_content = portfolio_settings.contact_content if portfolio_settings else {}
        about_content = portfolio_settings.about_content if portfolio_settings else {}
        skills_content = portfolio_settings.skills_content if portfolio_settings else {}
        services_content = portfolio_settings.services_content if portfolio_settings else {}
        testimonials_content = portfolio_settings.testimonials_content if portfolio_settings else {}
        card = DigitalCard.objects.filter(profile=profile).first()

        tech_counter = Counter()
        for item in items:
            tech_counter.update(item.technologies or [])
        skills = [name for name, _ in tech_counter.most_common()]

        return Response({
            'profile': PublicProfileSerializer(profile).data,
            'items': PortfolioItemSerializer(items, many=True).data,
            'style_preset': style_preset,
            'theme_colors': theme_colors,
            'hero_content': hero_content,
            'contact_content': contact_content,
            'about_content': about_content,
            'skills_content': skills_content,
            'services_content': services_content,
            'testimonials_content': testimonials_content,
            'skills': skills,
            'digital_card': DigitalCardSerializer(card).data if card else None,
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
            item = PortfolioItem.objects.get(profile=profile, slug=slug, is_published=True)
        except PortfolioItem.DoesNotExist:
            return _NOT_FOUND
        track_view(request, profile, 'portafolio')
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
        if not cv or not cv.is_published:
            return _NOT_FOUND
        track_view(request, profile, 'cv')
        return Response({
            'profile': PublicProfileSerializer(profile).data,
            'cv': CVDocumentSerializer(cv).data,
        })


class TrackShareView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=['public'],
        summary='Track a share event for a public digital-service page',
        auth=[],
    )
    def post(self, request, username: str):
        profile = _get_public_profile(username)
        if not profile:
            return _NOT_FOUND
        service = request.data.get('service')
        if service not in dict(PageEvent.SERVICE_CHOICES):
            return Response(
                {'error': {'code': 'invalid_service', 'message': 'Invalid service.'}},
                status=400,
            )
        track_share(profile, service)
        return Response(status=204)
