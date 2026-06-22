import requests
from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Q
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contact.models import ContactMessage
from apps.contact.serializers import (
    ContactMessageCreateSerializer,
    ContactMessageSerializer,
    ContactMessageUpdateSerializer,
)


def _get_client_ip(request) -> str | None:
    x_fwd = request.META.get('HTTP_X_FORWARDED_FOR')
    return x_fwd.split(',')[0].strip() if x_fwd else request.META.get('REMOTE_ADDR')


def _verify_recaptcha(token: str) -> bool:
    """Verifica el token con Google. Sin clave configurada (dev), permite siempre."""
    if not settings.RECAPTCHA_SECRET_KEY:
        return True
    try:
        resp = requests.post(
            settings.RECAPTCHA_VERIFY_URL,
            data={'secret': settings.RECAPTCHA_SECRET_KEY, 'response': token},
            timeout=5,
        )
        data = resp.json()
        return bool(data.get('success')) and data.get('score', 0) >= settings.RECAPTCHA_MIN_SCORE
    except Exception:
        return False


def _send_confirmation_email(name: str, email: str) -> None:
    send_mail(
        subject='Recibimos tu mensaje — Hub de Servicios',
        message=(
            f'Hola {name},\n\n'
            'Gracias por contactarnos. Hemos recibido tu mensaje y te responderemos '
            'a la brevedad posible.\n\n'
            'Si tienes alguna consulta urgente, puedes escribirnos directamente a '
            f'{settings.DEFAULT_FROM_EMAIL}\n\n'
            'Saludos,\nEl equipo de Hub de Servicios'
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
        fail_silently=True,
    )


# ── Public ────────────────────────────────────────────────────────────────────

class PublicContactView(APIView):
    permission_classes     = [AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = ContactMessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if not _verify_recaptcha(data['recaptcha_token']):
            return Response(
                {'error': {'code': 'recaptcha_failed', 'message': 'Verificación fallida. Intenta de nuevo.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        msg = ContactMessage.objects.create(
            name=data['name'],
            email=data['email'],
            phone=data.get('phone', ''),
            message=data['message'],
            ip_address=_get_client_ip(request),
        )

        _send_confirmation_email(msg.name, msg.email)

        return Response({'detail': 'Mensaje recibido.'}, status=status.HTTP_201_CREATED)


# ── Admin ────────────────────────────────────────────────────────────────────

class AdminContactListView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request):
        qs = ContactMessage.objects.all()
        if s := request.query_params.get('status'):
            qs = qs.filter(status=s)
        if q := request.query_params.get('search'):
            qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q))
        return Response({'messages': ContactMessageSerializer(qs, many=True).data})


class AdminContactDetailView(APIView):
    permission_classes = [IsAdminUser]

    def patch(self, request, pk):
        try:
            msg = ContactMessage.objects.get(pk=pk)
        except ContactMessage.DoesNotExist:
            return Response({'error': 'Not found.'}, status=status.HTTP_404_NOT_FOUND)
        s = ContactMessageUpdateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        msg.status = s.validated_data['status']
        msg.save(update_fields=['status', 'updated_at'])
        return Response({'message': ContactMessageSerializer(msg).data})
