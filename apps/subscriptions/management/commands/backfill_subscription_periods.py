"""
Rellena `current_period_start`/`current_period_end` en las suscripciones pagadas
que quedaron sin período registrado.

El signal `auto_create_subscription` (apps/subscriptions/signals.py) crea toda
Subscription con `trial_*` pero sin `current_period_*`, así que un tenant que
llegó a un plan pagado sin pasar por `activate_subscription_plan()` quedó con el
período en NULL. Sin período no hay nada que vencer, y la tarea de expiración de
la Fase 5 los ignoraría para siempre.

Alcance deliberadamente acotado a `status='active'` — ver ADR-008 y la sección
"Migración de Datos Existentes" del PRD:

  - trialing                  → los gobierna `expire_professional_trials` (ADR-006)
  - pending_payment / unpaid  → nunca completaron el pago; darles período sería
                                regalar un mes. `activate_subscription_plan()` ya
                                fija el período cuando su comprobante se apruebe
  - canceled / free           → sin período que proteger

Idempotente: filtra por `current_period_end__isnull=True`, así que una segunda
pasada no encuentra nada. Corre en dry-run salvo que se pase `--apply`.

    python manage.py backfill_subscription_periods            # preview
    python manage.py backfill_subscription_periods --apply    # escribe
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.subscriptions.models import Invoice, Subscription

COURTESY_DAYS = 30

SOURCE_INVOICE = 'invoice'
SOURCE_COURTESY = 'cortesia'


def _resolve_period(subscription: Subscription, now) -> tuple[object, object, str]:
    """
    Devuelve (period_start, period_end, origen) para una suscripción sin período.

    Prioriza la última factura pagada del tenant: es el período realmente
    contratado, aunque ya haya vencido (en ese caso el tenant entrará en gracia
    cuando corra la tarea de la Fase 5, que es el resultado honesto — tuvo un
    período y terminó). Sin factura, se concede un período de cortesía: un dato
    incompleto no debe traducirse en un corte de servicio el mismo día.
    """
    last_paid = (
        Invoice.objects
        .filter(tenant=subscription.tenant, status='paid', period_end__isnull=False)
        .order_by('-period_end')
        .first()
    )
    if last_paid is not None:
        return last_paid.period_start or last_paid.invoice_date, last_paid.period_end, SOURCE_INVOICE
    return now, now + timedelta(days=COURTESY_DAYS), SOURCE_COURTESY


class Command(BaseCommand):
    help = (
        'Rellena el período de facturación de las suscripciones activas de pago '
        'que lo tengan en NULL. Dry-run por defecto; usar --apply para escribir.'
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Escribe los cambios. Sin este flag solo muestra qué haría.',
        )

    def handle(self, *args, **options) -> None:
        apply_changes: bool = options['apply']
        now = timezone.now()

        pending = (
            Subscription.objects
            .exclude(plan='free')
            .filter(status='active', current_period_end__isnull=True)
            .select_related('tenant')
            .order_by('tenant__slug')
        )

        if not pending.exists():
            self.stdout.write(self.style.SUCCESS(
                'Nada que rellenar: no hay suscripciones activas de pago sin período.'
            ))
            return

        mode = 'APLICANDO' if apply_changes else 'DRY-RUN (usar --apply para escribir)'
        self.stdout.write(f'Modo: {mode}')
        self.stdout.write(
            f'{"TENANT":<28} {"PLAN":<13} {"ESTADO":<10} {"ORIGEN":<9} PERIOD_END'
        )

        counts = {SOURCE_INVOICE: 0, SOURCE_COURTESY: 0}
        for subscription in pending:
            period_start, period_end, source = _resolve_period(subscription, now)
            counts[source] += 1
            self.stdout.write(
                f'{subscription.tenant.slug:<28} {subscription.plan:<13} '
                f'{subscription.status:<10} {source:<9} '
                f'{period_end:%Y-%m-%d}'
            )

            if apply_changes:
                with transaction.atomic():
                    subscription.current_period_start = period_start
                    subscription.current_period_end = period_end
                    subscription.save(update_fields=[
                        'current_period_start', 'current_period_end', 'updated_at',
                    ])

        total = counts[SOURCE_INVOICE] + counts[SOURCE_COURTESY]
        summary = (
            f'{total} suscripción(es): {counts[SOURCE_INVOICE]} desde factura pagada, '
            f'{counts[SOURCE_COURTESY]} con período de cortesía de {COURTESY_DAYS} días.'
        )
        if apply_changes:
            self.stdout.write(self.style.SUCCESS(f'Actualizadas {summary}'))
        else:
            self.stdout.write(self.style.WARNING(f'Se actualizarían {summary}'))
