"""
Public one-click endpoints for admin Yape payment approval/rejection.
Accessed via links sent in Telegram. No JWT auth — admin_token is the credential.

GET  → confirmation page (safe for link-preview bots / crawlers)
POST → perform the actual action (approve or reject)
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

from .services import activate_yape_proof

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
    .btn {{ display: inline-block; margin-top: 1.5rem; padding: .75rem 2rem;
            border: none; border-radius: 8px; font-size: 1rem; font-weight: 600;
            cursor: pointer; color: white; background: {color}; }}
    .btn:hover {{ opacity: .85; }}
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


def _confirm_page(title: str, body: str, action_url: str,
                  btn_label: str, color: str) -> HttpResponse:
    """Confirmation page with a POST button — safe for link-preview bots."""
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
    h1 {{ color: #111827; font-size: 1.5rem; margin-bottom: .75rem; }}
    p  {{ color: #6b7280; font-size: .95rem; line-height: 1.6; margin-bottom: 1.5rem; }}
    form button {{ display: block; width: 100%; padding: .85rem;
                  border: none; border-radius: 8px; font-size: 1rem; font-weight: 700;
                  cursor: pointer; color: white; background: {color}; }}
    form button:hover {{ opacity: .85; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">{'✅' if color == '#22c55e' else '❌'}</div>
    <h1>{title}</h1>
    <p>{body}</p>
    <form method="POST" action="{action_url}">
      <button type="submit">{btn_label}</button>
    </form>
  </div>
</body>
</html>"""
    return HttpResponse(html)


def _get_proof(token: str):
    from apps.subscriptions.models import YapePaymentProof
    try:
        return YapePaymentProof.objects.select_related(
            'subscription__tenant'
        ).get(admin_token=token), None
    except YapePaymentProof.DoesNotExist:
        return None, _html_page('Enlace inválido', 'Este enlace no existe o ya no es válido.', '#ef4444')


def _already_processed(proof) -> HttpResponse | None:
    if proof.status != 'pending':
        label = 'aprobado' if proof.status == 'approved' else 'rechazado'
        return _html_page('Ya procesado', f'Este comprobante ya fue {label} anteriormente.', '#f59e0b')
    return None


class YapeActivateView(APIView):
    """
    GET  → Confirmation page (safe: link-preview bots hit this but nothing happens).
    POST → Activates the tenant subscription to the paid plan.
    """
    permission_classes     = [AllowAny]
    authentication_classes = []

    def get(self, request, token: str):
        proof, err = _get_proof(token)
        if err:
            return err
        if done := _already_processed(proof):
            return done

        tenant = proof.subscription.tenant
        return _confirm_page(
            title='Aprobar pago Yape',
            body=(
                f'¿Confirmas que el pago de <strong>{tenant.name}</strong> '
                f'es válido y deseas activar el plan '
                f'<strong>{proof.plan.capitalize()}</strong>?'
            ),
            action_url=request.path,
            btn_label='✅ Confirmar aprobación',
            color='#22c55e',
        )

    def post(self, request, token: str):
        proof, err = _get_proof(token)
        if err:
            return err
        if done := _already_processed(proof):
            return done

        tenant = proof.subscription.tenant

        activate_yape_proof(proof)

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
            logger.info('YapeActivate: proof %s approved, email sent to %s', proof.id, owner.email)

        return _html_page(
            'Cuenta Activada',
            f'La cuenta de <strong>{tenant.name}</strong> fue activada con el plan '
            f'<strong>{proof.plan.capitalize()}</strong>. Se notificó al usuario por email.',
        )


class YapeRejectView(APIView):
    """
    GET  → Confirmation page (safe: link-preview bots hit this but nothing happens).
    POST → Marks the proof as rejected; tenant stays on Free plan.
    """
    permission_classes     = [AllowAny]
    authentication_classes = []

    def get(self, request, token: str):
        proof, err = _get_proof(token)
        if err:
            return err
        if done := _already_processed(proof):
            return done

        tenant = proof.subscription.tenant
        return _confirm_page(
            title='Rechazar pago Yape',
            body=(
                f'¿Confirmas que el comprobante de <strong>{tenant.name}</strong> '
                f'para el plan <strong>{proof.plan.capitalize()}</strong> NO es válido?'
            ),
            action_url=request.path,
            btn_label='❌ Confirmar rechazo',
            color='#ef4444',
        )

    def post(self, request, token: str):
        proof, err = _get_proof(token)
        if err:
            return err
        if done := _already_processed(proof):
            return done

        tenant       = proof.subscription.tenant
        subscription = proof.subscription
        hub_url      = getattr(settings, 'FRONTEND_HUB_URL', '').rstrip('/')

        with transaction.atomic():
            subscription.plan   = 'free'
            subscription.status = 'active'
            subscription.save(update_fields=['plan', 'status', 'updated_at'])
            tenant.plan = 'free'
            tenant.save(update_fields=['plan', 'updated_at'])
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
                    f"Tu cuenta continúa activa con el plan Free. "
                    f"Si deseas intentarlo de nuevo o tienes dudas, "
                    f"contáctanos respondiendo este email.\n\n"
                    f"Ingresa a tu cuenta: {hub_url}/login\n\n"
                    f"Saludos,\nEl equipo"
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[owner.email],
                fail_silently=True,
            )
            logger.info('YapeReject: proof %s rejected, email sent to %s', proof.id, owner.email)

        return _html_page(
            'Pago Rechazado',
            f'La solicitud de <strong>{tenant.name}</strong> fue rechazada. '
            f'El usuario continúa con el plan Free. Se notificó por email.',
            '#ef4444',
        )
