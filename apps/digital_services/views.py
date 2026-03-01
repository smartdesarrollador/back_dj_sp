"""
Digital Services private views (authenticated).

URL namespace: /api/v1/app/digital/

Endpoints:
  GET/POST  profile/                  → PublicProfile upsert
  GET/POST  tarjeta/                  → DigitalCard upsert
  POST      tarjeta/qr/               → Generate QR code (base64 PNG)
  GET/POST  landing/                  → LandingTemplate upsert
  GET/POST  portafolio/               → PortfolioItem list / create
  PATCH/DEL portafolio/<uuid:pk>/     → PortfolioItem detail
  GET/POST  cv/                       → CVDocument upsert
  GET       cv/export/                → Export CV as PDF
  GET       analytics/<str:service>/  → Basic analytics counters
  GET/POST  custom-domain/            → CustomDomain upsert
  POST      custom-domain/verify/     → Trigger domain verification
"""
import base64
import io
import secrets

import weasyprint
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.digital_services.models import (
    CVDocument,
    CustomDomain,
    DigitalCard,
    LandingTemplate,
    PortfolioItem,
    PublicProfile,
)
from apps.digital_services.serializers import (
    CVDocumentSerializer,
    CustomDomainSerializer,
    DigitalCardSerializer,
    LandingTemplateSerializer,
    PortfolioItemSerializer,
    PublicProfileSerializer,
)
from apps.rbac.permissions import HasFeature, check_plan_limit

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}},
    status=status.HTTP_404_NOT_FOUND,
)


def _get_profile(user) -> PublicProfile | None:
    """Return the PublicProfile for this user, or None."""
    try:
        return PublicProfile.objects.get(user=user)
    except PublicProfile.DoesNotExist:
        return None


# ─── Profile ──────────────────────────────────────────────────────────────────

