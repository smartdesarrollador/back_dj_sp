"""
Tests de la expiración automática de planes pagados.

Es la primera tarea del sistema que **quita** acceso, así que los casos importantes no
son solo "degrada cuando toca" sino sobre todo los que **no** deben degradar: trials,
períodos sin fecha, comprobantes pendientes de revisión y planes aún vigentes.

Primer test de tareas Celery del repo: se llama la función del task directamente
(síncrono), sin `.delay()` ni modo eager. No hay `freezegun` en requirements, así que las
fechas se construyen relativas a `timezone.now()`.
"""
import uuid
from datetime import timedelta
from decimal import Decimal

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.subscriptions.models import Invoice, Subscription, YapePaymentProof
from apps.subscriptions.services import (
    GRACE_DAYS,
    activate_yape_proof,
    get_renewal_state,
)
from apps.subscriptions.tasks import expire_paid_subscriptions, remind_subscription_expiry
from apps.tenants.models import Tenant
from core.tests.helpers import png_bytes

_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


def make_tenant(plan='professional'):
    slug = f'tenant-{uuid.uuid4().hex[:8]}'
    return Tenant.objects.create(name=slug, slug=slug, subdomain=slug, plan=plan)


def make_user(tenant, **extra):
    from apps.auth_app.models import User
    return User.objects.create_user(
        email=f'user-{uuid.uuid4().hex[:8]}@example.com',
        name='Owner', password='testpass123', tenant=tenant, **extra,
    )


def make_sub(plan='professional', days_ago=None, with_owner=True, **extra) -> Subscription:
    """
    Suscripción en el estado buscado. `days_ago` sitúa `current_period_end` en el pasado
    (o en el futuro si es negativo). El signal ya creó la suscripción del tenant.
    """
    tenant = make_tenant(plan)
    if with_owner:
        make_user(tenant)
    sub = Subscription.objects.get(tenant=tenant)
    sub.plan = plan
    sub.status = extra.pop('status', 'active')
    if days_ago is not None:
        sub.current_period_end = timezone.now() - timedelta(days=days_ago)
    for field, value in extra.items():
        setattr(sub, field, value)
    sub.save()
    return sub


