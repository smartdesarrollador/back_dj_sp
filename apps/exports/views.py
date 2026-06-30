"""
Workspace data export — full account backup as a ZIP of JSON files.

URL namespace: /api/v1/app/workspace/

Endpoints:
  GET /app/workspace/backup/  → ZIP backup (HasFeature full_backup)

Security:
  - Tenant isolation: every queryset is filtered by request.tenant + request.user.
  - Secrets are NEVER exported in cleartext. Encrypted ProjectItemField values
    (field_type='password', is_encrypted=True) are emitted as a masked placeholder.
    Env vars, SSH keys, SSL private keys and Vault items are excluded entirely.
  - The action is recorded in the audit log via AuditMixin.
"""
import io
import json
import zipfile

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.bookmarks.models import Bookmark
from apps.calendar_app.models import CalendarEvent
from apps.contacts.models import Contact
from apps.notes.models import Note
from apps.projects.models import Project
from apps.rbac.permissions import HasFeature
from apps.snippets.models import CodeSnippet
from apps.tasks.models import Task
from core.mixins import AuditMixin

MASKED_SECRET = '***ENCRYPTED***'


def _ts(value) -> str | None:
    return value.isoformat() if value else None


def _notes(tenant, user) -> list[dict]:
    return [
        {
            'id': str(n.id),
            'title': n.title,
            'content': n.content,
            'category': n.category,
            'is_pinned': n.is_pinned,
            'color': n.color,
            'created_at': _ts(n.created_at),
            'updated_at': _ts(n.updated_at),
        }
        for n in Note.objects.filter(tenant=tenant, user=user)
    ]


def _tasks(tenant, user) -> list[dict]:
    qs = Task.objects.filter(tenant=tenant, created_by=user).select_related('board')
    return [
        {
            'id': str(t.id),
            'title': t.title,
            'description': t.description,
            'status': t.status,
            'priority': t.priority,
            'board': t.board.name if t.board_id else None,
            'due_date': t.due_date.isoformat() if t.due_date else None,
            'created_at': _ts(t.created_at),
            'updated_at': _ts(t.updated_at),
        }
        for t in qs
    ]


def _snippets(tenant, user) -> list[dict]:
    return [
        {
            'id': str(s.id),
            'title': s.title,
            'description': s.description,
            'code': s.code,
            'language': s.language,
            'tags': list(s.tags or []),
            'created_at': _ts(s.created_at),
            'updated_at': _ts(s.updated_at),
        }
        for s in CodeSnippet.objects.filter(tenant=tenant, user=user)
    ]


def _contacts(tenant, user) -> list[dict]:
    qs = Contact.objects.filter(tenant=tenant, user=user).select_related('group')
    return [
        {
            'id': str(c.id),
            'first_name': c.first_name,
            'last_name': c.last_name,
            'email': c.email,
            'phone': c.phone,
            'company': c.company,
            'job_title': c.job_title,
            'group': c.group.name if c.group_id else None,
            'notes': c.notes,
            'created_at': _ts(c.created_at),
            'updated_at': _ts(c.updated_at),
        }
        for c in qs
    ]


def _bookmarks(tenant, user) -> list[dict]:
    qs = Bookmark.objects.filter(tenant=tenant, user=user).select_related('collection')
    return [
        {
            'id': str(b.id),
            'title': b.title,
            'url': b.url,
            'description': b.description,
            'collection': b.collection.name if b.collection_id else None,
            'tags': list(b.tags or []),
            'created_at': _ts(b.created_at),
            'updated_at': _ts(b.updated_at),
        }
        for b in qs
    ]


def _calendar(tenant, user) -> list[dict]:
    return [
        {
            'id': str(e.id),
            'title': e.title,
            'description': e.description,
            'start_datetime': _ts(e.start_datetime),
            'end_datetime': _ts(e.end_datetime),
            'is_all_day': e.is_all_day,
            'location': e.location,
            'rrule': e.rrule,
            'color': e.color,
            'created_at': _ts(e.created_at),
            'updated_at': _ts(e.updated_at),
        }
        for e in CalendarEvent.objects.filter(tenant=tenant, user=user)
    ]


def _projects(tenant, user) -> list[dict]:
    """Projects owned by the user, with secrets masked (never exported in cleartext)."""
    qs = (
        Project.objects.filter(tenant=tenant, created_by=user)
        .prefetch_related('sections__items__fields')
    )
    result = []
    for p in qs:
        result.append({
            'id': str(p.id),
            'name': p.name,
            'description': p.description,
            'color': p.color,
            'icon': p.icon,
            'is_archived': p.is_archived,
            'created_at': _ts(p.created_at),
            'sections': [
                {
                    'name': sec.name,
                    'color': sec.color,
                    'items': [
                        {
                            'name': it.name,
                            'description': it.description,
                            'url': it.url,
                            'username': it.username,
                            'notes': it.notes,
                            'fields': [
                                {
                                    'label': f.label,
                                    'field_type': f.field_type,
                                    # Secrets are masked, never decrypted into the export.
                                    'value': MASKED_SECRET if f.is_encrypted else f.value,
                                }
                                for f in it.fields.all()
                            ],
                        }
                        for it in sec.items.all()
                    ],
                }
                for sec in p.sections.all()
            ],
        })
    return result


class WorkspaceBackupView(AuditMixin, APIView):
    """Full account backup as a ZIP of JSON files (excludes all secrets)."""

    permission_classes = [IsAuthenticated, HasFeature('full_backup')]

    @extend_schema(tags=['app-exports'], summary='Export full workspace backup (ZIP)')
    def get(self, request):
        tenant = request.tenant
        user = request.user

        datasets = {
            'notes': _notes(tenant, user),
            'tasks': _tasks(tenant, user),
            'snippets': _snippets(tenant, user),
            'contacts': _contacts(tenant, user),
            'bookmarks': _bookmarks(tenant, user),
            'calendar': _calendar(tenant, user),
            'projects': _projects(tenant, user),
        }

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as zf:
            for name, rows in datasets.items():
                zf.writestr(f'{name}.json', json.dumps(rows, ensure_ascii=False, indent=2))
            zf.writestr('manifest.json', json.dumps({
                'tenant': str(getattr(tenant, 'id', '')),
                'user': str(getattr(user, 'id', '')),
                'generated_at': _ts(timezone.now()),
                'counts': {name: len(rows) for name, rows in datasets.items()},
            }, ensure_ascii=False, indent=2))

        self.log_action(
            request,
            action='data.export',
            resource_type='workspace_backup',
            extra={
                'export_type': 'zip',
                'counts': {name: len(rows) for name, rows in datasets.items()},
            },
        )

        response = HttpResponse(buffer.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = 'attachment; filename="workspace-backup.zip"'
        return response
