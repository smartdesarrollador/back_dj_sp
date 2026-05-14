from rest_framework import serializers

from apps.licenses.models import DesktopAppLicense, _generate_license_key


def _mask_email(email: str) -> str:
    name, domain = email.split('@', 1)
    return f"{name[0]}***@{domain}"


def _mask_key(key: str) -> str:
    parts = key.split('-')
    return f"{parts[0]}-{parts[1]}-****-****"


class LicenseSerializer(serializers.ModelSerializer):
    license_key = serializers.SerializerMethodField()
    status = serializers.CharField(source='status', read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True)
    tenant_name = serializers.CharField(source='user.tenant.name', read_only=True)
    tenant_plan = serializers.CharField(source='user.tenant.plan', read_only=True)

    class Meta:
        model = DesktopAppLicense
        fields = [
            'id', 'user_email', 'tenant_name', 'tenant_plan',
            'license_key', 'status', 'hardware_id',
            'activated_at', 'expires_at', 'is_active', 'sent_at',
            'notes', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_license_key(self, obj) -> str:
        # Hub users see masked key; admins see full key (context flag)
        if self.context.get('admin_view'):
            return obj.license_key
        return _mask_key(obj.license_key)


class AdminLicenseSerializer(LicenseSerializer):
    license_key = serializers.CharField(source='license_key', read_only=True)

    def get_license_key(self, obj) -> str:
        return obj.license_key


class AdminCreateLicenseSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    expires_at = serializers.DateTimeField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True, default='')
    send_email = serializers.BooleanField(default=True)

    def validate_user_id(self, value):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            user = User.objects.select_related('tenant').get(pk=value)
        except User.DoesNotExist:
            raise serializers.ValidationError('Usuario no encontrado.')
        if DesktopAppLicense.objects.filter(user=user).exists():
            raise serializers.ValidationError('Este usuario ya tiene una licencia.')
        self._user = user
        return value

    def create(self, validated_data):
        user = self._user
        license_key = _generate_license_key()
        return DesktopAppLicense.objects.create(
            user=user,
            license_key=license_key,
            expires_at=validated_data.get('expires_at'),
            notes=validated_data.get('notes', ''),
            created_by=validated_data.get('created_by'),
        )


class AdminUpdateLicenseSerializer(serializers.ModelSerializer):
    class Meta:
        model = DesktopAppLicense
        fields = ['is_active', 'expires_at', 'notes']


class ActivateLicenseSerializer(serializers.Serializer):
    license_key = serializers.CharField(max_length=19)
    hardware_id = serializers.CharField(max_length=64)

    def validate_hardware_id(self, value):
        if len(value) != 64:
            raise serializers.ValidationError('hardware_id inválido.')
        return value
