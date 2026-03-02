# Migration for PASO 16 — Calendar module

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('tenants', '0002_rename_tenants_slug_idx_tenants_slug_3181c2_idx_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CalendarEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('start_datetime', models.DateTimeField(db_index=True)),
                ('end_datetime', models.DateTimeField()),
                ('is_all_day', models.BooleanField(default=False)),
                ('location', models.CharField(blank=True, max_length=500)),
                ('rrule', models.TextField(blank=True)),
                ('color', models.CharField(default='blue', max_length=20)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='calendar_events', to='tenants.tenant')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='calendar_events', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'calendar_events',
                'ordering': ['start_datetime'],
                'indexes': [
                    models.Index(fields=['tenant', 'user', 'start_datetime'], name='cal_evt_tnt_user_start_idx'),
                    models.Index(fields=['tenant', 'user', 'end_datetime'], name='cal_events_tenant_user_end_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='EventAttendee',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('status', models.CharField(
                    choices=[('invited', 'Invited'), ('accepted', 'Accepted'), ('declined', 'Declined'), ('maybe', 'Maybe')],
                    default='invited',
                    max_length=10,
                )),
                ('event', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attendees', to='calendar_app.calendarevent')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='event_attendances', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'calendar_event_attendees',
                'unique_together': {('event', 'user')},
            },
        ),
    ]
