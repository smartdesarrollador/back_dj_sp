from django.urls import path

from apps.calendar_app.views import (
    CalendarEventDetailView,
    CalendarEventListCreateView,
    EventAttendeeDetailView,
    EventAttendeeListView,
)

urlpatterns = [
    path('', CalendarEventListCreateView.as_view(), name='calendar-event-list-create'),
    path('<uuid:pk>/', CalendarEventDetailView.as_view(), name='calendar-event-detail'),
    path('<uuid:event_pk>/attendees/', EventAttendeeListView.as_view(), name='event-attendee-list'),
    path('<uuid:event_pk>/attendees/<uuid:user_pk>/', EventAttendeeDetailView.as_view(), name='event-attendee-detail'),
]
