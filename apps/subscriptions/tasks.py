"""Subscription-related Celery tasks."""
import logging
from datetime import timedelta

import requests
from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)


def _plan_label(plan: str) -> str:
    from apps.subscriptions.serializers import PLAN_DISPLAY_NAMES
    return PLAN_DISPLAY_NAMES.get(plan, plan.capitalize())


def _hub_url() -> str:
    return getattr(settings, 'FRONTEND_HUB_URL', '').rstrip('/')


def _notify_owner(tenant, subject: str, body: str, title: str, icon: str) -> None:
    """
    Email al owner + notificación in-app del tenant. El owner es el usuario más
    antiguo, igual que en las tareas de trial. `fail_silently` porque un SMTP caído
    no debe abortar la degradación: el estado en BD es la fuente de verdad.
    """
    from django.core.mail import send_mail

    from apps.notifications.models import Notification

    Notification.objects.create(
        tenant=tenant, category='billing', title=title, message=body.strip(), icon=icon,
    )

    owner = tenant.users.order_by('created_at').first()
    if owner is None:
        logger.warning('subscription notice: tenant %s has no users to email', tenant.slug)
        return

    send_mail(
        subject=subject,
        message=f'Hola {owner.name},\n\n{body.strip()}\n\n— El equipo de Hub de Servicios',
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[owner.email],
        fail_silently=True,
    )


def _audit_subscription(subscription, action: str, extra: dict) -> None:
    """Evento de sistema: sin usuario ni request detrás (AuditLog.user es nullable)."""
    from apps.audit.models import AuditLog

    AuditLog.objects.create(
        tenant=subscription.tenant,
        user=None,
        action=action,
        resource_type='Subscription',
        resource_id=str(subscription.id),
        extra=extra,
    )


@shared_task(
    name='apps.subscriptions.tasks.notify_yape_payment',
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=10,
)
def notify_yape_payment(proof_id: str) -> None:
    """
    POST to the n8n webhook with Yape proof data so n8n can:
    1. Analyze the screenshot with OpenAI vision
    2. Send a Telegram message with approve/reject one-click links
    """
    from apps.subscriptions.models import YapePaymentProof

    try:
        proof = YapePaymentProof.objects.select_related(
            'subscription__tenant'
        ).get(pk=proof_id)
    except YapePaymentProof.DoesNotExist:
        logger.error('notify_yape_payment: YapePaymentProof %s not found', proof_id)
        return

    webhook_url = getattr(settings, 'N8N_YAPE_PAYMENT_WEBHOOK_URL', '')
    if not webhook_url:
        logger.warning('notify_yape_payment: N8N_YAPE_PAYMENT_WEBHOOK_URL not configured')
        return

    tenant = proof.subscription.tenant
    owner  = tenant.users.order_by('created_at').first()
    base_url = getattr(settings, 'APP_BASE_URL', '').rstrip('/')

    redemption = getattr(proof, 'redemption', None)
    promo = None
    if redemption is not None:
        promo = {
            'code':            redemption.promotion.code,
            'original_amount': str(redemption.original_amount),
            'discount_amount': str(redemption.discount_amount),
            'final_amount':    str(redemption.final_amount),
        }

    from apps.subscriptions.models import YapeConfig

    payload = {
        'proof_id':      str(proof.id),
        'plan':          proof.plan,
        # Sin el ciclo, quien aprueba desde Telegram ve "$854" para un plan que sabe
        # que cuesta $79/mes y no puede distinguir un pago anual legítimo de un error
        # — y con un clic activa 365 días.
        'billing_cycle': proof.billing_cycle,
        'amount':        str(proof.amount),
        'promo':         promo,
        'exchange_rate': str(YapeConfig.get().exchange_rate),
        'tenant': {
            'id':        str(tenant.id),
            'name':      tenant.name,
            'slug':      tenant.slug,
            'subdomain': tenant.subdomain,
        },
        'user': {
            'name':  owner.name  if owner else '',
            'email': owner.email if owner else '',
        },
        'image_url':   f"{base_url}/media/{proof.screenshot.name}",
        'approve_url': f"{base_url}/api/v1/public/yape-payment/activate/{proof.admin_token}/",
        'reject_url':  f"{base_url}/api/v1/public/yape-payment/reject/{proof.admin_token}/",
        'submitted_at': timezone.now().isoformat(),
    }

    response = requests.post(webhook_url, json=payload, timeout=30)
    response.raise_for_status()
    logger.info(
        'notify_yape_payment: proof %s sent to n8n (status=%s)',
        proof_id, response.status_code,
    )