def add_pending_proof(sub, created_days_ago=0) -> YapePaymentProof:
    proof = YapePaymentProof.objects.create(
        subscription=sub, plan=sub.plan,
        screenshot=SimpleUploadedFile('p.png', png_bytes(), content_type='image/png'),
        amount=Decimal('79.00'), admin_token=uuid.uuid4().hex,
    )
    if created_days_ago:
        YapePaymentProof.objects.filter(pk=proof.pk).update(
            created_at=timezone.now() - timedelta(days=created_days_ago)
        )
        proof.refresh_from_db()
    return proof


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestEnterGrace(TestCase):
    def test_expired_paid_plan_enters_grace(self):
        sub = make_sub(days_ago=1)
        period_end = sub.current_period_end

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(summary['grace_started'], 1)
        self.assertEqual(sub.status, 'past_due')
        self.assertAlmostEqual(
            sub.grace_until, period_end + timedelta(days=GRACE_DAYS),
            delta=timedelta(seconds=5),
        )

    def test_access_is_kept_during_grace(self):
        """El gating lee Tenant.plan: en gracia sigue siendo el plan pagado."""
        sub = make_sub(days_ago=1)

        expire_paid_subscriptions()

        sub.tenant.refresh_from_db()
        self.assertEqual(sub.tenant.plan, 'professional')

    def test_grace_notifies_owner_and_audits(self):
        sub = make_sub(days_ago=1)

        expire_paid_subscriptions()

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('venció', mail.outbox[0].subject)
        self.assertTrue(
            Notification.objects.filter(tenant=sub.tenant, category='billing').exists()
        )
        log = AuditLog.objects.get(action='subscription.grace_started')
        self.assertIsNone(log.user)
        self.assertEqual(log.resource_type, 'Subscription')
        self.assertEqual(log.extra['plan'], 'professional')

    def test_still_valid_period_is_untouched(self):
        sub = make_sub(days_ago=-5)  # vence en 5 días

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(summary['grace_started'], 0)
        self.assertEqual(sub.status, 'active')
        self.assertIsNone(sub.grace_until)

    def test_second_run_does_not_restart_grace(self):
        sub = make_sub(days_ago=1)
        expire_paid_subscriptions()
        first_grace = Subscription.objects.get(pk=sub.pk).grace_until

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(summary['grace_started'], 0)
        self.assertEqual(sub.grace_until, first_grace)
        self.assertEqual(len(mail.outbox), 1)  # sin email duplicado


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestDegradeAfterGrace(TestCase):
    def _expired_grace(self, **extra):
        return make_sub(
            days_ago=GRACE_DAYS + 2, status='past_due',
            grace_until=timezone.now() - timedelta(days=1), **extra,
        )

    def test_degrades_tenant_and_subscription(self):
        sub = self._expired_grace()

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        sub.tenant.refresh_from_db()
        self.assertEqual(summary['expired'], 1)
        self.assertEqual(sub.tenant.plan, 'free')
        self.assertEqual(sub.plan, 'free')
        self.assertEqual(sub.status, 'active')
        self.assertIsNone(sub.grace_until)

    def test_period_end_and_cycle_are_preserved(self):
        """
        Contrato de la Fase 4: de current_period_end depende que get_renewal_state()
        distinga un plan vencido de un Free que nunca pagó.
        """
        sub = self._expired_grace(billing_cycle='annual')
        period_end = sub.current_period_end

        expire_paid_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(sub.current_period_end, period_end)
        self.assertEqual(sub.billing_cycle, 'annual')

    def test_renewal_state_becomes_expired(self):
        sub = self._expired_grace()
        Invoice.objects.create(
            tenant=sub.tenant, stripe_invoice_id=f'inv_{uuid.uuid4().hex[:8]}',
            amount_cents=7900, status='paid',
            period_start=timezone.now() - timedelta(days=40),
            period_end=timezone.now() - timedelta(days=10),
            invoice_date=timezone.now() - timedelta(days=40),
        )

        expire_paid_subscriptions()

        sub.refresh_from_db()
        sub.tenant.refresh_from_db()
        self.assertEqual(get_renewal_state(sub), 'expired')

    def test_degradation_notifies_and_audits(self):
        self._expired_grace()

        expire_paid_subscriptions()

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('expiró', mail.outbox[0].subject)
        log = AuditLog.objects.get(action='subscription.expired')
        self.assertEqual(log.extra['previous_plan'], 'professional')

    def test_grace_not_yet_reached_is_untouched(self):
        sub = make_sub(
            days_ago=2, status='past_due',
            grace_until=timezone.now() + timedelta(days=5),
        )

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        sub.tenant.refresh_from_db()
        self.assertEqual(summary['expired'], 0)
        self.assertEqual(sub.tenant.plan, 'professional')
        self.assertEqual(sub.status, 'past_due')

    def test_second_run_is_idempotent(self):
        self._expired_grace()
        expire_paid_subscriptions()

        summary = expire_paid_subscriptions()

        self.assertEqual(summary['expired'], 0)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(AuditLog.objects.filter(action='subscription.expired').count(), 1)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestCancelAtPeriodEnd(TestCase):
    def test_cancellation_degrades_without_grace(self):
        sub = make_sub(days_ago=1, cancel_at_period_end=True)

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        sub.tenant.refresh_from_db()
        self.assertEqual(summary['canceled'], 1)
        self.assertEqual(summary['grace_started'], 0)
        self.assertEqual(sub.tenant.plan, 'free')
        self.assertEqual(sub.status, 'canceled')
        self.assertIsNone(sub.grace_until)

    def test_cancellation_audits_its_own_action(self):
        make_sub(days_ago=1, cancel_at_period_end=True)

        expire_paid_subscriptions()

        self.assertTrue(
            AuditLog.objects.filter(action='subscription.canceled_at_period_end').exists()
        )
        self.assertIn('finalizó', mail.outbox[0].subject)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestExclusions(TestCase):
    def test_trialing_is_left_to_the_trial_task(self):
        sub = make_sub(days_ago=1, status='trialing')

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        sub.tenant.refresh_from_db()
        self.assertEqual(summary['grace_started'], 0)
        self.assertEqual(sub.tenant.plan, 'professional')
        self.assertEqual(sub.status, 'trialing')

    def test_null_period_is_never_degraded(self):
        sub = make_sub(days_ago=None, current_period_end=None)

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(summary['grace_started'], 0)
        self.assertEqual(sub.status, 'active')

    def test_free_plan_is_untouched(self):
        sub = make_sub(plan='free', days_ago=30)

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(summary['grace_started'], 0)
        self.assertIsNone(sub.grace_until)

    def test_tenant_already_free_is_skipped(self):
        """Subscription.plan puede desincronizarse; Tenant.plan es la fuente de verdad."""
        sub = make_sub(days_ago=1)
        sub.tenant.plan = 'free'
        sub.tenant.save(update_fields=['plan'])

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        self.assertEqual(summary['grace_started'], 0)
        self.assertEqual(sub.status, 'active')


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestPendingProofProtection(TestCase):
    def _expired_grace(self):
        return make_sub(
            days_ago=GRACE_DAYS + 2, status='past_due',
            grace_until=timezone.now() - timedelta(days=1),
        )

    def test_pending_proof_prevents_degradation(self):
        sub = self._expired_grace()
        add_pending_proof(sub)

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        sub.tenant.refresh_from_db()
        self.assertEqual(summary['skipped_pending_proof'], 1)
        self.assertEqual(summary['expired'], 0)
        self.assertEqual(sub.tenant.plan, 'professional')
        self.assertEqual(sub.status, 'past_due')

    def test_protection_has_no_time_limit(self):
        """Un comprobante viejo sigue protegiendo: la demora es culpa interna."""
        sub = self._expired_grace()
        add_pending_proof(sub, created_days_ago=40)

        summary = expire_paid_subscriptions()

        sub.tenant.refresh_from_db()
        self.assertEqual(summary['skipped_pending_proof'], 1)
        self.assertEqual(sub.tenant.plan, 'professional')

    def test_rejected_proof_does_not_protect(self):
        sub = self._expired_grace()
        proof = add_pending_proof(sub)
        proof.status = 'rejected'
        proof.save(update_fields=['status'])

        summary = expire_paid_subscriptions()

        sub.tenant.refresh_from_db()
        self.assertEqual(summary['expired'], 1)
        self.assertEqual(sub.tenant.plan, 'free')

    def test_approving_the_proof_removes_the_candidate(self):
        sub = self._expired_grace()
        activate_yape_proof(add_pending_proof(sub))

        summary = expire_paid_subscriptions()

        sub.refresh_from_db()
        sub.tenant.refresh_from_db()
        self.assertEqual(summary['expired'], 0)
        self.assertEqual(summary['skipped_pending_proof'], 0)
        self.assertEqual(sub.tenant.plan, 'professional')
        self.assertEqual(sub.status, 'active')


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestDryRun(TestCase):
    def test_dry_run_writes_nothing(self):
        sub = make_sub(days_ago=1)

        summary = expire_paid_subscriptions(dry_run=True)

        sub.refresh_from_db()
        self.assertEqual(summary['grace_started'], 1)
        self.assertTrue(summary['dry_run'])
        self.assertEqual(sub.status, 'active')
        self.assertIsNone(sub.grace_until)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(AuditLog.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_dry_run_reports_degradations_too(self):
        make_sub(
            days_ago=GRACE_DAYS + 2, status='past_due',
            grace_until=timezone.now() - timedelta(days=1),
        )

        summary = expire_paid_subscriptions(dry_run=True)

        self.assertEqual(summary['expired'], 1)
        self.assertEqual(Tenant.objects.filter(plan='professional').count(), 1)


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestExpiryReminders(TestCase):
    def test_reminder_is_sent_at_each_milestone(self):
        for days in (7, 3, 1):
            with self.subTest(days=days):
                mail.outbox = []
                sub = make_sub(days_ago=-days)

                summary = remind_subscription_expiry()

                sub.refresh_from_db()
                self.assertEqual(summary['sent'], 1)
                self.assertIn(f'T-{days}', sub.renewal_reminders_sent)
                self.assertIn(f'{days} día', mail.outbox[0].subject)

    def test_reminder_is_not_repeated(self):
        make_sub(days_ago=-3)
        remind_subscription_expiry()

        summary = remind_subscription_expiry()

        self.assertEqual(summary['sent'], 0)
        self.assertEqual(summary['already_sent'], 1)
        self.assertEqual(len(mail.outbox), 1)

    def test_milestones_accumulate(self):
        sub = make_sub(days_ago=-7, renewal_reminders_sent=['T-7'])
        sub.current_period_end = timezone.now() + timedelta(days=3)
        sub.save(update_fields=['current_period_end'])

        remind_subscription_expiry()

        sub.refresh_from_db()
        self.assertEqual(sub.renewal_reminders_sent, ['T-7', 'T-3'])

    def test_far_from_expiry_gets_nothing(self):
        make_sub(days_ago=-20)

        summary = remind_subscription_expiry()

        self.assertEqual(summary['sent'], 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_canceled_subscription_is_not_reminded(self):
        make_sub(days_ago=-3, cancel_at_period_end=True)

        summary = remind_subscription_expiry()

        self.assertEqual(summary['sent'], 0)

    def test_free_and_trialing_are_not_reminded(self):
        make_sub(plan='free', days_ago=-3)
        make_sub(days_ago=-3, status='trialing')

        summary = remind_subscription_expiry()

        self.assertEqual(summary['sent'], 0)

    def test_dry_run_does_not_mark_milestones(self):
        sub = make_sub(days_ago=-3)

        summary = remind_subscription_expiry(dry_run=True)

        sub.refresh_from_db()
        self.assertEqual(summary['sent'], 1)
        self.assertEqual(sub.renewal_reminders_sent, [])
        self.assertEqual(len(mail.outbox), 0)

    def test_paying_rearms_the_reminders(self):
        """activate_subscription_plan limpia la lista → el ciclo siguiente vuelve a avisar."""
        sub = make_sub(days_ago=-3)
        remind_subscription_expiry()
        sub.refresh_from_db()
        self.assertEqual(sub.renewal_reminders_sent, ['T-3'])

        activate_yape_proof(add_pending_proof(sub))

        sub.refresh_from_db()
        self.assertEqual(sub.renewal_reminders_sent, [])


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class TestDegradationDoesNotBlockAccess(APITestCase):
    def test_login_with_mfa_still_works_after_degradation(self):
        """
        MFA es una feature `professional+`, pero LoginView comprueba `user.mfa_enabled`
        (campo del usuario), no el plan. Degradar no debe dejar a nadie fuera de su
        cuenta — este test lo fija.
        """
        tenant = make_tenant('professional')
        user = make_user(tenant, mfa_enabled=True)
        user.email_verified = True
        user.save(update_fields=['email_verified'])
        sub = Subscription.objects.get(tenant=tenant)
        sub.plan = 'professional'
        sub.status = 'past_due'
        sub.current_period_end = timezone.now() - timedelta(days=GRACE_DAYS + 2)
        sub.grace_until = timezone.now() - timedelta(days=1)
        sub.save()

        expire_paid_subscriptions()
        tenant.refresh_from_db()
        self.assertEqual(tenant.plan, 'free')

        resp = self.client.post(
            '/api/v1/auth/login',
            {'email': user.email, 'password': 'testpass123'},
            format='json',
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(resp.data.get('mfa_required'))

    def test_existing_users_stay_active_after_degradation(self):
        """Congelado, no eliminación: los usuarios por encima del límite Free siguen ahí."""
        tenant = make_tenant('professional')
        for _ in range(7):  # Free permite 5
            make_user(tenant)
        sub = Subscription.objects.get(tenant=tenant)
        sub.plan = 'professional'
        sub.status = 'past_due'
        sub.current_period_end = timezone.now() - timedelta(days=GRACE_DAYS + 2)
        sub.grace_until = timezone.now() - timedelta(days=1)
        sub.save()

        expire_paid_subscriptions()

        tenant.refresh_from_db()
        self.assertEqual(tenant.plan, 'free')
        self.assertEqual(tenant.users.filter(is_active=True).count(), 7)

    def test_creating_beyond_free_limit_is_blocked_after_degradation(self):
        from apps.rbac.permissions import check_plan_limit
        from rest_framework.exceptions import APIException

        tenant = make_tenant('professional')
        owner = make_user(tenant)
        for _ in range(6):
            make_user(tenant)
        sub = Subscription.objects.get(tenant=tenant)
        sub.plan = 'professional'
        sub.status = 'past_due'
        sub.current_period_end = timezone.now() - timedelta(days=GRACE_DAYS + 2)
        sub.grace_until = timezone.now() - timedelta(days=1)
        sub.save()

        expire_paid_subscriptions()
        owner.refresh_from_db()

        with self.assertRaises(APIException) as ctx:
            check_plan_limit(owner, 'users', tenant.users.count())
        self.assertEqual(ctx.exception.status_code, 402)
