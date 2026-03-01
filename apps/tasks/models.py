"""
Tasks models — Kanban boards with tasks, subtasks and comments.
"""
from django.conf import settings
from django.db import models

from core.models import BaseModel


class TaskBoard(BaseModel):
    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='task_boards',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_boards',
    )
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    class Meta:
        db_table = 'task_boards'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['tenant', 'created_at'], name='task_boards_tenant_created_idx'),
        ]

    def __str__(self) -> str:
        return self.name


class Task(BaseModel):
    STATUS_CHOICES = [
        ('todo', 'To Do'),
        ('in_progress', 'In Progress'),
        ('review', 'In Review'),
        ('done', 'Done'),
    ]
    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('urgent', 'Urgent'),
    ]

    tenant = models.ForeignKey(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='tasks',
    )
    board = models.ForeignKey(
        TaskBoard,
        on_delete=models.CASCADE,
        related_name='tasks',
    )
    parent_task = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='subtasks',
    )
    title = models.CharField(max_length=500)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='todo')
    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='medium')
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_tasks',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_tasks',
    )
    due_date = models.DateField(null=True, blank=True)
    order = models.PositiveIntegerField(default=0, db_index=True)

    class Meta:
        db_table = 'tasks'
        ordering = ['order', 'created_at']
        indexes = [
            models.Index(fields=['tenant', 'board', 'status'], name='tasks_tenant_board_status_idx'),
            models.Index(fields=['tenant', 'assignee'], name='tasks_tenant_assignee_idx'),
            models.Index(fields=['tenant', 'due_date'], name='tasks_tenant_due_date_idx'),
        ]

    def __str__(self) -> str:
        return self.title


class TaskComment(BaseModel):
    task = models.ForeignKey(
        Task,
        on_delete=models.CASCADE,
        related_name='comments',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='task_comments',
    )
    content = models.TextField()

    class Meta:
        db_table = 'task_comments'
        ordering = ['created_at']

    def __str__(self) -> str:
        return f'Comment on task {self.task_id}'
