"""
Public one-click endpoints for admin Yape payment approval/rejection.
Accessed via links sent in Telegram. No JWT auth — admin_token is the credential.
"""
import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

User = get_user_model()


def _html_page(title: str, body: str, color: str = '#22c55e') -> HttpResponse:
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            display: flex; align-items: center; justify-content: center;
            min-height: 100vh; background: #f3f4f6; }}
    .card {{ text-align: center; padding: 2.5rem 2rem; background: white;
             border-radius: 16px; box-shadow: 0 4px 24px rgba(0,0,0,.08);
             max-width: 420px; width: 90%; }}
    .icon {{ font-size: 3rem; margin-bottom: 1rem; }}
    h1 {{ color: {color}; font-size: 1.5rem; margin-bottom: .75rem; }}
    p  {{ color: #6b7280; font-size: .95rem; line-height: 1.6; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{'✅' if color == '#22c55e' else '❌' if color == '#ef4444' else '⚠️'}</div>
    <h1>{title}</h1>
    <p>{body}</p>
  </div>
</body>
</html>"""
    return HttpResponse(html)


class YapeActivateView(APIView):
    """Admin clicks the approve link from Telegram → activates the tenant account."""
    permission_classes     = [AllowAny]
    authentication_classes = []

    def get(self, request, token: str):
        from apps.subscriptions.models import YapePaymentProof

        try:
            proof = YapePaymentProof.objects.select_related(
                'subscription__tenant'
            ).get(admin_token=token)
        except YapePaymentProof.DoesNotExist:
            return _html_page('Enlace inválido', 'Este enlace no existe o ya no es válido.', '#ef4444')

        if proof.status != 'pending':
            label = 'aprobado' if proof.status == 'approved' else 'rechazado'
            return _html_page(
                'Ya procesado',
                f'Este comprobante ya fue {label} anteriormente.',
                '#f59e0b',
            )

        tenant       = proof.subscription.tenant
        subscription = proof.subscription

        with transaction.atomic():
            subscription.plan                 = proof.plan
            subscription.status               = 'active'
            subscription.current_period_start = timezone.now()
            subscription.trial_start          = None
            subscription.trial_end            = None
            subscription.save(update_fields=[
                'plan', 'status', 'current_period_start',
                'trial_start', 'trial_end', 'updated_at',
            ])

            tenant.plan      = proof.plan
            tenant.is_active = True
            tenant.save(update_fields=['plan', 'is_active', 'updated_at'])

            User.objects.filter(tenant=tenant).update(is_active=True)

            proof.status      = 'approved'
            proof.reviewed_at = timezone.now()
            proof.save(update_fields=['status', 'reviewed_at', 'updated_at'])

        owner = tenant.users.order_by('created_at').first()
        if owner:
            hub_url = getattr(settings, 'FRONTEND_HUB_URL', '').rstrip('/')
            send_mail(
                subject='¡Tu cuenta ha sido activada!',
                message=(
                    f"Hola {owner.name},\n\n"
                    f"Tu pago Yape fue verificado exitosamente. "
                    f"Tu plan {proof.plan.capitalize()} ya está activo.\n\n"
                    f"Ingresa a tu cuenta: {hub_url}/login\n\n"
                    f"Saludos,\nEl equipo"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[owner.email],
                fail_silently=True,
            )
            logger.info(
                'YapeActivate: proof %s approved, email sent to %s', proof.id, owner.email
            )

        return _html_page(
            'Cuenta Activada',
            f'La cuenta de <strong>{tenant.name}</strong> fue activada con el plan '
            f'<strong>{proof.plan.capitalize()}</strong>. Se notificó al usuario por email.',
        )


class YapeRejectView(APIView):
    """Admin clicks the reject link from Telegram → marks account as rejected."""
    permission_classes     = [AllowAny]
    authentication_classes = []

    def get(self, request, token: str):
        from apps.subscriptions.models import YapePaymentProof

        try:
            proof = YapePaymentProof.objects.select_related(
                'subscription__tenant'
            ).get(admin_token=token)
        except YapePaymentProof.DoesNotExist:
            return _html_page('Enlace inválido', 'Este enlace no existe o ya no es válido.', '#ef4444')

        if proof.status != 'pending':
            label = 'aprobado' if proof.status == 'approved' else 'rechazado'
            return _html_page(
                'Ya procesado',
                f'Este comprobante ya fue {label} anteriormente.',
                '#f59e0b',
            )

        tenant       = proof.subscription.tenant
        subscription = proof.subscription

        with transaction.atomic():
            subscription.status = 'canceled'
            subscription.save(update_fields=['status', 'updated_at'])

            proof.status      = 'rejected'
            proof.reviewed_at = timezone.now()
            proof.save(update_fields=['status', 'reviewed_at', 'updated_at'])

        owner = tenant.users.order_by('created_at').first()
        if owner:
            send_mail(
                subject='Tu pago Yape no pudo ser verificado',
                message=(
                    f"Hola {owner.name},\n\n"
                    f"Lamentablemente no pudimos verificar tu comprobante de pago Yape "
                    f"para el plan {proof.plan.capitalize()}.\n\n"
                    f"Por favor contáctanos respondiendo este email para resolver tu caso.\n\n"
                    f"Saludos,\nEl equipo"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[owner.email],
                fail_silently=True,
            )
            logger.info(
                'YapeReject: proof %s rejected, email sent to %s', proof.id, owner.email
            )

        return _html_page(
            'Pago Rechazado',
            f'La solicitud de <strong>{tenant.name}</strong> fue rechazada. '
            f'Se notificó al usuario por email.',
            '#ef4444',
        )
