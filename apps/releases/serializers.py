import os

from django.urls import reverse
from rest_framework import serializers

from apps.releases.models import ALLOWED_EXTENSIONS, MAX_FILE_SIZE_BYTES, DesktopRelease


class DesktopReleaseSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    file_size_mb = serializers.SerializerMethodField()

    class Meta:
        model = DesktopRelease
        fields = [
            'id', 'version', 'platform', 'app_type', 'file_url', 'file_name', 'file_size',
            'file_size_mb', 'sha256', 'release_notes', 'is_published',
            'download_count', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_file_url(self, obj) -> str | None:
        request = self.context.get('request')
        if obj.file and request:
            download_path = reverse('public-release-download', kwargs={'pk': str(obj.id)})
            return request.build_absolute_uri(download_path)
        return None

    def get_file_size_mb(self, obj) -> float:
        return round(obj.file_size / (1024 * 1024), 2)


class DesktopReleaseCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DesktopRelease
        fields = ['version', 'platform', 'app_type', 'file', 'release_notes']

    def validate_file(self, value):
        ext = os.path.splitext(value.name)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise serializers.ValidationError(
                f'Extensión no permitida: {ext}. Solo se aceptan: '
                + ', '.join(sorted(ALLOWED_EXTENSIONS))
            )
        if value.size > MAX_FILE_SIZE_BYTES:
            size_mb = value.size / (1024 * 1024)
            raise serializers.ValidationError(
                f'El archivo supera el límite de 500 MB ({size_mb:.1f} MB).'
            )
        return value

    def validate(self, attrs):
        version = attrs.get('version')
        platform = attrs.get('platform')
        app_type = attrs.get('app_type', 'tauri')
        if DesktopRelease.objects.filter(version=version, platform=platform, app_type=app_type).exists():
            raise serializers.ValidationError(
                {'version': f'Ya existe un release {version} para {platform} ({app_type}).'}
            )
        return attrs


class DesktopReleaseUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = DesktopRelease
        fields = ['is_published', 'release_notes']
