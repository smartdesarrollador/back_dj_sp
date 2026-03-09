from rest_framework import serializers

from .models import Referral


class ReferralItemSerializer(serializers.ModelSerializer):
    tenant_name = serializers.CharField(source='referred.name', read_only=True)

    class Meta:
        model = Referral
        fields = ['id', 'tenant_name', 'status', 'credit_amount', 'activated_at', 'created_at']


class ReferralStatsSerializer(serializers.Serializer):
    referred = serializers.IntegerField()
    credits_earned = serializers.DecimalField(max_digits=10, decimal_places=2)
    available_credits = serializers.DecimalField(max_digits=10, decimal_places=2)


class ReferralDashboardSerializer(serializers.Serializer):
    code = serializers.CharField()
    referral_url = serializers.CharField()
    stats = ReferralStatsSerializer()
    referrals = ReferralItemSerializer(many=True)
