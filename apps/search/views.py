"""
Global search — aggregates results across the user's workspace resources.

URL namespace: /api/v1/app/search/

Endpoint:
  GET /app/search/?q=<term>[&types=notes,tasks,...][&date_from=][&date_to=][&limit=]
    → { query, total, groups: [{ type, label, count, results: [...] }] }

Each resource is queried with the same tenant/user isolation used by its own
list view. Chat messages are restricted to conversations the user belongs to.
"""
from django.db.models import Q
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.bookmarks.models import Bookmark
from apps.calendar_app.models import CalendarEvent
from apps.chat.models import Message
from apps.contacts.models import Contact
from apps.notes.models import Note
from apps.projects.models import Project
from apps.snippets.models import CodeSnippet
from apps.tasks.models import Task
from apps.vault.models import VaultItem

MIN_QUERY_LEN = 2
DEFAULT_LIMIT = 5
MAX_LIMIT = 20

# Order here defines the order of groups in the response.
TYPE_LABELS = {
    'notes': 'Notas',
    'tasks': 'Tareas',
    'events': 'Eventos',
    'contacts': 'Contactos',
    'bookmarks': 'Bookmarks',
    'snippets': 'Snippets',
    'projects': 'Proyectos',
    'vault': 'Bóveda',
    'messages': 'Mensajes',
}

# Human labels for vault item types — safe to show (non-sensitive metadata).
_VAULT_TYPE_LABELS = {
    'login': 'Login',
    'api_key': 'API Key',
    'secure_note': 'Nota segura',
    'card': 'Tarjeta',
}


def _make_snippet(query: str, *fields: str, radius: int = 60) -> tuple[str, str]:
    """Return (snippet, matched_field_value) for the first field containing query."""
    needle = query.lower()
    for value in fields:
        if not value:
            continue
        idx = value.lower().find(needle)
        if idx == -1:
            continue
        start = max(0, idx - radius)
        end = min(len(value), idx + len(query) + radius)
        prefix = '…' if start > 0 else ''
        suffix = '…' if end < len(value) else ''
        return f'{prefix}{value[start:end].strip()}{suffix}', value
    # No field matched the term directly (e.g. matched on a non-snippet field).
    first = next((f for f in fields if f), '')
    return (first[: 2 * radius].strip(), first)


def _item(type_: str, obj_id, title: str, snippet: str, url: str, created_at) -> dict:
    return {
        'type': type_,
        'id': str(obj_id),
        'title': title,
        'snippet': snippet,
        'url': url,
        'created_at': created_at.isoformat() if created_at else None,
    }


def _apply_dates(qs, date_from, date_to, field='created_at'):
    if date_from:
        qs = qs.filter(**{f'{field}__gte': date_from})
    if date_to:
        qs = qs.filter(**{f'{field}__lte': date_to})
    return qs


def _search_notes(request, q, limit, df, dt):
    qs = Note.objects.filter(
        Q(tenant=request.tenant, user=request.user)
        & (Q(title__icontains=q) | Q(content__icontains=q))
    )
    qs = _apply_dates(qs, df, dt).order_by('-created_at')[:limit]
    out = []
    for n in qs:
        snippet, _ = _make_snippet(q, n.content, n.title)
        out.append(_item('notes', n.id, n.title, snippet, '/notes', n.created_at))
    return out


def _search_tasks(request, q, limit, df, dt):
    qs = Task.objects.filter(
        Q(tenant=request.tenant) & (Q(title__icontains=q) | Q(description__icontains=q))
    )
    qs = _apply_dates(qs, df, dt).order_by('-created_at')[:limit]
    out = []
    for t in qs:
        snippet, _ = _make_snippet(q, t.description, t.title)
        out.append(_item('tasks', t.id, t.title, snippet, '/tasks', t.created_at))
    return out


def _search_events(request, q, limit, df, dt):
    qs = CalendarEvent.objects.filter(
        Q(tenant=request.tenant, user=request.user)
        & (Q(title__icontains=q) | Q(description__icontains=q) | Q(location__icontains=q))
    )
    qs = _apply_dates(qs, df, dt).order_by('-start_datetime')[:limit]
    out = []
    for e in qs:
        snippet, _ = _make_snippet(q, e.description, e.location, e.title)
        out.append(_item('events', e.id, e.title, snippet, '/calendar', e.created_at))
    return out


def _search_contacts(request, q, limit, df, dt):
    qs = Contact.objects.filter(
        Q(tenant=request.tenant, user=request.user)
        & (
            Q(first_name__icontains=q)
            | Q(last_name__icontains=q)
            | Q(email__icontains=q)
            | Q(company__icontains=q)
            | Q(notes__icontains=q)
        )
    )
    qs = _apply_dates(qs, df, dt).order_by('-created_at')[:limit]
    out = []
    for c in qs:
        name = f'{c.first_name} {c.last_name}'.strip()
        snippet, _ = _make_snippet(q, c.company, c.email, c.notes, name)
        out.append(_item('contacts', c.id, name, snippet, '/contacts', c.created_at))
    return out


