# Migration for PASO 16 — Tasks module

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
            name='TaskBoard',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='task_boards', to='tenants.tenant')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_boards', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'task_boards',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['tenant', 'created_at'], name='task_boards_tenant_created_idx')],
            },
        ),
        migrations.CreateModel(
            name='Task',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('title', models.CharField(max_length=500)),
                ('description', models.TextField(blank=True)),
                ('status', models.CharField(
                    choices=[('todo', 'To Do'), ('in_progress', 'In Progress'), ('review', 'In Review'), ('done', 'Done')],
                    default='todo',
                    max_length=20,
                )),
                ('priority', models.CharField(
                    choices=[('low', 'Low'), ('medium', 'Medium'), ('high', 'High'), ('urgent', 'Urgent')],
                    default='medium',
                    max_length=10,
                )),
                ('due_date', models.DateField(blank=True, null=True)),
                ('order', models.PositiveIntegerField(db_index=True, default=0)),
                ('board', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='tasks.taskboard')),
                ('parent_task', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='subtasks', to='tasks.task')),
                ('assignee', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_tasks', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_tasks', to=settings.AUTH_USER_MODEL)),
                ('tenant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='tenants.tenant')),
            ],
            options={
                'db_table': 'tasks',
                'ordering': ['order', 'created_at'],
                'indexes': [
                    models.Index(fields=['tenant', 'board', 'status'], name='tasks_tenant_board_status_idx'),
                    models.Index(fields=['tenant', 'assignee'], name='tasks_tenant_assignee_idx'),
                    models.Index(fields=['tenant', 'due_date'], name='tasks_tenant_due_date_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='TaskComment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('content', models.TextField()),
                ('task', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comments', to='tasks.task')),
                ('user', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='task_comments', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'task_comments',
                'ordering': ['created_at'],
            },
        ),
    ]
