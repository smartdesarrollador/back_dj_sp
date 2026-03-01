"""
Initial migration for support app — SupportTicket + TicketComment models.
"""
import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('tenants', '0002_rename_tenants_slug_idx_tenants_slug_3181c2_idx_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='SupportTicket',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('reference', models.CharField(blank=True, db_index=True, max_length=20, unique=True)),
                ('subject', models.CharField(max_length=255)),
                ('description', models.TextField()),
                ('category', models.CharField(
                    choices=[
                        ('technical', 'Técnico'),
                        ('billing', 'Facturación'),
                        ('access', 'Acceso'),
                        ('feature_request', 'Solicitud'),
                        ('other', 'Otro'),
                    ],
                    max_length=30,
                )),
                ('priority', models.CharField(
                    choices=[
                        ('urgente', 'Urgente'),
                        ('alta', 'Alta'),
                        ('media', 'Media'),
                        ('baja', 'Baja'),
                    ],
                    default='media',
                    max_length=10,
                )),
                ('status', models.CharField(
                    choices=[
                        ('open', 'Abierto'),
                        ('in_progress', 'En Progreso'),
                        ('waiting_client', 'Esperando Cliente'),
                        ('resolved', 'Resuelto'),
                        ('closed', 'Cerrado'),
                    ],
                    default='open',
                    max_length=20,
                )),
                ('client_email', models.EmailField(blank=True, max_length=254)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('tenant', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='support_tickets',
                    to='tenants.tenant',
                )),
                ('client', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='submitted_tickets',
                    to=settings.AUTH_USER_MODEL,
                )),
                ('assigned_to', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='assigned_support_tickets',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'db_table': 'support_tickets',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='supportticket',
            index=models.Index(
                fields=['tenant', 'status'],
                name='support_tickets_tenant_status_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='supportticket',
            index=models.Index(
                fields=['tenant', 'priority'],
                name='support_tickets_tenant_priority_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='supportticket',
            index=models.Index(
                fields=['tenant', 'client'],
                name='support_tickets_tenant_client_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='supportticket',
            index=models.Index(
                fields=['assigned_to', 'status'],
                name='support_tickets_assigned_status_idx',
            ),
        ),
        migrations.CreateModel(
            name='TicketComment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('author', models.CharField(max_length=255)),
                ('role', models.CharField(
                    choices=[('client', 'Cliente'), ('agent', 'Agente')],
                    max_length=10,
                )),
                ('message', models.TextField()),
                ('ticket', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='comments',
                    to='support.supportticket',
                )),
            ],
            options={
                'db_table': 'ticket_comments',
                'ordering': ['created_at'],
            },
        ),
    ]
