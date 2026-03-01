"""
Calendar models — events with RRULE recurrence and attendees.
"""
from django.conf import settings
from django.db import models

from core.models import BaseModel


class CalendarEvent(BaseModel):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='calendar_events',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='calendar_events',
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    start_datetime = models.DateTimeField(db_index=True)
    end_datetime = models.DateTimeField()
    is_all_day = models.BooleanField(default=False)
    location = models.CharField(max_length=500, blank=True)
    rrule = models.TextField(blank=True)  # iCal RRULE string (e.g. "FREQ=WEEKLY;BYDAY=MO")
    color = models.CharField(max_length=20, default='blue')

    class Meta:
        db_table = 'calendar_events'
        ordering = ['start_datetime']
        indexes = [
            models.Index(fields=['tenant', 'user', 'start_datetime'], name='cal_events_tenant_user_start_idx'),
            models.Index(fields=['tenant', 'user', 'end_datetime'], name='cal_events_tenant_user_end_idx'),
        ]

    def __str__(self) -> str:
        return self.title


class EventAttendee(BaseModel):
    STATUS_CHOICES = [
        ('invited', 'Invited'),
        ('accepted', 'Accepted'),
        ('declined', 'Declined'),
        ('maybe', 'Maybe'),
    ]

    event = models.ForeignKey(
        CalendarEvent,
        on_delete=models.CASCADE,
        related_name='attendees',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='event_attendances',
    )
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='invited')

    class Meta:
        db_table = 'calendar_event_attendees'
        unique_together = [['event', 'user']]

    def __str__(self) -> str:
        return f'{self.user} → {self.event} ({self.status})'
