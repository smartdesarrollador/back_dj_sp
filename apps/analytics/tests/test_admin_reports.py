"""
Tests for AdminSummaryView — cross-tenant business metrics (MRR/ARR/churn).

Covers: IsStaffUser is load-bearing (not redundant with the RBAC permission),
own-tenant exclusion, MRR computed from Invoice (not stale Subscription.status),
churn detection, trial-conversion counting, period param handling.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.auth_app.models import SSOToken
from apps.digital_services.models import PageEvent, PublicProfile
from apps.licenses.models import DesktopAppLicense, _generate_license_key
from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.services.models import Service, TenantService
from apps.subscriptions.models import Invoice
from apps.tenants.models import Tenant

User = get_user_model()

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
_LOCMEM_CACHE = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}}

SUMMARY_URL = '/api/v1/admin/reports/summary/'
SERVICE_ADOPTION_URL = '/api/v1/admin/reports/service-adoption/'
VISTA_TRAFFIC_URL = '/api/v1/admin/reports/vista-traffic/'
DESKTOP_LICENSES_URL = '/api/v1/admin/reports/desktop-licenses/'


def _create_tenant(slug, plan='professional'):
    return Tenant.objects.create(name=slug.capitalize(), slug=slug, subdomain=slug, plan=plan)


def _create_staff(tenant, email):
    return User.objects.create_user(
        email=email, name='Staff', password='pass123', tenant=tenant, is_staff=True,
    )


def _grant_permission(user, codename):
    permission, _ = Permission.objects.get_or_create(
        codename=codename,
        defaults={'name': codename, 'resource': codename.split('.')[0], 'action': codename.split('.')[1]},
    )
    role = Role.objects.create(tenant=user.tenant, name=f'role-{codename}')
    RolePermission.objects.create(role=role, permission=permission)
    UserRole.objects.create(user=user, role=role)


def _create_service(slug, min_plan='free', is_active=True):
    return Service.objects.create(
        slug=slug, name=slug.title(), icon='Icon',
        url_template=f'https://{{subdomain}}.{slug}.app',
        min_plan=min_plan, is_active=is_active,
    )


def _create_public_profile(tenant, username):
    user = User.objects.create_user(
        email=f'{username}@{tenant.slug}.com', name=username, password='x', tenant=tenant,
    )
    return PublicProfile.objects.create(user=user, username=username, display_name=username)


def _create_page_event(profile, service, event_type, referrer='', session_hash='', created_at=None):
    """
    session_hash defaults to '' — pass explicit distinct values ('s1', 's2', ...)
    in tests that assert unique_views, since two events both left at '' collapse
    into a single unique visitor.
    """
    event = PageEvent.objects.create(
        profile=profile, service=service, event_type=event_type,
        referrer=referrer, session_hash=session_hash,
    )
    if created_at is not None:
        # auto_now_add bypass — same trick used for backdating secrets in
        # apps/analytics/tests/test_reports.py's DevOps tests.
        PageEvent.objects.filter(pk=event.pk).update(created_at=created_at)
    return event


def _create_license(user, hardware_id='', activated_at=None, sent_at=None, is_active=True):
    return DesktopAppLicense.objects.create(
        user=user,
        license_key=_generate_license_key(),
        hardware_id=hardware_id,
        activated_at=activated_at,
        sent_at=sent_at,
        is_active=is_active,
    )


def _paid_invoice(tenant, amount_cents, period_start, period_end, invoice_date=None):
    return Invoice.objects.create(
        tenant=tenant,
        stripe_invoice_id=f'yape_{tenant.slug}_{period_start.isoformat()}',
        amount_cents=amount_cents,
        currency='usd',
        status='paid',
        period_start=period_start,
        period_end=period_end,
        invoice_date=invoice_date or period_start,
        paid_at=period_start,
    )


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestAdminSummaryViewStaffOnly(APITestCase):
    def setUp(self):
        cache.clear()
        self.own_tenant = _create_tenant('own-corp')
        self.headers = {'HTTP_X_TENANT_SLUG': 'own-corp'}

    def test_non_staff_user_with_rbac_permission_is_blocked(self):
        owner = User.objects.create_user(
            email='owner@own-corp.com', name='Owner', password='pass123', tenant=self.own_tenant,
        )
        _grant_permission(owner, 'customers.analytics')
        self.client.force_authenticate(user=owner)

        response = self.client.get(SUMMARY_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_without_rbac_permission_is_blocked(self):
        staff = _create_staff(self.own_tenant, 'staff@own-corp.com')
        self.client.force_authenticate(user=staff)

        response = self.client.get(SUMMARY_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_with_rbac_permission_succeeds(self):
        staff = _create_staff(self.own_tenant, 'staff2@own-corp.com')
        _grant_permission(staff, 'customers.analytics')
        self.client.force_authenticate(user=staff)

        response = self.client.get(SUMMARY_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestAdminSummaryMetrics(APITestCase):
    def setUp(self):
        cache.clear()
        self.own_tenant = _create_tenant('own-corp')
        self.staff = _create_staff(self.own_tenant, 'staff@own-corp.com')
        _grant_permission(self.staff, 'customers.analytics')
        self.client.force_authenticate(user=self.staff)
        self.headers = {'HTTP_X_TENANT_SLUG': 'own-corp'}
        self.now = timezone.now()

    def test_own_tenant_excluded_from_all_metrics(self):
        User.objects.create_user(
            email='ownuser@own-corp.com', name='Own User', password='x', tenant=self.own_tenant,
        )
        _paid_invoice(
            self.own_tenant, 2900,
            self.now - timedelta(days=1), self.now + timedelta(days=29),
        )

        response = self.client.get(SUMMARY_URL, **self.headers)
        body = response.json()
        # own_tenant has 1 user (+ the staff member) and a covering invoice —
        # if it weren't excluded, mrr/total_users would be nonzero.
        self.assertEqual(body['total_users'], 0)
        self.assertEqual(body['mrr'], 0.0)

    def test_mrr_only_counts_tenants_with_current_invoice_coverage(self):
        tenant_a = _create_tenant('tenant-a')
        tenant_b = _create_tenant('tenant-b')
        tenant_c = _create_tenant('tenant-c')

        # A: paid, period covers now → counts
        _paid_invoice(tenant_a, 2900, self.now - timedelta(days=1), self.now + timedelta(days=29))

        # B: paid invoice long lapsed, but Subscription.status is still 'active'
        # (no expiry task exists) — must NOT count toward MRR.
        _paid_invoice(tenant_b, 4900, self.now - timedelta(days=90), self.now - timedelta(days=60))
        tenant_b.subscription.status = 'active'
        tenant_b.subscription.save(update_fields=['status'])

        # C: no invoices at all → excluded
        # (nothing to create)

        response = self.client.get(SUMMARY_URL, **self.headers)
        body = response.json()
        self.assertEqual(body['mrr'], 29.0)
        self.assertEqual(body['arr'], 348.0)

    def test_churn_rate_detects_lapsed_coverage(self):
        period_days = 30
        cutoff = self.now - timedelta(days=period_days)

        # Covered at cutoff AND now → not churned
        renewing = _create_tenant('renewing-corp')
        _paid_invoice(renewing, 2900, cutoff - timedelta(days=5), self.now + timedelta(days=5))

        # Covered at cutoff but lapsed by now → churned
        churned = _create_tenant('churned-corp')
        _paid_invoice(churned, 2900, cutoff - timedelta(days=5), cutoff + timedelta(days=5))

        response = self.client.get(f'{SUMMARY_URL}?period={period_days}', **self.headers)
        body = response.json()
        # base set = {renewing, churned} = 2; lapsed = {churned} = 1 → 50%
        self.assertEqual(body['churn_rate'], 50.0)

    def test_trial_conversions_counts_first_payment_not_renewals(self):
        period_days = 30
        window_start = self.now - timedelta(days=period_days)

        # First-ever payment inside the window → counts
        converting = _create_tenant('converting-corp')
        _paid_invoice(
            converting, 2900,
            self.now - timedelta(days=5), self.now + timedelta(days=25),
            invoice_date=self.now - timedelta(days=5),
        )

        # First payment was long ago; this invoice inside the window is a
        # RENEWAL, not a conversion → must not count.
        renewing = _create_tenant('renewing-old-corp')
        _paid_invoice(
            renewing, 2900,
            self.now - timedelta(days=100), self.now - timedelta(days=70),
            invoice_date=self.now - timedelta(days=100),
        )
        _paid_invoice(
            renewing, 2900,
            self.now - timedelta(days=3), self.now + timedelta(days=27),
            invoice_date=self.now - timedelta(days=3),
        )

        response = self.client.get(f'{SUMMARY_URL}?period={period_days}', **self.headers)
        body = response.json()
        self.assertEqual(body['trial_conversions'], 1)

    def test_period_param_defaults_and_caps(self):
        response = self.client.get(f'{SUMMARY_URL}?period=abc', **self.headers)
        self.assertEqual(response.json()['period_days'], 30)

        response = self.client.get(f'{SUMMARY_URL}?period=9999', **self.headers)
        self.assertEqual(response.json()['period_days'], 365)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestServiceAdoptionViewStaffOnly(APITestCase):
    def setUp(self):
        cache.clear()
        self.own_tenant = _create_tenant('own-corp')
        self.headers = {'HTTP_X_TENANT_SLUG': 'own-corp'}

    def test_non_staff_user_with_rbac_permission_is_blocked(self):
        owner = User.objects.create_user(
            email='owner@own-corp.com', name='Owner', password='pass123', tenant=self.own_tenant,
        )
        _grant_permission(owner, 'customers.analytics')
        self.client.force_authenticate(user=owner)

        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_without_rbac_permission_is_blocked(self):
        staff = _create_staff(self.own_tenant, 'staff@own-corp.com')
        self.client.force_authenticate(user=staff)

        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_with_rbac_permission_succeeds(self):
        staff = _create_staff(self.own_tenant, 'staff2@own-corp.com')
        _grant_permission(staff, 'customers.analytics')
        self.client.force_authenticate(user=staff)

        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestServiceAdoptionMetrics(APITestCase):
    def setUp(self):
        cache.clear()
        self.own_tenant = _create_tenant('own-corp')
        self.staff = _create_staff(self.own_tenant, 'staff@own-corp.com')
        _grant_permission(self.staff, 'customers.analytics')
        self.client.force_authenticate(user=self.staff)
        self.headers = {'HTTP_X_TENANT_SLUG': 'own-corp'}

    def _by_slug(self, response):
        return {s['service']: s for s in response.json()['services']}

    def test_auto_provisioning_counts_as_acquired(self):
        # provision_free_services fires per-tenant-creation for min_plan='free'
        # Services that already exist at that moment.
        _create_service('workspace')
        _create_service('vista')
        _create_service('desktop')
        _create_tenant('tenant-a')
        _create_tenant('tenant-b')

        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        by_slug = self._by_slug(response)
        self.assertEqual(by_slug['workspace']['acquired'], 2)
        self.assertEqual(by_slug['vista']['acquired'], 2)
        self.assertEqual(by_slug['desktop']['acquired'], 2)

    def test_suspended_tenant_service_excluded_from_acquired(self):
        _create_service('workspace')
        tenant_a = _create_tenant('tenant-a')
        ts = TenantService.objects.get(tenant=tenant_a, service__slug='workspace')
        ts.status = 'suspended'
        ts.save(update_fields=['status'])

        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        by_slug = self._by_slug(response)
        self.assertEqual(by_slug['workspace']['acquired'], 0)

    def test_sso_activation_ignores_unused_tokens_and_dedupes_per_tenant(self):
        _create_service('workspace')
        tenant_a = _create_tenant('tenant-a')
        user_a = User.objects.create_user(
            email='a@tenant-a.com', name='A', password='x', tenant=tenant_a,
        )

        SSOToken.objects.create(
            user=user_a, tenant=tenant_a, service='workspace',
            token='tok-unused-1', expires_at=timezone.now() + timedelta(minutes=1),
        )
        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        self.assertEqual(self._by_slug(response)['workspace']['activated'], 0)

        cache.clear()
        SSOToken.objects.create(
            user=user_a, tenant=tenant_a, service='workspace', token='tok-used-1',
            used_at=timezone.now(), expires_at=timezone.now() + timedelta(minutes=1),
        )
        SSOToken.objects.create(
            user=user_a, tenant=tenant_a, service='workspace', token='tok-used-2',
            used_at=timezone.now(), expires_at=timezone.now() + timedelta(minutes=1),
        )
        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        by_slug = self._by_slug(response)
        self.assertEqual(by_slug['workspace']['activated'], 1)
        self.assertEqual(by_slug['workspace']['acquired'], 1)
        self.assertEqual(by_slug['workspace']['activation_rate'], 100.0)

    def test_desktop_ignores_sso_signal_and_pending_license(self):
        _create_service('desktop')
        tenant_a = _create_tenant('tenant-a')
        user_a = User.objects.create_user(
            email='a@tenant-a.com', name='A', password='x', tenant=tenant_a,
        )

        # An SSOToken for 'desktop' must never be read — Desktop never goes
        # through the SSO flow in the real app.
        SSOToken.objects.create(
            user=user_a, tenant=tenant_a, service='desktop', token='tok-desktop-used',
            used_at=timezone.now(), expires_at=timezone.now() + timedelta(minutes=1),
        )
        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        self.assertEqual(self._by_slug(response)['desktop']['activated'], 0)

        # Pending license: hardware_id set, activated_at not yet -> not activated.
        cache.clear()
        DesktopAppLicense.objects.create(
            user=user_a, license_key=_generate_license_key(), hardware_id='HW-1',
        )
        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        self.assertEqual(self._by_slug(response)['desktop']['activated'], 0)

    def test_desktop_activated_license_counts_even_if_later_revoked(self):
        _create_service('desktop')
        tenant_a = _create_tenant('tenant-a')
        user_a = User.objects.create_user(
            email='a@tenant-a.com', name='A', password='x', tenant=tenant_a,
        )
        user_b = User.objects.create_user(
            email='b@tenant-a.com', name='B', password='x', tenant=tenant_a,
        )

        # Revoked (is_active=False) but was activated at some point -> still counts
        # ("ever activated" reading, documented as intentional).
        DesktopAppLicense.objects.create(
            user=user_a, license_key=_generate_license_key(), hardware_id='HW-1',
            activated_at=timezone.now(), is_active=False,
        )
        # Second activated user in the SAME tenant -> must not double-count.
        DesktopAppLicense.objects.create(
            user=user_b, license_key=_generate_license_key(), hardware_id='HW-2',
            activated_at=timezone.now(),
        )

        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        self.assertEqual(self._by_slug(response)['desktop']['activated'], 1)

    def test_zero_acquired_gives_zero_rate_no_crash(self):
        _create_tenant('tenant-a')
        _create_tenant('tenant-b')
        # Created AFTER the tenants above -> provisioning signal never fired for them.
        _create_service('workspace')

        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_slug = self._by_slug(response)
        self.assertEqual(by_slug['workspace']['acquired'], 0)
        self.assertEqual(by_slug['workspace']['activated'], 0)
        self.assertEqual(by_slug['workspace']['activation_rate'], 0.0)

    def test_own_tenant_excluded(self):
        service = _create_service('workspace')
        TenantService.objects.get_or_create(
            tenant=self.own_tenant, service=service, defaults={'status': 'active'},
        )
        SSOToken.objects.create(
            user=self.staff, tenant=self.own_tenant, service='workspace', token='tok-own',
            used_at=timezone.now(), expires_at=timezone.now() + timedelta(minutes=1),
        )
        other = _create_tenant('tenant-a')
        TenantService.objects.get_or_create(
            tenant=other, service=service, defaults={'status': 'active'},
        )

        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        by_slug = self._by_slug(response)
        self.assertEqual(by_slug['workspace']['acquired'], 1)
        self.assertEqual(by_slug['workspace']['activated'], 0)

    def test_inactive_service_excluded_from_response(self):
        _create_service('workspace', is_active=False)
        _create_tenant('tenant-a')

        response = self.client.get(SERVICE_ADOPTION_URL, **self.headers)
        slugs = {s['service'] for s in response.json()['services']}
        self.assertNotIn('workspace', slugs)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestVistaTrafficViewStaffOnly(APITestCase):
    def setUp(self):
        cache.clear()
        self.own_tenant = _create_tenant('own-corp')
        self.headers = {'HTTP_X_TENANT_SLUG': 'own-corp'}

    def test_non_staff_user_with_rbac_permission_is_blocked(self):
        owner = User.objects.create_user(
            email='owner@own-corp.com', name='Owner', password='pass123', tenant=self.own_tenant,
        )
        _grant_permission(owner, 'customers.analytics')
        self.client.force_authenticate(user=owner)

        response = self.client.get(VISTA_TRAFFIC_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_without_rbac_permission_is_blocked(self):
        staff = _create_staff(self.own_tenant, 'staff@own-corp.com')
        self.client.force_authenticate(user=staff)

        response = self.client.get(VISTA_TRAFFIC_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_with_rbac_permission_succeeds(self):
        staff = _create_staff(self.own_tenant, 'staff2@own-corp.com')
        _grant_permission(staff, 'customers.analytics')
        self.client.force_authenticate(user=staff)

        response = self.client.get(VISTA_TRAFFIC_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestVistaTrafficMetrics(APITestCase):
    def setUp(self):
        cache.clear()
        self.own_tenant = _create_tenant('own-corp')
        self.staff = _create_staff(self.own_tenant, 'staff@own-corp.com')
        _grant_permission(self.staff, 'customers.analytics')
        self.client.force_authenticate(user=self.staff)
        self.headers = {'HTTP_X_TENANT_SLUG': 'own-corp'}
        self.now = timezone.now()

    def _by_service(self, response):
        return {s['service']: s for s in response.json()['services']}

    def test_views_unique_views_shares_counted_per_service(self):
        tenant_a = _create_tenant('tenant-a')
        profile = _create_public_profile(tenant_a, 'creator-a')

        _create_page_event(profile, 'tarjeta', PageEvent.EVENT_VIEW, session_hash='s1')
        _create_page_event(profile, 'tarjeta', PageEvent.EVENT_VIEW, session_hash='s2')
        _create_page_event(profile, 'tarjeta', PageEvent.EVENT_VIEW, session_hash='s2')
        _create_page_event(profile, 'tarjeta', PageEvent.EVENT_SHARE)

        response = self.client.get(VISTA_TRAFFIC_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        by_service = self._by_service(response)

        self.assertEqual(by_service['tarjeta']['views'], 3)
        self.assertEqual(by_service['tarjeta']['unique_views'], 2)
        self.assertEqual(by_service['tarjeta']['shares'], 1)
        for slug in ('landing', 'portafolio', 'cv'):
            self.assertEqual(by_service[slug]['views'], 0)
            self.assertEqual(by_service[slug]['unique_views'], 0)
            self.assertEqual(by_service[slug]['shares'], 0)

    def test_own_tenant_excluded(self):
        own_profile = _create_public_profile(self.own_tenant, 'own-creator')
        _create_page_event(own_profile, 'cv', PageEvent.EVENT_VIEW, session_hash='s1', referrer='own.com')
        _create_page_event(own_profile, 'cv', PageEvent.EVENT_SHARE)

        response = self.client.get(VISTA_TRAFFIC_URL, **self.headers)
        by_service = self._by_service(response)
        self.assertEqual(by_service['cv']['views'], 0)
        self.assertEqual(by_service['cv']['shares'], 0)
        self.assertEqual(response.json()['referrers'], [])

    def test_period_window_excludes_old_events(self):
        period_days = 30
        tenant_a = _create_tenant('tenant-a')
        profile = _create_public_profile(tenant_a, 'creator-a')

        _create_page_event(profile, 'landing', PageEvent.EVENT_VIEW, session_hash='s1')
        _create_page_event(
            profile, 'landing', PageEvent.EVENT_VIEW, session_hash='s2',
            created_at=self.now - timedelta(days=period_days + 5),
        )

        response = self.client.get(f'{VISTA_TRAFFIC_URL}?period={period_days}', **self.headers)
        by_service = self._by_service(response)
        self.assertEqual(by_service['landing']['views'], 1)

    def test_referrers_ordered_by_count_and_excludes_blank(self):
        tenant_a = _create_tenant('tenant-a')
        profile = _create_public_profile(tenant_a, 'creator-a')

        for i in range(3):
            _create_page_event(
                profile, 'portafolio', PageEvent.EVENT_VIEW,
                session_hash=f'google-{i}', referrer='google.com',
            )
        for i in range(2):
            _create_page_event(
                profile, 'portafolio', PageEvent.EVENT_VIEW,
                session_hash=f'linkedin-{i}', referrer='linkedin.com',
            )
        _create_page_event(profile, 'portafolio', PageEvent.EVENT_VIEW, session_hash='blank-1')

        response = self.client.get(VISTA_TRAFFIC_URL, **self.headers)
        referrers = response.json()['referrers']
        self.assertEqual(referrers[0], {'source': 'google.com', 'visits': 3})
        self.assertEqual(referrers[1], {'source': 'linkedin.com', 'visits': 2})
        self.assertEqual(len(referrers), 2)  # blank referrer never appears

    def test_referrers_ignore_share_events(self):
        tenant_a = _create_tenant('tenant-a')
        profile = _create_public_profile(tenant_a, 'creator-a')

        _create_page_event(
            profile, 'tarjeta', PageEvent.EVENT_VIEW, session_hash='s1', referrer='a.com',
        )
        # A share event forced to carry a referrer (track_share() never does this
        # in practice) must still be excluded — referrers are views-only.
        _create_page_event(
            profile, 'tarjeta', PageEvent.EVENT_SHARE, referrer='b.com',
        )

        response = self.client.get(VISTA_TRAFFIC_URL, **self.headers)
        referrers = response.json()['referrers']
        self.assertEqual(referrers, [{'source': 'a.com', 'visits': 1}])

    def test_zero_page_events_returns_zeroed_services_and_empty_referrers(self):
        response = self.client.get(VISTA_TRAFFIC_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(len(body['services']), 4)
        self.assertTrue(all(s['views'] == 0 for s in body['services']))
        self.assertEqual(body['referrers'], [])

    def test_period_param_defaults_and_caps(self):
        response = self.client.get(f'{VISTA_TRAFFIC_URL}?period=abc', **self.headers)
        self.assertEqual(response.json()['period_days'], 30)

        response = self.client.get(f'{VISTA_TRAFFIC_URL}?period=9999', **self.headers)
        self.assertEqual(response.json()['period_days'], 365)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestDesktopLicenseFunnelViewStaffOnly(APITestCase):
    def setUp(self):
        cache.clear()
        self.own_tenant = _create_tenant('own-corp')
        self.headers = {'HTTP_X_TENANT_SLUG': 'own-corp'}

    def test_non_staff_user_with_rbac_permission_is_blocked(self):
        owner = User.objects.create_user(
            email='owner@own-corp.com', name='Owner', password='pass123', tenant=self.own_tenant,
        )
        _grant_permission(owner, 'customers.analytics')
        self.client.force_authenticate(user=owner)

        response = self.client.get(DESKTOP_LICENSES_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_without_rbac_permission_is_blocked(self):
        staff = _create_staff(self.own_tenant, 'staff@own-corp.com')
        self.client.force_authenticate(user=staff)

        response = self.client.get(DESKTOP_LICENSES_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_with_rbac_permission_succeeds(self):
        staff = _create_staff(self.own_tenant, 'staff2@own-corp.com')
        _grant_permission(staff, 'customers.analytics')
        self.client.force_authenticate(user=staff)

        response = self.client.get(DESKTOP_LICENSES_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS, CACHES=_LOCMEM_CACHE)
class TestDesktopLicenseFunnelMetrics(APITestCase):
    def setUp(self):
        cache.clear()
        self.own_tenant = _create_tenant('own-corp')
        self.staff = _create_staff(self.own_tenant, 'staff@own-corp.com')
        _grant_permission(self.staff, 'customers.analytics')
        self.client.force_authenticate(user=self.staff)
        self.headers = {'HTTP_X_TENANT_SLUG': 'own-corp'}
        self.now = timezone.now()

    def test_buckets_are_mutually_exclusive_and_counted_correctly(self):
        tenant_a = _create_tenant('tenant-a')
        activated_user = User.objects.create_user(
            email='activated@tenant-a.com', name='A', password='x', tenant=tenant_a,
        )
        pending_user = User.objects.create_user(
            email='pending@tenant-a.com', name='P', password='x', tenant=tenant_a,
        )
        revoked_user = User.objects.create_user(
            email='revoked@tenant-a.com', name='R', password='x', tenant=tenant_a,
        )

        _create_license(
            activated_user, hardware_id='HW-1', activated_at=self.now, sent_at=self.now,
        )
        _create_license(pending_user, sent_at=self.now)
        # Revoked but WAS previously activated — must land in 'revoked', not
        # 'activated' (mutually exclusive, matches DesktopAppLicense.status
        # priority: revoked wins).
        _create_license(
            revoked_user, hardware_id='HW-3', activated_at=self.now, sent_at=self.now,
            is_active=False,
        )

        response = self.client.get(DESKTOP_LICENSES_URL, **self.headers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.json()
        self.assertEqual(body['total'], 3)
        self.assertEqual(body['activated'], 1)
        self.assertEqual(body['pending'], 1)
        self.assertEqual(body['revoked'], 1)
        self.assertEqual(body['sent'], 3)

    def test_sent_counts_independently_of_status(self):
        tenant_a = _create_tenant('tenant-a')
        user_a = User.objects.create_user(
            email='a@tenant-a.com', name='A', password='x', tenant=tenant_a,
        )
        # Never sent, still pending — must not count toward `sent`.
        _create_license(user_a)

        response = self.client.get(DESKTOP_LICENSES_URL, **self.headers)
        body = response.json()
        self.assertEqual(body['sent'], 0)
        self.assertEqual(body['pending'], 1)

    def test_activation_rate_uses_sent_as_denominator_with_zero_guard(self):
        response = self.client.get(DESKTOP_LICENSES_URL, **self.headers)
        self.assertEqual(response.json()['activation_rate'], 0.0)

        tenant_a = _create_tenant('tenant-a')
        user_a = User.objects.create_user(
            email='a@tenant-a.com', name='A', password='x', tenant=tenant_a,
        )
        user_b = User.objects.create_user(
            email='b@tenant-a.com', name='B', password='x', tenant=tenant_a,
        )
        _create_license(user_a, hardware_id='HW-1', activated_at=self.now, sent_at=self.now)
        _create_license(user_b, sent_at=self.now)  # sent, not yet activated

        cache.clear()
        response = self.client.get(DESKTOP_LICENSES_URL, **self.headers)
        body = response.json()
        self.assertEqual(body['sent'], 2)
        self.assertEqual(body['activated'], 1)
        self.assertEqual(body['activation_rate'], 50.0)

    def test_own_tenant_excluded(self):
        own_user = User.objects.create_user(
            email='owncreator@own-corp.com', name='Own', password='x', tenant=self.own_tenant,
        )
        _create_license(
            own_user, hardware_id='HW-own', activated_at=self.now, sent_at=self.now,
        )

        response = self.client.get(DESKTOP_LICENSES_URL, **self.headers)
        body = response.json()
        self.assertEqual(body['total'], 0)
        self.assertEqual(body['sent'], 0)
        self.assertEqual(body['activated'], 0)
