"""
Serializers for the Calendar module.
"""
from rest_framework import serializers

from apps.calendar_app.models import CalendarEvent, EventAttendee


class CalendarEventSerializer(serializers.ModelSerializer):
    attendees_count = serializers.SerializerMethodField()
    start_date = serializers.SerializerMethodField()
    end_date = serializers.SerializerMethodField()

    class Meta:
        model = CalendarEvent
        fields = [
            'id', 'title', 'description', 'start_datetime', 'end_datetime',
            'start_date', 'end_date',
            'is_all_day', 'location', 'rrule', 'color',
            'attendees_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_attendees_count(self, obj) -> int:
        return obj.attendees.count()

    def get_start_date(self, obj) -> str:
        return obj.start_datetime.strftime('%Y-%m-%dT%H:%M:%S') if obj.start_datetime else ''

    def get_end_date(self, obj) -> str:
        return obj.end_datetime.strftime('%Y-%m-%dT%H:%M:%S') if obj.end_datetime else ''


class CalendarEventCreateUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    start_datetime = serializers.DateTimeField()
    end_datetime = serializers.DateTimeField()
    is_all_day = serializers.BooleanField(required=False, default=False)
    location = serializers.CharField(required=False, allow_blank=True, default='', max_length=500)
    rrule = serializers.CharField(required=False, allow_blank=True, default='')
    color = serializers.CharField(required=False, max_length=20, default='blue')

    def validate(self, attrs):
        if attrs.get('end_datetime') and attrs.get('start_datetime'):
            if attrs['end_datetime'] < attrs['start_datetime']:
                raise serializers.ValidationError(
                    {'end_datetime': 'end_datetime must be greater than or equal to start_datetime.'}
                )
        return attrs


class EventAttendeeSerializer(serializers.ModelSerializer):
    user_id = serializers.UUIDField(source='user.id', read_only=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = EventAttendee
        fields = ['id', 'user_id', 'user_name', 'status', 'created_at']
        read_only_fields = ['id', 'created_at']

    def get_user_name(self, obj) -> str | None:
        if obj.user:
            return getattr(obj.user, 'name', str(obj.user))
        return None


class EventAttendeeCreateSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    status = serializers.ChoiceField(
        choices=EventAttendee.STATUS_CHOICES,
        required=False,
        default='invited',
    )
