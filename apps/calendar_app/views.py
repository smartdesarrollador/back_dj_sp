"""
Calendar views — events with recurrence and attendees.

URL namespace: /api/v1/app/calendar/

Endpoints:
  GET    /app/calendar/                              → list events (?start= ?end=)
  POST   /app/calendar/                              → create event
  GET    /app/calendar/<pk>/                         → event detail
  PATCH  /app/calendar/<pk>/                         → update event
  DELETE /app/calendar/<pk>/                         → delete event
  GET    /app/calendar/<event_pk>/attendees/         → list attendees
  POST   /app/calendar/<event_pk>/attendees/         → add attendee (owner only)
  DELETE /app/calendar/<event_pk>/attendees/<user_pk>/ → remove attendee
"""
from django.contrib.auth import get_user_model
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.calendar_app.models import CalendarEvent, EventAttendee
from apps.calendar_app.serializers import (
    CalendarEventCreateUpdateSerializer,
    CalendarEventSerializer,
    EventAttendeeCreateSerializer,
    EventAttendeeSerializer,
)
from apps.rbac.permissions import HasFeature, HasPermission, check_plan_limit, _user_has_permission
from core.mixins import AuditMixin
from utils.plans import get_plan_limit

User = get_user_model()

_IMPORT_MAX_ROWS = 1000

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)


def _get_event(pk, tenant, user):
    try:
        return CalendarEvent.objects.get(pk=pk, tenant=tenant, user=user)
    except CalendarEvent.DoesNotExist:
        return None


def _get_visible_event(pk, tenant, user):
    """Read-only lookup: event visible to its owner or a confirmed attendee."""
    try:
        return (
            CalendarEvent.objects.filter(Q(user=user) | Q(attendees__user=user))
            .select_related('user')
            .distinct()
            .get(pk=pk, tenant=tenant)
        )
    except CalendarEvent.DoesNotExist:
        return None


def _get_event_for_tenant(pk, tenant):
    """Get event by tenant regardless of owner (used for attendee views)."""
    try:
        return CalendarEvent.objects.get(pk=pk, tenant=tenant)
    except CalendarEvent.DoesNotExist:
        return None


