"""
Ejecuta a mano la expiración de planes pagados (y, opcionalmente, los recordatorios).

Existe para poder **revisar el lote antes de habilitar el beat**: es la primera tarea
del sistema que quita acceso, y actúa sobre datos históricos que nunca se sometieron a
expiración. Un dry-run en producción antes de encender el crontab evita degradar en masa
por un dato inesperado. Después queda como herramienta de ops (forzar la pasada del día,
o inspeccionar qué haría sin escribir).

    python manage.py expire_subscriptions                # preview
    python manage.py expire_subscriptions --apply        # escribe
    python manage.py expire_subscriptions --reminders    # incluye los avisos T-7/T-3/T-1

La lógica NO se duplica: llama a las mismas tareas Celery que corren por beat, en
proceso (sin `.delay()`).
"""
from django.core.management.base import BaseCommand

from apps.subscriptions.tasks import expire_paid_subscriptions, remind_subscription_expiry

ACTION_LABELS = {
    'grace_started': 'entra en gracia',
    'canceled': 'baja pedida → Free',
    'expired': 'gracia cumplida → Free',
    'skipped_pending_proof': 'NO degradado (comprobante pendiente)',
}


class Command(BaseCommand):
    help = (
        'Aplica la expiración de planes pagados vencidos. Dry-run por defecto; '
        'usar --apply para escribir.'
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            '--apply',
            action='store_true',
            help='Escribe los cambios. Sin este flag solo muestra qué haría.',
        )
        parser.add_argument(
            '--reminders',
            action='store_true',
            help='Ejecuta también los recordatorios de vencimiento (T-7/T-3/T-1).',
        )

    def handle(self, *args, **options) -> None:
        apply_changes: bool = options['apply']
        dry_run = not apply_changes

        self.stdout.write(
            f'Modo: {"APLICANDO" if apply_changes else "DRY-RUN (usar --apply para escribir)"}'
        )

        summary = expire_paid_subscriptions(dry_run=dry_run)
        self._render('Expiración', summary, ACTION_LABELS)

        if summary['skipped_pending_proof']:
            self.stdout.write(self.style.WARNING(
                f'\n{summary["skipped_pending_proof"]} suscripción(es) con comprobante '
                f'pendiente NO se degradaron. Revisar la cola de comprobantes: mientras '
                f'siga pendiente, el acceso se conserva.'
            ))

        if options['reminders']:
            reminders = remind_subscription_expiry(dry_run=dry_run)
            self._render('Recordatorios', reminders, {})
            self.stdout.write(
                f'Avisos: {reminders["sent"]} a enviar, '
                f'{reminders["already_sent"]} ya enviados antes.'
            )

    def _render(self, header: str, summary: dict, labels: dict) -> None:
        self.stdout.write(f'\n── {header} ──')
        if not summary['details']:
            self.stdout.write(self.style.SUCCESS('Sin candidatos.'))
            return

        self.stdout.write(f'{"TENANT":<28} {"PLAN":<13} ACCIÓN')
        for slug, plan, action in summary['details']:
            self.stdout.write(f'{slug:<28} {plan:<13} {labels.get(action, action)}')

        counts = {k: v for k, v in summary.items()
                  if k not in ('details', 'dry_run') and v}
        line = ' · '.join(f'{k}={v}' for k, v in counts.items()) or 'sin cambios'
        style = self.style.SUCCESS if not summary['dry_run'] else self.style.WARNING
        self.stdout.write(style(line))
