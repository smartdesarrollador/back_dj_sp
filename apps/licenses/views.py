from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.licenses.models import PAID_PLANS, DesktopAppLicense, _generate_license_key
from apps.licenses.serializers import (
    ActivateLicenseSerializer,
    AdminCreateLicenseSerializer,
    AdminLicenseSerializer,
    AdminUpdateLicenseSerializer,
    LicenseSerializer,
    _mask_email,
)

_NOT_FOUND = {'error': {'code': 'not_found', 'message': 'Licencia no encontrada.'}}


# ── Hub App endpoints ─────────────────────────────────────────────────────────

class MyLicenseView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-licenses'], summary='Obtener licencia del usuario autenticado')
    def get(self, request):
        try:
            lic = DesktopAppLicense.objects.select_related('user__tenant').get(user=request.user)
        except DesktopAppLicense.DoesNotExist:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        return Response(LicenseSerializer(lic).data)


class RequestLicenseView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-licenses'], summary='Solicitar license key (genera y envía por email)')
    def post(self, request):
        user = request.user
        tenant_plan = getattr(user.tenant, 'plan', 'free')

        if tenant_plan not in PAID_PLANS:
            return Response(
                {'error': {'code': 'plan_required', 'message': 'Se requiere plan Starter o superior.'}},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )

        if DesktopAppLicense.objects.filter(user=user, is_active=True).exists():
            return Response(
                {'error': {'code': 'already_exists', 'message': 'Ya tienes una licencia activa. Usa reenviar.'}},
                status=status.HTTP_409_CONFLICT,
            )

        license_key = _generate_license_key()
        lic = DesktopAppLicense.objects.create(user=user, license_key=license_key)

        from apps.licenses.tasks import send_license_key_email
        send_license_key_email.delay(str(user.pk))

        lic.sent_at = timezone.now()
        lic.save(update_fields=['sent_at'])

        return Response({
            'sent_to': _mask_email(user.email),
            'message': 'Tu license key ha sido enviada a tu correo.',
        }, status=status.HTTP_201_CREATED)


class ResendLicenseView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=['app-licenses'], summary='Reenviar license key al correo del usuario')
    def post(self, request):
        try:
            lic = DesktopAppLicense.objects.get(user=request.user, is_active=True)
        except DesktopAppLicense.DoesNotExist:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)

        from apps.licenses.tasks import send_license_key_email
        send_license_key_email.delay(str(request.user.pk))

        lic.sent_at = timezone.now()
        lic.save(update_fields=['sent_at'])

        return Response({'sent_to': _mask_email(request.user.email)})


# ── Public endpoint (used by desktop app) ────────────────────────────────────

class ActivateLicenseView(APIView):
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['public-licenses'],
        summary='Activar licencia desde la app desktop',
        request=ActivateLicenseSerializer,
        responses={
            200: OpenApiResponse(description='{ activation_token, license_key, activated_at }'),
            400: OpenApiResponse(description='Datos inválidos'),
            404: OpenApiResponse(description='Licencia no encontrada'),
            409: OpenApiResponse(description='Hardware ID ya registrado en otra máquina'),
            410: OpenApiResponse(description='Licencia revocada o expirada'),
        },
    )
    def post(self, request):
        serializer = ActivateLicenseSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'error': {'code': 'validation_error', 'detail': serializer.errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        license_key = serializer.validated_data['license_key']
        hardware_id = serializer.validated_data['hardware_id']

        try:
            lic = DesktopAppLicense.objects.select_related('user').get(license_key=license_key)
        except DesktopAppLicense.DoesNotExist:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)

        if not lic.is_active:
            return Response(
                {'error': {'code': 'revoked', 'message': 'Esta licencia ha sido revocada.'}},
                status=status.HTTP_410_GONE,
            )

        now = timezone.now()
        if lic.expires_at and lic.expires_at < now:
            return Response(
                {'error': {'code': 'expired', 'message': 'Esta licencia ha expirado.'}},
                status=status.HTTP_410_GONE,
            )

        if lic.hardware_id and lic.hardware_id != hardware_id:
            return Response(
                {'error': {'code': 'hardware_mismatch', 'message': 'Esta licencia ya está activada en otro equipo.'}},
                status=status.HTTP_409_CONFLICT,
            )

        if not lic.hardware_id:
            lic.hardware_id = hardware_id
            lic.activated_at = now
            lic.save(update_fields=['hardware_id', 'activated_at'])

        token = lic.build_activation_token(hardware_id)

        return Response({
            'activation_token': token,
            'license_key': lic.license_key,
            'activated_at': lic.activated_at.isoformat(),
        })


# ── Admin endpoints ───────────────────────────────────────────────────────────

class AdminLicenseListView(APIView):
    permission_classes = [IsAdminUser]

    @extend_schema(tags=['admin-licenses'], summary='Listar todas las licencias')
    def get(self, request):
        qs = DesktopAppLicense.objects.select_related('user__tenant').order_by('-created_at')
        if search := request.query_params.get('search'):
            qs = qs.filter(user__email__icontains=search) | qs.filter(license_key__icontains=search)
        if s := request.query_params.get('status'):
            if s == 'active':
                qs = qs.filter(is_active=True, hardware_id__gt='')
            elif s == 'pending':
                qs = qs.filter(is_active=True, hardware_id='')
            elif s == 'revoked':
                qs = qs.filter(is_active=False)
        if plan := request.query_params.get('plan'):
            qs = qs.filter(user__tenant__plan=plan)
        serializer = AdminLicenseSerializer(qs, many=True, context={'admin_view': True})
        return Response({'licenses': serializer.data, 'total': qs.count()})

    @extend_schema(tags=['admin-licenses'], summary='Crear licencia manualmente')
    def post(self, request):
        serializer = AdminCreateLicenseSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'error': {'code': 'validation_error', 'detail': serializer.errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        lic = serializer.save(created_by=request.user)

        if serializer.validated_data.get('send_email', True):
            from apps.licenses.tasks import send_license_key_email
            send_license_key_email.delay(str(lic.user_id))
            lic.sent_at = timezone.now()
            lic.save(update_fields=['sent_at'])

        return Response(
            AdminLicenseSerializer(lic, context={'admin_view': True}).data,
            status=status.HTTP_201_CREATED,
        )


class AdminLicenseDetailView(APIView):
    permission_classes = [IsAdminUser]

    def _get(self, pk):
        try:
            return DesktopAppLicense.objects.select_related('user__tenant').get(pk=pk)
        except DesktopAppLicense.DoesNotExist:
            return None

    @extend_schema(tags=['admin-licenses'], summary='Actualizar licencia (revocar, extender, notas)')
    def patch(self, request, pk):
        lic = self._get(pk)
        if lic is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        serializer = AdminUpdateLicenseSerializer(lic, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'error': {'code': 'validation_error', 'detail': serializer.errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return Response(AdminLicenseSerializer(lic, context={'admin_view': True}).data)

    @extend_schema(tags=['admin-licenses'], summary='Eliminar licencia')
    def delete(self, request, pk):
        lic = self._get(pk)
        if lic is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        lic.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
