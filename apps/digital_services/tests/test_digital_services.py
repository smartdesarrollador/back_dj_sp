"""
Tests for PASO 18 — Digital Services module.

Groups:
  Group 1: PublicProfile + DigitalCard (5 tests)
  Group 2: Landing + Portfolio (5 tests)
  Group 3: CV + PDF + Analytics + Custom Domain (5 tests)
"""
import datetime
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.digital_services.models import (
    CVDocument,
    DigitalCard,
    LandingTemplate,
    PortfolioItem,
    PortfolioSettings,
    PublicProfile,
)
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

BASE_URL = '/api/v1/app/digital/'
PUBLIC_URL = '/api/v1/public/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_superuser(tenant, email):
    user = User.objects.create_user(
        email=email, name='Test User', password='x', tenant=tenant
    )
    user.is_superuser = True
    user.save(update_fields=['is_superuser'])
    return user


def _make_profile(user, username='jsmith', is_public=False):
    return PublicProfile.objects.create(
        user=user,
        username=username,
        display_name='John Smith',
        is_public=is_public,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Group 1: PublicProfile + DigitalCard
# ══════════════════════════════════════════════════════════════════════════════

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestPublicProfileAndCard(APITestCase):

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('digital-corp')
        self.user = _create_superuser(self.tenant, 'u@digital.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'digital-corp'}

    # ── Test 1 ───────────────────────────────────────────────────────────────

    def test_create_public_profile_success(self):
        """POST profile/ creates a new PublicProfile for the authenticated user."""
        data = {'username': 'jsmith', 'display_name': 'John Smith', 'title': 'Engineer'}
        response = self.client.post(f'{BASE_URL}profile/', data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()['profile']
        self.assertEqual(body['username'], 'jsmith')
        self.assertEqual(body['display_name'], 'John Smith')
        self.assertTrue(PublicProfile.objects.filter(user=self.user, username='jsmith').exists())

    # ── Test 2 ───────────────────────────────────────────────────────────────

    def test_profile_reserved_username_rejected(self):
        """POST profile/ with reserved username 'admin' returns 400."""
        data = {'username': 'admin', 'display_name': 'Admin User'}
        response = self.client.post(f'{BASE_URL}profile/', data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # ── Test 3 ───────────────────────────────────────────────────────────────

    def test_create_digital_card_success(self):
        """POST tarjeta/ creates a DigitalCard linked to the user's profile."""
        _make_profile(self.user)
        data = {
            'email': 'john@example.com',
            'phone': '+1-555-0100',
            'location': 'New York',
            'primary_color': '#FF5733',
        }
        response = self.client.post(f'{BASE_URL}tarjeta/', data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()['card']
        self.assertEqual(body['email'], 'john@example.com')
        self.assertEqual(body['primary_color'], '#FF5733')
        self.assertTrue(DigitalCard.objects.filter(profile__user=self.user).exists())

    # ── Test 4 ───────────────────────────────────────────────────────────────

    def test_generate_qr_requires_starter(self):
        """POST tarjeta/qr/ with a Free plan returns 402 (feature gate: qr_vcard_export)."""
        free_tenant = _create_tenant('free-digital', plan='free')
        free_user = _create_superuser(free_tenant, 'u@free-digital.com')
        _make_profile(free_user, username='freeuser')
        self.client.force_authenticate(user=free_user)
        response = self.client.post(
            f'{BASE_URL}tarjeta/qr/',
            {'url': 'https://example.com'},
            **{'HTTP_X_TENANT_SLUG': 'free-digital'},
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Test 5 ───────────────────────────────────────────────────────────────

    def test_public_profile_returns_404_when_not_public(self):
        """GET /public/profiles/<username>/ returns 404 if is_public=False."""
        _make_profile(self.user, username='privateuser', is_public=False)
        response = self.client.get(f'{PUBLIC_URL}profiles/privateuser/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


# ══════════════════════════════════════════════════════════════════════════════
# Group 2: Landing + Portfolio
# ══════════════════════════════════════════════════════════════════════════════

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestLandingAndPortfolio(APITestCase):

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('landing-corp')
        self.user = _create_superuser(self.tenant, 'u@landing.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'landing-corp'}

    # ── Test 6 ───────────────────────────────────────────────────────────────

    def test_landing_requires_starter(self):
        """POST landing/ with Free plan returns 402 (feature gate: landing_page)."""
        free_tenant = _create_tenant('free-landing', plan='free')
        free_user = _create_superuser(free_tenant, 'u@free-landing.com')
        _make_profile(free_user, username='freelander')
        self.client.force_authenticate(user=free_user)
        response = self.client.post(
            f'{BASE_URL}landing/',
            {'template_type': 'basic'},
            **{'HTTP_X_TENANT_SLUG': 'free-landing'},
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Test 7 ───────────────────────────────────────────────────────────────

    def test_create_landing_starter(self):
        """POST landing/ with Starter plan creates a LandingTemplate."""
        starter_tenant = _create_tenant('starter-landing', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'u@starter-landing.com')
        _make_profile(starter_user, username='starterlander')
        self.client.force_authenticate(user=starter_user)
        data = {'template_type': 'minimal', 'contact_email': 'hello@starter.com'}
        response = self.client.post(
            f'{BASE_URL}landing/',
            data,
            **{'HTTP_X_TENANT_SLUG': 'starter-landing'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()['landing']
        self.assertEqual(body['template_type'], 'minimal')
        self.assertEqual(body['style_preset'], 'modern')
        self.assertTrue(LandingTemplate.objects.filter(profile__user=starter_user).exists())

    # ── Test 7b ──────────────────────────────────────────────────────────────

    def test_create_landing_with_style_preset(self):
        """POST landing/ persists a custom style_preset."""
        starter_tenant = _create_tenant('style-landing', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'u@style-landing.com')
        _make_profile(starter_user, username='stylelander')
        self.client.force_authenticate(user=starter_user)
        data = {'template_type': 'basic', 'style_preset': 'soft'}
        response = self.client.post(
            f'{BASE_URL}landing/',
            data,
            **{'HTTP_X_TENANT_SLUG': 'style-landing'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()['landing']
        self.assertEqual(body['style_preset'], 'soft')

    # ── Test 7c ──────────────────────────────────────────────────────────────

    def test_create_landing_with_style_preset_bold(self):
        """POST landing/ persiste un style_preset de Fase 2 (editorial/bold)."""
        starter_tenant = _create_tenant('style-landing-f2', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'u@style-landing-f2.com')
        _make_profile(starter_user, username='stylelanderf2')
        self.client.force_authenticate(user=starter_user)
        data = {'template_type': 'basic', 'style_preset': 'bold'}
        response = self.client.post(
            f'{BASE_URL}landing/',
            data,
            **{'HTTP_X_TENANT_SLUG': 'style-landing-f2'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()['landing']
        self.assertEqual(body['style_preset'], 'bold')

    # ── Test 8 ───────────────────────────────────────────────────────────────

    def test_portfolio_requires_professional(self):
        """POST portafolio/ with Starter plan returns 402 (feature gate: portfolio)."""
        starter_tenant = _create_tenant('starter-port', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'u@starter-port.com')
        _make_profile(starter_user, username='starterport')
        self.client.force_authenticate(user=starter_user)
        response = self.client.post(
            f'{BASE_URL}portafolio/',
            {'title': 'My Project'},
            **{'HTTP_X_TENANT_SLUG': 'starter-port'},
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Test 9 ───────────────────────────────────────────────────────────────

    def test_create_portfolio_item_professional(self):
        """POST portafolio/ with Professional plan creates a PortfolioItem (201)."""
        _make_profile(self.user, username='prouser')
        data = {
            'title': 'E-commerce Platform',
            'slug': 'ecommerce-platform',
            'description_short': 'Full-stack e-commerce solution',
            'cover_image_url': 'https://example.com/cover.jpg',
            'project_date': '2024-06-01',
        }
        response = self.client.post(f'{BASE_URL}portafolio/', data, **self.slug)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()['item']
        self.assertEqual(body['title'], 'E-commerce Platform')
        self.assertTrue(PortfolioItem.objects.filter(profile__user=self.user).exists())

    # ── Test 10 ──────────────────────────────────────────────────────────────

    def test_public_portfolio_lists_items(self):
        """GET /public/portafolio/<username>/ returns 200 with items for a public profile."""
        profile = _make_profile(self.user, username='portuser', is_public=True)
        PortfolioItem.objects.create(
            profile=profile,
            title='Demo Project',
            slug='demo-project',
            description_short='Short description',
            cover_image_url='https://example.com/img.jpg',
            project_date=datetime.date(2024, 1, 15),
        )
        response = self.client.get(f'{PUBLIC_URL}portafolio/portuser/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIn('items', body)
        self.assertEqual(len(body['items']), 1)
        self.assertEqual(body['items'][0]['title'], 'Demo Project')

    # ── Test 10b ─────────────────────────────────────────────────────────────

    def test_portfolio_settings_hero_content_roundtrip(self):
        """POST portfolio-settings/ persists hero_content (badge/CTA/social toggle)."""
        _make_profile(self.user, username='herouser')
        data = {
            'hero_content': {
                'badge': 'Disponible para proyectos',
                'ctaText': 'Contáctame',
                'ctaUrl': 'mailto:hola@example.com',
                'showSocialLinks': True,
            },
        }
        response = self.client.post(
            f'{BASE_URL}portfolio-settings/', data, format='json', **self.slug,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['hero_content']['badge'], 'Disponible para proyectos')
        self.assertTrue(body['hero_content']['showSocialLinks'])

    # ── Test 10c ─────────────────────────────────────────────────────────────

    def test_public_portfolio_includes_digital_card_and_hero_content(self):
        """GET /public/portafolio/<username>/ includes digital_card + hero_content."""
        profile = _make_profile(self.user, username='herouser2', is_public=True)
        DigitalCard.objects.create(profile=profile, linkedin_url='https://linkedin.com/in/x')
        PortfolioSettings.objects.create(
            profile=profile,
            hero_content={'badge': 'Hola', 'showSocialLinks': True},
        )
        response = self.client.get(f'{PUBLIC_URL}portafolio/herouser2/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertIn('digital_card', body)
        self.assertEqual(body['digital_card']['linkedin_url'], 'https://linkedin.com/in/x')
        self.assertEqual(body['hero_content']['badge'], 'Hola')

    # ── Test 10d ─────────────────────────────────────────────────────────────

    def test_portfolio_settings_contact_content_roundtrip(self):
        """POST portfolio-settings/ persists contact_content (title/description) and
        the public endpoint returns it alongside digital_card contact fields."""
        profile = _make_profile(self.user, username='contactuser', is_public=True)
        DigitalCard.objects.create(profile=profile, email='hola@example.com', phone='+1-555-0100')
        data = {
            'contact_content': {
                'title': '¿Trabajamos juntos?',
                'description': 'Envíame un mensaje y conversemos sobre tu proyecto.',
            },
        }
        response = self.client.post(
            f'{BASE_URL}portfolio-settings/', data, format='json', **self.slug,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['contact_content']['title'], '¿Trabajamos juntos?')

        public_response = self.client.get(f'{PUBLIC_URL}portafolio/contactuser/')
        self.assertEqual(public_response.status_code, status.HTTP_200_OK)
        public_body = public_response.json()
        self.assertEqual(public_body['contact_content']['title'], '¿Trabajamos juntos?')
        self.assertEqual(public_body['digital_card']['email'], 'hola@example.com')

    # ── Test 10e ─────────────────────────────────────────────────────────────

    def test_portfolio_settings_about_content_roundtrip(self):
        """POST portfolio-settings/ persists about_content (title/text/highlights)."""
        _make_profile(self.user, username='aboutuser', is_public=True)
        data = {
            'about_content': {
                'title': 'Sobre mí',
                'text': 'Cuento largo sobre mi trayectoria profesional.',
                'highlights': ['+10 años de experiencia', '50+ proyectos entregados'],
            },
        }
        response = self.client.post(
            f'{BASE_URL}portfolio-settings/', data, format='json', **self.slug,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['about_content']['title'], 'Sobre mí')
        self.assertEqual(len(body['about_content']['highlights']), 2)

        public_response = self.client.get(f'{PUBLIC_URL}portafolio/aboutuser/')
        self.assertEqual(public_response.status_code, status.HTTP_200_OK)
        self.assertEqual(public_response.json()['about_content']['title'], 'Sobre mí')

    # ── Test 10f ─────────────────────────────────────────────────────────────

    def test_portfolio_settings_skills_content_roundtrip(self):
        """POST portfolio-settings/ persists skills_content (title/showSkills)."""
        _make_profile(self.user, username='skillsuser')
        data = {'skills_content': {'title': 'Stack Tecnológico', 'showSkills': True}}
        response = self.client.post(
            f'{BASE_URL}portfolio-settings/', data, format='json', **self.slug,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['skills_content']['title'], 'Stack Tecnológico')
        self.assertTrue(body['skills_content']['showSkills'])

    # ── Test 10g ─────────────────────────────────────────────────────────────

    def test_public_portfolio_aggregates_skills_from_published_projects(self):
        """GET /public/portafolio/<username>/ returns unique skills ordered by frequency,
        derived from technologies of published projects only."""
        profile = _make_profile(self.user, username='skillsagg', is_public=True)
        PortfolioItem.objects.create(
            profile=profile, title='Proyecto 1', slug='proyecto-1',
            description_short='Desc', project_date=datetime.date(2024, 1, 15),
            technologies=['React', 'Django', 'React'],
        )
        PortfolioItem.objects.create(
            profile=profile, title='Proyecto 2', slug='proyecto-2',
            description_short='Desc', project_date=datetime.date(2024, 2, 15),
            technologies=['React', 'Stripe'],
        )
        PortfolioItem.objects.create(
            profile=profile, title='Proyecto no publicado', slug='no-publicado',
            description_short='Desc', project_date=datetime.date(2024, 3, 15),
            technologies=['Vue'], is_published=False,
        )
        response = self.client.get(f'{PUBLIC_URL}portafolio/skillsagg/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['skills'][0], 'React')
        self.assertEqual(set(body['skills']), {'React', 'Django', 'Stripe'})
        self.assertNotIn('Vue', body['skills'])

    # ── Test 10h ─────────────────────────────────────────────────────────────

    def test_portfolio_settings_services_content_roundtrip(self):
        """POST portfolio-settings/ persists services_content (title + items[])."""
        _make_profile(self.user, username='servicesuser', is_public=True)
        data = {
            'services_content': {
                'title': 'Servicios',
                'items': [
                    {'icon': 'code', 'title': 'Desarrollo web', 'description': 'Sitios a medida', 'link': ''},
                ],
            },
        }
        response = self.client.post(
            f'{BASE_URL}portfolio-settings/', data, format='json', **self.slug,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['services_content']['title'], 'Servicios')
        self.assertEqual(len(body['services_content']['items']), 1)
        self.assertEqual(body['services_content']['items'][0]['title'], 'Desarrollo web')

        public_response = self.client.get(f'{PUBLIC_URL}portafolio/servicesuser/')
        self.assertEqual(public_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(public_response.json()['services_content']['items']), 1)

    # ── Test 10i ─────────────────────────────────────────────────────────────

    def test_portfolio_settings_testimonials_content_roundtrip(self):
        """POST portfolio-settings/ persists testimonials_content (title + items[])."""
        _make_profile(self.user, username='testimonialsuser', is_public=True)
        data = {
            'testimonials_content': {
                'title': 'Lo que dicen mis clientes',
                'items': [
                    {'name': 'Ana Pérez', 'role': 'CEO', 'company': 'Acme', 'text': 'Excelente trabajo.', 'rating': 5},
                ],
            },
        }
        response = self.client.post(
            f'{BASE_URL}portfolio-settings/', data, format='json', **self.slug,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(len(body['testimonials_content']['items']), 1)
        self.assertEqual(body['testimonials_content']['items'][0]['name'], 'Ana Pérez')

        public_response = self.client.get(f'{PUBLIC_URL}portafolio/testimonialsuser/')
        self.assertEqual(public_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(public_response.json()['testimonials_content']['items']), 1)

    # ── Test 10j ─────────────────────────────────────────────────────────────

    def test_portfolio_settings_style_preset_roundtrip(self):
        """POST portfolio-settings/ persiste style_preset y el endpoint público lo expone."""
        _make_profile(self.user, username='styleportuser', is_public=True)
        data = {'style_preset': 'soft'}
        response = self.client.post(
            f'{BASE_URL}portfolio-settings/', data, format='json', **self.slug,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['style_preset'], 'soft')

        public_response = self.client.get(f'{PUBLIC_URL}portafolio/styleportuser/')
        self.assertEqual(public_response.status_code, status.HTTP_200_OK)
        self.assertEqual(public_response.json()['style_preset'], 'soft')

    # ── Test 10k ─────────────────────────────────────────────────────────────

    def test_public_portfolio_style_preset_defaults_to_modern(self):
        """GET público retorna 'modern' cuando el usuario nunca configuró un preset."""
        profile = _make_profile(self.user, username='defaultstyleuser', is_public=True)
        PortfolioSettings.objects.create(profile=profile)
        response = self.client.get(f'{PUBLIC_URL}portafolio/defaultstyleuser/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['style_preset'], 'modern')

    # ── Test 10l ─────────────────────────────────────────────────────────────

    def test_portfolio_settings_style_preset_accepts_fase2_presets(self):
        """POST portfolio-settings/ acepta los presets de Fase 2 (editorial/bold)."""
        _make_profile(self.user, username='fase2styleuser', is_public=True)
        data = {'style_preset': 'editorial'}
        response = self.client.post(
            f'{BASE_URL}portfolio-settings/', data, format='json', **self.slug,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['style_preset'], 'editorial')

        public_response = self.client.get(f'{PUBLIC_URL}portafolio/fase2styleuser/')
        self.assertEqual(public_response.status_code, status.HTTP_200_OK)
        self.assertEqual(public_response.json()['style_preset'], 'editorial')


# ══════════════════════════════════════════════════════════════════════════════
# Group 3: CV + PDF + Analytics + Custom Domain
# ══════════════════════════════════════════════════════════════════════════════

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestCVAndDomain(APITestCase):

    def setUp(self):
        cache.clear()
        self.tenant = _create_tenant('cv-corp')
        self.user = _create_superuser(self.tenant, 'u@cv.com')
        self.client.force_authenticate(user=self.user)
        self.slug = {'HTTP_X_TENANT_SLUG': 'cv-corp'}

    # ── Test 11 ──────────────────────────────────────────────────────────────

    def test_cv_available_free_plan(self):
        """GET cv/ with Free plan returns 200 (CV uses digital_card gate, available on Free)."""
        free_tenant = _create_tenant('free-cv', plan='free')
        free_user = _create_superuser(free_tenant, 'u@free-cv.com')
        profile = _make_profile(free_user, username='freecv')
        CVDocument.objects.create(profile=profile, professional_summary='I am a developer.')
        self.client.force_authenticate(user=free_user)
        response = self.client.get(
            f'{BASE_URL}cv/',
            **{'HTTP_X_TENANT_SLUG': 'free-cv'},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()['cv']
        self.assertEqual(body['professional_summary'], 'I am a developer.')

    # ── Test 11b ─────────────────────────────────────────────────────────────

    def test_cv_get_includes_profile_for_public_url(self):
        """GET cv/ includes 'profile' (username) so the dashboard can build the public CV link."""
        profile = _make_profile(self.user, username='cvprofileuser')
        CVDocument.objects.create(profile=profile, professional_summary='Dev.')
        response = self.client.get(f'{BASE_URL}cv/', **self.slug)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.json()['profile']['username'], 'cvprofileuser')

    # ── Test 12 ──────────────────────────────────────────────────────────────

    def test_cv_pdf_export_requires_starter(self):
        """GET cv/export/ with Free plan returns 402 (feature gate: cv_pdf_export)."""
        free_tenant = _create_tenant('free-cvpdf', plan='free')
        free_user = _create_superuser(free_tenant, 'u@free-cvpdf.com')
        _make_profile(free_user, username='freepdf')
        self.client.force_authenticate(user=free_user)
        response = self.client.get(
            f'{BASE_URL}cv/export/',
            **{'HTTP_X_TENANT_SLUG': 'free-cvpdf'},
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Test 13 ──────────────────────────────────────────────────────────────

    def test_cv_pdf_export_returns_pdf(self):
        """GET cv/export/ with Starter plan returns 200 with content_type=application/pdf."""
        starter_tenant = _create_tenant('starter-cvpdf', plan='starter')
        starter_user = _create_superuser(starter_tenant, 'u@starter-cvpdf.com')
        profile = _make_profile(starter_user, username='starterpdf')
        CVDocument.objects.create(profile=profile, professional_summary='Senior dev.')
        self.client.force_authenticate(user=starter_user)

        mock_pdf = b'%PDF-1.4 fake pdf content'
        mock_html_instance = MagicMock()
        mock_html_instance.write_pdf.return_value = mock_pdf

        with patch('apps.digital_services.views.weasyprint') as mock_wp:
            mock_wp.HTML.return_value = mock_html_instance
            response = self.client.get(
                f'{BASE_URL}cv/export/',
                **{'HTTP_X_TENANT_SLUG': 'starter-cvpdf'},
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response['Content-Type'], 'application/pdf')

    # ── Test 14 ──────────────────────────────────────────────────────────────

    def test_analytics_requires_starter(self):
        """GET analytics/tarjeta/ with Free plan returns 402 (feature gate: digital_analytics)."""
        free_tenant = _create_tenant('free-analytics', plan='free')
        free_user = _create_superuser(free_tenant, 'u@free-analytics.com')
        _make_profile(free_user, username='freeanalytics')
        self.client.force_authenticate(user=free_user)
        response = self.client.get(
            f'{BASE_URL}analytics/tarjeta/',
            **{'HTTP_X_TENANT_SLUG': 'free-analytics'},
        )
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)

    # ── Test 15 ──────────────────────────────────────────────────────────────

    def test_custom_domain_requires_enterprise(self):
        """POST custom-domain/ with Professional plan returns 402 (feature gate: custom_domain)."""
        _make_profile(self.user, username='prodomain')
        response = self.client.post(
            f'{BASE_URL}custom-domain/',
            {'domain': 'mysite.com'},
            **self.slug,
        )
        # self.tenant has plan='professional', which does NOT have custom_domain
        self.assertEqual(response.status_code, status.HTTP_402_PAYMENT_REQUIRED)