@shared_task(
    name='apps.subscriptions.tasks.expire_professional_trials',
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=10,
)
def expire_professional_trials() -> None:
    """
    Downgrade all Professional trialing subscriptions whose trial has expired.
    Sends downgrade notification email to each tenant owner.
    Runs daily at 04:00 UTC via Celery beat.
    """
    from apps.subscriptions.models import Subscription
    from django.core.mail import send_mail
    from django.db import transaction

    now = timezone.now()
    expired_subs = Subscription.objects.filter(
        plan='professional',
        status='trialing',
        trial_end__lte=now,
    ).select_related('tenant')

    for sub in expired_subs:
        tenant = sub.tenant
        owner = tenant.users.order_by('created_at').first()

        with transaction.atomic():
            sub.plan = 'free'
            sub.status = 'active'
            sub.trial_start = None
            sub.trial_end = None
            sub.save(update_fields=['plan', 'status', 'trial_start', 'trial_end', 'updated_at'])
            tenant.plan = 'free'
            tenant.save(update_fields=['plan', 'updated_at'])

        if owner:
            hub_url = getattr(settings, 'FRONTEND_HUB_URL', '').rstrip('/')
            send_mail(
                subject='Tu período de prueba Professional ha finalizado',
                message=(
                    f'Hola {owner.name},\n\n'
                    'Tu prueba gratuita de 30 días del Plan Professional ha finalizado. '
                    'Tu cuenta ha vuelto al Plan Free.\n\n'
                    f'Si deseas continuar con Professional, accede a tu panel y actualiza tu plan: '
                    f'{hub_url}/subscription\n\n'
                    '— El equipo de Hub de Servicios'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[owner.email],
                fail_silently=True,
            )
        logger.info('expire_professional_trials: downgraded tenant %s', tenant.slug)


@shared_task(
    name='apps.subscriptions.tasks.remind_professional_trial_expiry',
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=10,
)
def remind_professional_trial_expiry() -> None:
    """
    Send a 7-day reminder email to tenants whose Professional trial expires in ~7 days.
    Uses a ±1 day window (6–8 days) to handle Celery beat scheduling jitter.
    Runs daily at 10:00 UTC via Celery beat.
    """
    from apps.subscriptions.models import Subscription
    from django.core.mail import send_mail

    now = timezone.now()
    window_start = now + timedelta(days=6)
    window_end = now + timedelta(days=8)

    reminder_subs = Subscription.objects.filter(
        plan='professional',
        status='trialing',
        trial_end__gte=window_start,
        trial_end__lte=window_end,
    ).select_related('tenant')

    for sub in reminder_subs:
        tenant = sub.tenant
        owner = tenant.users.order_by('created_at').first()
        if not owner:
            continue

        days_left = max(1, (sub.trial_end - now).days)
        hub_url = getattr(settings, 'FRONTEND_HUB_URL', '').rstrip('/')
        send_mail(
            subject=f'Tu prueba Professional termina en {days_left} días',
            message=(
                f'Hola {owner.name},\n\n'
                f'Tu prueba gratuita del Plan Professional termina en {days_left} días '
                f'(el {sub.trial_end.strftime("%d/%m/%Y")}).\n\n'
                'Para no perder el acceso a funcionalidades profesionales, actualiza '
                f'tu plan antes de que expire: {hub_url}/subscription\n\n'
                '— El equipo de Hub de Servicios'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[owner.email],
            fail_silently=True,
        )
        logger.info('remind_professional_trial_expiry: reminded tenant %s', tenant.slug)


@shared_task(
    name='apps.subscriptions.tasks.expire_paid_subscriptions',
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=10,
)
def expire_paid_subscriptions(dry_run: bool = False) -> dict:
    """
    Cierra el ciclo de vida de los planes PAGADOS vencidos. Corre a diario (04:15 UTC).

    Dos pasos en una pasada:

      1. Entrar en gracia — plan de pago `active` cuyo `current_period_end` ya pasó:
         pasa a `past_due` con `grace_until = current_period_end + GRACE_DAYS` y
         CONSERVA el acceso completo. Si el tenant había pedido la baja
         (`cancel_at_period_end`), se degrada en el acto y sin gracia: extenderle el
         servicio contradiría su decisión.

      2. Degradar tras la gracia — `past_due` con `grace_until` cumplido: `Tenant.plan`
         y `Subscription.plan` a 'free'. El gating (check_plan_limit / plan_has_feature)
         lee `Tenant.plan`, así que el acceso cae solo — ver ADR-008, decisión 1.

    No degrada si hay un comprobante Yape `pending`: el cobro es manual y lo revisa una
    persona, así que cortarle a quien ya pagó sería el peor fallo posible. Sin cota de
    tiempo, pero con WARNING y contador en el resumen para que se vea.

    Excluye `status='trialing'` (los gobierna `expire_professional_trials`, ADR-006) y
    `current_period_end IS NULL` (dato incompleto: no se corta a ciegas).

    NO limpia `current_period_end` ni `billing_cycle` al degradar — de ellos depende que
    `get_renewal_state()` distinga un plan vencido de un Free que nunca pagó.

    Con `dry_run=True` no escribe nada y solo informa qué haría.
    """
    from django.db import transaction

    from apps.subscriptions.models import Subscription
    from apps.subscriptions.services import GRACE_DAYS

    now = timezone.now()
    summary = {
        'grace_started': 0,
        'canceled': 0,
        'expired': 0,
        'skipped_pending_proof': 0,
        'dry_run': dry_run,
        'details': [],
    }

    # ── Paso 1: entrar en gracia (o degradar ya, si pidió la baja) ────────────────
    entering = Subscription.objects.filter(
        status='active',
        current_period_end__lte=now,
        current_period_end__isnull=False,
        grace_until__isnull=True,
    ).exclude(plan='free').select_related('tenant')

    for sub in entering:
        if sub.tenant.plan == 'free':
            # Tenant.plan es la fuente de verdad: si ya está en free no hay nada que vencer.
            continue

        if sub.cancel_at_period_end:
            summary['canceled'] += 1
            summary['details'].append((sub.tenant.slug, sub.tenant.plan, 'canceled'))
            if not dry_run:
                _degrade_to_free(sub, now, status='canceled')
            continue

        summary['grace_started'] += 1
        summary['details'].append((sub.tenant.slug, sub.tenant.plan, 'grace_started'))
        if dry_run:
            continue

        plan_label = _plan_label(sub.tenant.plan)
        grace_until = sub.current_period_end + timedelta(days=GRACE_DAYS)
        with transaction.atomic():
            sub.status = 'past_due'
            sub.grace_until = grace_until
            sub.save(update_fields=['status', 'grace_until', 'updated_at'])
            _audit_subscription(sub, 'subscription.grace_started', {
                'plan': sub.tenant.plan,
                'billing_cycle': sub.billing_cycle,
                'period_end': sub.current_period_end.isoformat(),
                'grace_until': grace_until.isoformat(),
            })
        _notify_owner(
            sub.tenant,
            subject=f'Tu plan {plan_label} venció — tienes {GRACE_DAYS} días para renovar',
            body=(
                f'Tu plan {plan_label} venció el {sub.current_period_end:%d/%m/%Y}. '
                f'Mantienes el acceso completo hasta el {grace_until:%d/%m/%Y}.\n\n'
                f'Renueva para no perder ninguna funcionalidad: {_hub_url()}/subscription'
            ),
            title='Tu plan venció — renueva para no perder acceso',
            icon='AlertTriangle',
        )
        logger.info(
            'expire_paid_subscriptions: grace started for %s until %s',
            sub.tenant.slug, grace_until.date(),
        )

    # ── Paso 2: degradar tras la gracia ──────────────────────────────────────────
    expiring = Subscription.objects.filter(
        status='past_due',
        grace_until__lte=now,
        grace_until__isnull=False,
    ).exclude(plan='free').select_related('tenant')

    for sub in expiring:
        pending = sub.yape_proofs.filter(status='pending').order_by('created_at').first()
        if pending is not None:
            summary['skipped_pending_proof'] += 1
            summary['details'].append((sub.tenant.slug, sub.tenant.plan, 'skipped_pending_proof'))
            logger.warning(
                'expire_paid_subscriptions: %s NOT degraded — proof %s pending since %s '
                '(%s days). Revisar la cola de comprobantes.',
                sub.tenant.slug, pending.id, pending.created_at.date(),
                (now - pending.created_at).days,
            )
            continue

        summary['expired'] += 1
        summary['details'].append((sub.tenant.slug, sub.tenant.plan, 'expired'))
        if not dry_run:
            _degrade_to_free(sub, now, status='active')

    logger.info(
        'expire_paid_subscriptions: grace=%s canceled=%s expired=%s skipped=%s dry_run=%s',
        summary['grace_started'], summary['canceled'], summary['expired'],
        summary['skipped_pending_proof'], dry_run,
    )
    return summary


def _degrade_to_free(subscription, now, status: str) -> None:
    """
    Baja el plan a Free en Subscription y Tenant, audita y avisa.

    `status='canceled'` para una baja pedida por el cliente; `'active'` cuando el plan
    simplemente expiró (un tenant en Free es un tenant legítimamente activo — mismo
    criterio que `expire_professional_trials`, ver ADR-008 decisión 6).

    Deliberadamente NO toca `current_period_end` ni `billing_cycle`.
    """
    from django.db import transaction

    tenant = subscription.tenant
    previous_plan = tenant.plan
    plan_label = _plan_label(previous_plan)
    was_canceled = status == 'canceled'

    with transaction.atomic():
        subscription.plan = 'free'
        subscription.status = status
        subscription.grace_until = None
        subscription.save(update_fields=['plan', 'status', 'grace_until', 'updated_at'])
        tenant.plan = 'free'
        tenant.save(update_fields=['plan', 'updated_at'])
        _audit_subscription(
            subscription,
            'subscription.canceled_at_period_end' if was_canceled else 'subscription.expired',
            {
                'previous_plan': previous_plan,
                'billing_cycle': subscription.billing_cycle,
                'period_end': (
                    subscription.current_period_end.isoformat()
                    if subscription.current_period_end else None
                ),
            },
        )

    if was_canceled:
        subject = f'Tu suscripción {plan_label} finalizó'
        body = (
            f'Tal como solicitaste, tu suscripción al plan {plan_label} finalizó y tu '
            f'cuenta volvió al plan Free.\n\n'
            f'Tus datos se conservan intactos. Puedes volver a contratar cuando quieras: '
            f'{_hub_url()}/subscription'
        )
        title = 'Tu suscripción finalizó'
    else:
        subject = f'Tu plan {plan_label} expiró y tu cuenta volvió a Free'
        body = (
            f'El período de gracia terminó sin que registráramos tu renovación, así que '
            f'tu cuenta volvió al plan Free.\n\n'
            f'No se eliminó nada: tus datos siguen ahí y se reactivan en cuanto renueves. '
            f'Mientras estés en Free no podrás crear recursos por encima de sus límites.\n\n'
            f'Reactivar tu plan {plan_label}: {_hub_url()}/subscription'
        )
        title = f'Tu plan {plan_label} expiró'

    _notify_owner(tenant, subject=subject, body=body, title=title, icon='XCircle')
    logger.info(
        'expire_paid_subscriptions: %s degraded from %s to free (status=%s)',
        tenant.slug, previous_plan, status,
    )


@shared_task(
    name='apps.subscriptions.tasks.remind_subscription_expiry',
    ignore_result=True,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=10,
)
def remind_subscription_expiry(dry_run: bool = False) -> dict:
    """
    Avisa a los planes de pago que están por vencer, en los hitos T-7 / T-3 / T-1.
    Corre a diario (10:15 UTC).

    Cada hito se busca con una ventana de ±REMINDER_WINDOW_HOURS para absorber el jitter
    del scheduler, pero la idempotencia NO depende de la ventana: el hito enviado se
    registra en `Subscription.renewal_reminders_sent` y no se reenvía. Un restart del
    worker, o dos corridas el mismo día, no duplican correos.
    `activate_subscription_plan()` limpia esa lista al pagar, así que el ciclo siguiente
    vuelve a avisar.

    Se omiten los `cancel_at_period_end=True`: pedir renovar a quien acaba de darse de
    baja es spam, y el banner de cancelación del Hub ya le muestra la fecha.
    """
    from apps.subscriptions.models import Subscription
    from apps.subscriptions.services import REMINDER_MILESTONES, REMINDER_WINDOW_HOURS

    now = timezone.now()
    half_window = timedelta(hours=REMINDER_WINDOW_HOURS)
    summary = {'sent': 0, 'already_sent': 0, 'dry_run': dry_run, 'details': []}

    for days in REMINDER_MILESTONES:
        milestone = f'T-{days}'
        target = now + timedelta(days=days)

        candidates = Subscription.objects.filter(
            status='active',
            cancel_at_period_end=False,
            current_period_end__gte=target - half_window,
            current_period_end__lte=target + half_window,
        ).exclude(plan='free').select_related('tenant')

        for sub in candidates:
            if sub.tenant.plan == 'free':
                continue
            if milestone in (sub.renewal_reminders_sent or []):
                summary['already_sent'] += 1
                continue

            summary['sent'] += 1
            summary['details'].append((sub.tenant.slug, sub.tenant.plan, milestone))
            if dry_run:
                continue

            plan_label = _plan_label(sub.tenant.plan)
            sub.renewal_reminders_sent = [*(sub.renewal_reminders_sent or []), milestone]
            sub.save(update_fields=['renewal_reminders_sent', 'updated_at'])
            _notify_owner(
                sub.tenant,
                subject=f'Tu plan {plan_label} vence en {days} día{"s" if days > 1 else ""}',
                body=(
                    f'Tu plan {plan_label} vence el {sub.current_period_end:%d/%m/%Y}.\n\n'
                    f'Renuévalo para conservar todas las funcionalidades: '
                    f'{_hub_url()}/subscription'
                ),
                title=f'Tu plan vence en {days} día{"s" if days > 1 else ""}',
                icon='Clock',
            )
            logger.info(
                'remind_subscription_expiry: %s notified at %s', sub.tenant.slug, milestone,
            )

    logger.info(
        'remind_subscription_expiry: sent=%s already_sent=%s dry_run=%s',
        summary['sent'], summary['already_sent'], dry_run,
    )
    return summary