class CalendarEventListCreateView(APIView):
    permission_classes = [HasPermission('calendar.read')]

    @extend_schema(
        tags=['app-calendar'],
        summary='List calendar events',
        parameters=[
            OpenApiParameter('start', OpenApiTypes.DATETIME, description='Filter events from this datetime'),
            OpenApiParameter('end', OpenApiTypes.DATETIME, description='Filter events up to this datetime'),
        ],
    )
    def get(self, request):
        qs = (
            CalendarEvent.objects.filter(tenant=request.tenant)
            .filter(Q(user=request.user) | Q(attendees__user=request.user))
            .select_related('user')
            .distinct()
        )
        start = request.query_params.get('start')
        end = request.query_params.get('end')
        month = request.query_params.get('month')
        if month:
            try:
                year, mon = month.split('-')
                import calendar as cal_mod
                last_day = cal_mod.monthrange(int(year), int(mon))[1]
                qs = qs.filter(
                    start_datetime__gte=f'{year}-{mon}-01T00:00:00',
                    start_datetime__lte=f'{year}-{mon}-{last_day}T23:59:59',
                )
            except (ValueError, AttributeError):
                pass
        if start:
            qs = qs.filter(start_datetime__gte=start)
        if end:
            qs = qs.filter(end_datetime__lte=end)
        events = CalendarEventSerializer(qs, many=True, context={'request': request}).data
        return Response({'results': events, 'count': len(events), 'events': events})

    @extend_schema(tags=['app-calendar'], summary='Create calendar event')
    def post(self, request):
        if not _user_has_permission(request.user, 'calendar.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = CalendarEvent.objects.filter(
            tenant=request.tenant, user=request.user
        ).count()
        check_plan_limit(request.user, 'calendar_events', count)
        serializer = CalendarEventCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        event = CalendarEvent.objects.create(
            tenant=request.tenant,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(
            CalendarEventSerializer(event, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class CalendarImportView(AuditMixin, APIView):
    """Bulk import calendar events from a parsed file (client sends validated JSON rows)."""

    permission_classes = [HasPermission('calendar.create'), HasFeature('calendar_import')]

    @extend_schema(tags=['app-calendar'], summary='Bulk import calendar events')
    def post(self, request):
        items = request.data.get('items')
        if not isinstance(items, list):
            return Response(
                {'error': {'code': 'invalid', 'message': '"items" debe ser una lista.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(items) > _IMPORT_MAX_ROWS:
            return Response(
                {'error': {'code': 'too_many', 'message': f'Máximo {_IMPORT_MAX_ROWS} filas por importación.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        valid: list[dict] = []
        errors: list[dict] = []
        for idx, raw in enumerate(items):
            serializer = CalendarEventCreateUpdateSerializer(data=raw if isinstance(raw, dict) else {})
            if not serializer.is_valid():
                errors.append({'index': idx, 'errors': serializer.errors})
                continue
            valid.append(dict(serializer.validated_data))

        current = CalendarEvent.objects.filter(tenant=request.tenant, user=request.user).count()
        plan = getattr(request.tenant, 'plan', 'free')
        limit = get_plan_limit(plan, 'calendar_events')
        allowed = len(valid) if limit is None else max(0, limit - current)
        to_create = valid[:allowed]
        skipped = len(valid) - len(to_create)

        created = len(CalendarEvent.objects.bulk_create(
            [CalendarEvent(tenant=request.tenant, user=request.user, **d) for d in to_create]
        ))

        self.log_action(
            request,
            action='calendar.import',
            resource_type='CalendarEvent',
            extra={
                'created': created,
                'skipped': skipped,
                'errors': len(errors),
                'source': request.data.get('source', ''),
            },
        )
        return Response({'created': created, 'skipped': skipped, 'errors': errors})


class CalendarEventDetailView(AuditMixin, APIView):
    permission_classes = [HasPermission('calendar.read')]

    @extend_schema(tags=['app-calendar'], summary='Get calendar event detail')
    def get(self, request, pk):
        event = _get_visible_event(pk, request.tenant, request.user)
        if not event:
            return _NOT_FOUND
        return Response({'event': CalendarEventSerializer(event, context={'request': request}).data})

    @extend_schema(tags=['app-calendar'], summary='Update calendar event')
    def patch(self, request, pk):
        if not _user_has_permission(request.user, 'calendar.update'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        event = _get_event(pk, request.tenant, request.user)
        if not event:
            return _NOT_FOUND
        serializer = CalendarEventCreateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(event, field, value)
        # Re-validate dates after partial update
        if event.end_datetime < event.start_datetime:
            return Response(
                {'error': {'code': 'invalid_dates', 'message': 'end_datetime must be >= start_datetime.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        event.save()
        return Response(CalendarEventSerializer(event, context={'request': request}).data)

    @extend_schema(tags=['app-calendar'], summary='Delete calendar event')
    def delete(self, request, pk):
        if not _user_has_permission(request.user, 'calendar.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        event = _get_event(pk, request.tenant, request.user)
        if not event:
            return _NOT_FOUND
        self.log_action(request, 'delete', 'calendar_event', str(event.pk))
        event.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EventAttendeeListView(APIView):
    permission_classes = [HasPermission('calendar.read'), HasFeature('calendar_attendees')]

    @extend_schema(tags=['app-calendar'], summary='List event attendees')
    def get(self, request, event_pk):
        event = _get_event_for_tenant(event_pk, request.tenant)
        if not event:
            return _NOT_FOUND
        attendees = EventAttendee.objects.filter(event=event).select_related('user')
        return Response({'attendees': EventAttendeeSerializer(attendees, many=True).data})

    @extend_schema(tags=['app-calendar'], summary='Add attendee to event')
    def post(self, request, event_pk):
        event = _get_event_for_tenant(event_pk, request.tenant)
        if not event:
            return _NOT_FOUND
        # Only the event owner can invite attendees
        if event.user != request.user and not _user_has_permission(request.user, 'calendar.share'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        serializer = EventAttendeeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = User.objects.get(
                pk=serializer.validated_data['user_id'],
                tenant=request.tenant,
            )
        except User.DoesNotExist:
            return Response(
                {'error': {'code': 'not_found', 'message': 'User not found.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        attendee, created = EventAttendee.objects.get_or_create(
            event=event,
            user=user,
            defaults={'status': serializer.validated_data.get('status', 'invited')},
        )
        resp_status = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(EventAttendeeSerializer(attendee).data, status=resp_status)


class EventAttendeeDetailView(APIView):
    permission_classes = [HasPermission('calendar.read'), HasFeature('calendar_attendees')]

    @extend_schema(tags=['app-calendar'], summary='Remove attendee from event')
    def delete(self, request, event_pk, user_pk):
        event = _get_event_for_tenant(event_pk, request.tenant)
        if not event:
            return _NOT_FOUND
        # Only the event owner or the attendee themselves can remove
        if event.user != request.user and str(request.user.pk) != str(user_pk):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        try:
            attendee = EventAttendee.objects.get(event=event, user__pk=user_pk)
        except EventAttendee.DoesNotExist:
            return _NOT_FOUND
        attendee.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
