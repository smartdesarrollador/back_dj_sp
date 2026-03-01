"""
Serializers for the SSL Certs module.
"""
from rest_framework import serializers

from apps.ssl_certs.models import SSLCertificate


class SSLCertificateSerializer(serializers.ModelSerializer):
    status = serializers.SerializerMethodField()
    days_until_expiry = serializers.SerializerMethodField()

    class Meta:
        model = SSLCertificate
        fields = [
            'id', 'domain', 'issuer', 'valid_from', 'valid_until',
            'status', 'days_until_expiry',
            'alert_30_sent', 'alert_7_sent', 'alert_1_sent',
            'created_at', 'updated_at',
        ]
        read_only_fields = [
            'id', 'status', 'days_until_expiry', 'created_at', 'updated_at',
        ]

    def get_status(self, obj) -> str:
        return obj.status

    def get_days_until_expiry(self, obj) -> int | None:
        return obj.days_until_expiry


class SSLCertificateCreateUpdateSerializer(serializers.Serializer):
    domain = serializers.CharField(max_length=255)
    issuer = serializers.CharField(required=False, allow_blank=True, default='')
    valid_from = serializers.DateField(required=False, allow_null=True, default=None)
    valid_until = serializers.DateField(required=False, allow_null=True, default=None)
    certificate_pem = serializers.CharField(required=False, allow_blank=True, default='')
