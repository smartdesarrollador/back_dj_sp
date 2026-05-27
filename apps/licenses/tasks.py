from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail


@shared_task(name='apps.licenses.tasks.send_license_key_email')
def send_license_key_email(user_id: str) -> dict:
    from django.contrib.auth import get_user_model
    from apps.licenses.models import DesktopAppLicense

    User = get_user_model()
    try:
        user = User.objects.get(pk=user_id)
        lic = DesktopAppLicense.objects.get(user=user, is_active=True)
    except (User.DoesNotExist, DesktopAppLicense.DoesNotExist):
        return {'sent': False, 'reason': 'user_or_license_not_found'}

    hub_url = getattr(settings, 'FRONTEND_HUB_URL', 'https://digisider.com')
    download_url = f"{hub_url}/desktop"

    subject = 'Tu License Key — Smart Sidebar Offline'
    body = (
        f"Hola {user.name},\n\n"
        f"Tu license key para la app Smart Sidebar Offline es:\n\n"
        f"    {lic.license_key}\n\n"
        f"Pasos para activarla:\n"
        f"  1. Descarga el instalador desde: {download_url}\n"
        f"  2. Instala y abre la aplicación\n"
        f"  3. Ingresa tu license key cuando se te solicite\n"
        f"  4. La app se activará automáticamente (requiere conexión a internet solo esta vez)\n\n"
        f"Una vez activada, la app funciona 100% offline.\n\n"
        f"Si tienes problemas, visita {download_url} o contacta soporte.\n\n"
        f"Saludos,\nEl equipo de Smart Digital Tec"
    )

    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=False,
    )
    return {'sent': True, 'to': user.email}