def _search_bookmarks(request, q, limit, df, dt):
    qs = Bookmark.objects.filter(
        Q(tenant=request.tenant, user=request.user)
        & (Q(title__icontains=q) | Q(url__icontains=q) | Q(description__icontains=q))
    )
    qs = _apply_dates(qs, df, dt).order_by('-created_at')[:limit]
    out = []
    for b in qs:
        snippet, _ = _make_snippet(q, b.description, b.url, b.title)
        out.append(_item('bookmarks', b.id, b.title, snippet, '/bookmarks', b.created_at))
    return out


def _search_snippets(request, q, limit, df, dt):
    qs = CodeSnippet.objects.filter(
        Q(tenant=request.tenant, user=request.user)
        & (Q(title__icontains=q) | Q(description__icontains=q) | Q(code__icontains=q))
    )
    qs = _apply_dates(qs, df, dt).order_by('-created_at')[:limit]
    out = []
    for s in qs:
        snippet, _ = _make_snippet(q, s.description, s.code, s.title)
        out.append(_item('snippets', s.id, s.title, snippet, '/snippets', s.created_at))
    return out


def _search_projects(request, q, limit, df, dt):
    qs = Project.objects.filter(
        Q(tenant=request.tenant) & (Q(name__icontains=q) | Q(description__icontains=q))
    )
    qs = _apply_dates(qs, df, dt).order_by('-created_at')[:limit]
    out = []
    for p in qs:
        snippet, _ = _make_snippet(q, p.description, p.name)
        out.append(_item('projects', p.id, p.name, snippet, '/projects', p.created_at))
    return out


def _search_vault(request, q, limit, df, dt):
    # Vault is encrypted: only the plaintext ``title`` is searched/returned.
    # The encrypted ``data_ciphertext`` is never read here, so no unlock token
    # is required and no sensitive data is exposed.
    qs = VaultItem.objects.filter(
        tenant=request.tenant, user=request.user, title__icontains=q
    )
    qs = _apply_dates(qs, df, dt).order_by('title')[:limit]
    out = []
    for v in qs:
        snippet = _VAULT_TYPE_LABELS.get(v.item_type, '')
        out.append(_item('vault', v.id, v.title, snippet, '/vault', v.created_at))
    return out


def _search_messages(request, q, limit, df, dt):
    # Only messages from conversations the user is a member of, not deleted.
    qs = Message.objects.filter(
        conversation__members__user=request.user,
        deleted_at__isnull=True,
        content__icontains=q,
    ).select_related('conversation')
    qs = _apply_dates(qs, df, dt).order_by('-created_at')[:limit]
    out = []
    for m in qs:
        snippet, _ = _make_snippet(q, m.content)
        url = f'/chat?conversation={m.conversation_id}'
        out.append(_item('messages', m.id, 'Mensaje de chat', snippet, url, m.created_at))
    return out


SEARCHERS = {
    'notes': _search_notes,
    'tasks': _search_tasks,
    'events': _search_events,
    'contacts': _search_contacts,
    'bookmarks': _search_bookmarks,
    'snippets': _search_snippets,
    'projects': _search_projects,
    'vault': _search_vault,
    'messages': _search_messages,
}


class GlobalSearchView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=['app-search'],
        summary='Global search across workspace resources',
        parameters=[
            OpenApiParameter('q', OpenApiTypes.STR, required=True, description='Search term (min 2 chars)'),
            OpenApiParameter('types', OpenApiTypes.STR, description='Comma-separated subset of result types'),
            OpenApiParameter('date_from', OpenApiTypes.DATE, description='Filter created_at >= date'),
            OpenApiParameter('date_to', OpenApiTypes.DATE, description='Filter created_at <= date'),
            OpenApiParameter('limit', OpenApiTypes.INT, description=f'Results per type (default {DEFAULT_LIMIT}, max {MAX_LIMIT})'),
        ],
    )
    def get(self, request):
        q = (request.query_params.get('q') or '').strip()
        if len(q) < MIN_QUERY_LEN:
            return Response(
                {'error': {'code': 'invalid_query', 'message': f'La búsqueda requiere al menos {MIN_QUERY_LEN} caracteres.'}},
                status=400,
            )

        requested = request.query_params.get('types')
        if requested:
            types = [t for t in (s.strip() for s in requested.split(',')) if t in SEARCHERS]
        else:
            types = list(SEARCHERS.keys())

        limit = self._parse_limit(request.query_params.get('limit'))
        df = self._parse_dt(request.query_params.get('date_from'))
        dt = self._parse_dt(request.query_params.get('date_to'))

        groups = []
        total = 0
        for type_ in TYPE_LABELS:  # preserve display order
            if type_ not in types:
                continue
            results = SEARCHERS[type_](request, q, limit, df, dt)
            if results:
                groups.append({
                    'type': type_,
                    'label': TYPE_LABELS[type_],
                    'count': len(results),
                    'results': results,
                })
                total += len(results)

        return Response({'query': q, 'total': total, 'groups': groups})

    @staticmethod
    def _parse_limit(raw) -> int:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return DEFAULT_LIMIT
        return max(1, min(value, MAX_LIMIT))

    @staticmethod
    def _parse_dt(raw):
        if not raw:
            return None
        return parse_datetime(raw) or parse_date(raw)