class PublicProfileView(APIView):
    permission_classes = [HasFeature('digital_card')]

    @extend_schema(tags=['app-digital'], summary='Get own public profile')
    def get(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return _NOT_FOUND
        return Response({'profile': PublicProfileSerializer(profile).data})

    @extend_schema(tags=['app-digital'], summary='Create or update public profile')
    def post(self, request):
        profile = _get_profile(request.user)
        serializer = PublicProfileSerializer(
            instance=profile,
            data=request.data,
            partial=profile is not None,
        )
        serializer.is_valid(raise_exception=True)
        if profile:
            for field, value in serializer.validated_data.items():
                setattr(profile, field, value)
            profile.save()
        else:
            profile = PublicProfile.objects.create(
                user=request.user,
                **serializer.validated_data,
            )
        return Response({'profile': PublicProfileSerializer(profile).data})


# ─── Digital Card ─────────────────────────────────────────────────────────────

class DigitalCardView(APIView):
    permission_classes = [HasFeature('digital_card')]

    @extend_schema(tags=['app-digital'], summary='Get own digital card')
    def get(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return _NOT_FOUND
        card = DigitalCard.objects.filter(profile=profile).first()
        if not card:
            return _NOT_FOUND
        return Response({'card': DigitalCardSerializer(card).data})

    @extend_schema(tags=['app-digital'], summary='Create or update digital card')
    def post(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return Response(
                {'error': {'code': 'profile_required', 'message': 'Create a profile first.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        card = DigitalCard.objects.filter(profile=profile).first()
        serializer = DigitalCardSerializer(
            instance=card,
            data=request.data,
            partial=card is not None,
        )
        serializer.is_valid(raise_exception=True)
        if card:
            for field, value in serializer.validated_data.items():
                setattr(card, field, value)
            card.save()
        else:
            card = DigitalCard.objects.create(
                profile=profile,
                **serializer.validated_data,
            )
        return Response({'card': DigitalCardSerializer(card).data})


class GenerateQRView(APIView):
    permission_classes = [HasFeature('qr_vcard_export')]

    @extend_schema(tags=['app-digital'], summary='Generate QR code for digital card')
    def post(self, request):
        import qrcode

        profile = _get_profile(request.user)
        if not profile:
            return _NOT_FOUND

        url = request.data.get('url') or f'https://app.example.com/p/{profile.username}'
        img = qrcode.make(url)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        qr_b64 = base64.b64encode(buf.getvalue()).decode()
        data_url = f'data:image/png;base64,{qr_b64}'

        # Persist on the card if it exists
        DigitalCard.objects.filter(profile=profile).update(qr_code_url=data_url)

        return Response({'qr_code_url': data_url})


# ─── Landing ──────────────────────────────────────────────────────────────────

class LandingView(APIView):
    permission_classes = [HasFeature('landing_page')]

    @extend_schema(tags=['app-digital'], summary='Get own landing page')
    def get(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return _NOT_FOUND
        landing = LandingTemplate.objects.filter(profile=profile).first()
        if not landing:
            return _NOT_FOUND
        return Response({'landing': LandingTemplateSerializer(landing).data})

    @extend_schema(tags=['app-digital'], summary='Create or update landing page')
    def post(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return Response(
                {'error': {'code': 'profile_required', 'message': 'Create a profile first.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        landing = LandingTemplate.objects.filter(profile=profile).first()
        serializer = LandingTemplateSerializer(
            instance=landing,
            data=request.data,
            partial=landing is not None,
        )
        serializer.is_valid(raise_exception=True)
        if landing:
            for field, value in serializer.validated_data.items():
                setattr(landing, field, value)
            landing.save()
        else:
            landing = LandingTemplate.objects.create(
                profile=profile,
                **serializer.validated_data,
            )
        return Response({'landing': LandingTemplateSerializer(landing).data})


# ─── Portfolio ────────────────────────────────────────────────────────────────

class PortfolioListCreateView(APIView):
    permission_classes = [HasFeature('portfolio')]

    @extend_schema(tags=['app-digital'], summary='List portfolio items')
    def get(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return Response({'items': []})
        items = PortfolioItem.objects.filter(profile=profile)
        return Response({'items': PortfolioItemSerializer(items, many=True).data})

    @extend_schema(tags=['app-digital'], summary='Create portfolio item')
    def post(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return Response(
                {'error': {'code': 'profile_required', 'message': 'Create a profile first.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        current_count = PortfolioItem.objects.filter(profile=profile).count()
        check_plan_limit(request.user, 'portfolio_items', current_count)

        serializer = PortfolioItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = PortfolioItem.objects.create(
            profile=profile,
            **serializer.validated_data,
        )
        return Response(
            {'item': PortfolioItemSerializer(item).data},
            status=status.HTTP_201_CREATED,
        )


class PortfolioDetailView(APIView):
    permission_classes = [HasFeature('portfolio')]

    def _get_item(self, pk, user):
        try:
            return PortfolioItem.objects.select_related('profile').get(pk=pk)
        except PortfolioItem.DoesNotExist:
            return None

    @extend_schema(tags=['app-digital'], summary='Update portfolio item')
    def patch(self, request, pk):
        item = self._get_item(pk, request.user)
        if not item or item.profile.user_id != request.user.pk:
            return _NOT_FOUND

        # Max 3 featured items
        if request.data.get('is_featured') is True:
            featured_count = PortfolioItem.objects.filter(
                profile=item.profile,
                is_featured=True,
            ).exclude(pk=pk).count()
            if featured_count >= 3:
                return Response(
                    {'error': {'code': 'max_featured', 'message': 'Max 3 featured items allowed.'}},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        serializer = PortfolioItemSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(item, field, value)
        item.save()
        return Response({'item': PortfolioItemSerializer(item).data})

    @extend_schema(tags=['app-digital'], summary='Delete portfolio item')
    def delete(self, request, pk):
        item = self._get_item(pk, request.user)
        if not item or item.profile.user_id != request.user.pk:
            return _NOT_FOUND
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── CV ───────────────────────────────────────────────────────────────────────

class CVView(APIView):
    # CV basic is available on Free plan (uses digital_card gate)
    permission_classes = [HasFeature('digital_card')]

    @extend_schema(tags=['app-digital'], summary='Get own CV document')
    def get(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return _NOT_FOUND
        cv = CVDocument.objects.filter(profile=profile).first()
        if not cv:
            return _NOT_FOUND
        return Response({'cv': CVDocumentSerializer(cv).data})

    @extend_schema(tags=['app-digital'], summary='Create or update CV document')
    def post(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return Response(
                {'error': {'code': 'profile_required', 'message': 'Create a profile first.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        cv = CVDocument.objects.filter(profile=profile).first()
        serializer = CVDocumentSerializer(
            instance=cv,
            data=request.data,
            partial=cv is not None,
        )
        serializer.is_valid(raise_exception=True)
        if cv:
            for field, value in serializer.validated_data.items():
                setattr(cv, field, value)
            cv.save()
        else:
            cv = CVDocument.objects.create(
                profile=profile,
                **serializer.validated_data,
            )
        return Response({'cv': CVDocumentSerializer(cv).data})


class CVExportPDFView(APIView):
    permission_classes = [HasFeature('cv_pdf_export')]

    @extend_schema(tags=['app-digital'], summary='Export CV as PDF')
    def get(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return _NOT_FOUND
        cv = CVDocument.objects.filter(profile=profile).first()

        html = _render_cv_html(profile, cv)
        pdf_bytes = weasyprint.HTML(string=html).write_pdf()

        from django.http import HttpResponse
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="cv_{profile.username}.pdf"'
        return response


def _render_cv_html(profile, cv) -> str:
    """Render a minimal HTML CV for PDF export."""
    summary = cv.professional_summary if cv else ''
    skills = ', '.join(cv.skills) if cv and cv.skills else ''
    exp_html = ''
    if cv and cv.experience:
        items = ''.join(
            f'<li><strong>{e.get("position", "")}</strong> at {e.get("company", "")} '
            f'({e.get("start", "")} – {e.get("end", "Present")}): {e.get("description", "")}</li>'
            for e in cv.experience
        )
        exp_html = f'<h2>Experience</h2><ul>{items}</ul>'

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    body {{ font-family: sans-serif; margin: 40px; color: #111; }}
    h1 {{ color: #3B82F6; }}
    h2 {{ border-bottom: 1px solid #ddd; padding-bottom: 4px; }}
    ul {{ padding-left: 18px; }}
  </style>
</head>
<body>
  <h1>{profile.display_name}</h1>
  <p>{profile.title}</p>
  <p>{summary}</p>
  {exp_html}
  {'<h2>Skills</h2><p>' + skills + '</p>' if skills else ''}
</body>
</html>"""


# ─── Analytics ────────────────────────────────────────────────────────────────

class DigitalAnalyticsView(APIView):
    permission_classes = [HasFeature('digital_analytics')]

    @extend_schema(tags=['app-digital'], summary='Get basic analytics for a digital service')
    def get(self, request, service: str):
        profile = _get_profile(request.user)
        if not profile:
            return _NOT_FOUND

        data: dict = {'service': service, 'profile_username': profile.username}

        if service == 'portafolio':
            data['portfolio_items'] = PortfolioItem.objects.filter(profile=profile).count()
            data['featured_items'] = PortfolioItem.objects.filter(
                profile=profile, is_featured=True
            ).count()
        elif service == 'tarjeta':
            data['has_card'] = DigitalCard.objects.filter(profile=profile).exists()
        elif service == 'landing':
            data['has_landing'] = LandingTemplate.objects.filter(profile=profile).exists()
        elif service == 'cv':
            data['has_cv'] = CVDocument.objects.filter(profile=profile).exists()

        return Response({'analytics': data})


# ─── Custom Domain ────────────────────────────────────────────────────────────

class CustomDomainView(APIView):
    permission_classes = [HasFeature('custom_domain')]

    @extend_schema(tags=['app-digital'], summary='Get custom domain configuration')
    def get(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return _NOT_FOUND
        domain = CustomDomain.objects.filter(profile=profile).first()
        if not domain:
            return _NOT_FOUND
        return Response({'domain': CustomDomainSerializer(domain).data})

    @extend_schema(tags=['app-digital'], summary='Add custom domain')
    def post(self, request):
        profile = _get_profile(request.user)
        if not profile:
            return Response(
                {'error': {'code': 'profile_required', 'message': 'Create a profile first.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        domain_value = request.data.get('domain', '')
        if not domain_value:
            return Response(
                {'error': {'code': 'domain_required', 'message': 'domain is required.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = CustomDomain.objects.filter(profile=profile).first()
        if existing:
            existing.domain = domain_value
            existing.verification_status = 'pending'
            existing.save(update_fields=['domain', 'verification_status', 'updated_at'])
            return Response({'domain': CustomDomainSerializer(existing).data})

        token = secrets.token_hex(32)
        domain_obj = CustomDomain.objects.create(
            profile=profile,
            domain=domain_value,
            verification_token=token,
        )
        return Response(
            {'domain': CustomDomainSerializer(domain_obj).data},
            status=status.HTTP_201_CREATED,
        )


class CustomDomainVerifyView(APIView):
    permission_classes = [HasFeature('custom_domain')]

    @extend_schema(tags=['app-digital'], summary='Trigger domain DNS verification (stub)')
    def post(self, request):
        from django.utils import timezone

        profile = _get_profile(request.user)
        if not profile:
            return _NOT_FOUND
        domain = CustomDomain.objects.filter(profile=profile).first()
        if not domain:
            return _NOT_FOUND

        # Stub: in production this would perform a real DNS TXT record lookup
        domain.last_verification_attempt = timezone.now()
        domain.verification_status = 'pending'
        domain.save(update_fields=['last_verification_attempt', 'verification_status', 'updated_at'])

        return Response({
            'status': domain.verification_status,
            'message': 'Add TXT record to your DNS with the verification_token value.',
            'verification_token': domain.verification_token,
        })
