"""
Serializers for the Analytics/Reports module.
No database models — all data is computed on demand.
"""
from rest_framework import serializers


class ResourceUsageSerializer(serializers.Serializer):
    name = serializers.CharField()
    used = serializers.IntegerField()
    limit = serializers.IntegerField(allow_null=True)
    percent = serializers.FloatField(allow_null=True)


class SummarySerializer(serializers.Serializer):
    period_days = serializers.IntegerField()
    active_users = serializers.IntegerField()
    total_users = serializers.IntegerField()
    total_projects = serializers.IntegerField()
    total_notes = serializers.IntegerField()
    total_contacts = serializers.IntegerField()
    total_bookmarks = serializers.IntegerField()
    total_snippets = serializers.IntegerField()
    total_forms = serializers.IntegerField()
    audit_events_period = serializers.IntegerField()


class UsageSerializer(serializers.Serializer):
    plan = serializers.CharField()
    resources = ResourceUsageSerializer(many=True)


class TrendEntrySerializer(serializers.Serializer):
    date = serializers.DateField()
    events = serializers.IntegerField()


class TrendsSerializer(serializers.Serializer):
    trends = TrendEntrySerializer(many=True)
