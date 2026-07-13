"""
Notes views — CRUD for personal notes with category filtering and pinning.

URL namespace: /api/v1/app/notes/

Endpoints:
  GET    /app/notes/                  → list notes (supports ?category= ?search= ?tag=)
  POST   /app/notes/                  → create note
  GET    /app/notes/tags/             → list distinct tags used by the current user
  GET    /app/notes/<pk>/              → note detail
  PATCH  /app/notes/<pk>/              → update note
  DELETE /app/notes/<pk>/              → delete note
  PATCH  /app/notes/<pk>/pin/          → toggle pin
  GET    /app/notes/categories/        → list categories
  POST   /app/notes/categories/        → create category
  DELETE /app/notes/categories/<pk>/   → delete category
"""
from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notes.models import Note, NoteCategory
from apps.notes.serializers import NoteCategorySerializer, NoteCreateUpdateSerializer, NoteSerializer
from apps.rbac.permissions import HasFeature, HasPermission, check_plan_limit, _user_has_permission
from apps.sharing.models import Share
from core.mixins import AuditMixin
from utils.plans import get_plan_limit

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)

_IMPORT_MAX_ROWS = 1000


def _get_note(pk, tenant, user):
    """Return Note for tenant+user or None."""
    try:
        return Note.objects.get(pk=pk, tenant=tenant, user=user)
    except Note.DoesNotExist:
        return None


def _get_category(pk, tenant, user):
    try:
        return NoteCategory.objects.get(pk=pk, tenant=tenant, user=user)
    except NoteCategory.DoesNotExist:
        return None


class NoteListCreateView(APIView):
    permission_classes = [HasPermission('notes.read')]

    @extend_schema(
        tags=['app-notes'],
        summary='List notes',
        parameters=[
            OpenApiParameter('category', OpenApiTypes.UUID, description='Filter by category'),
            OpenApiParameter('search', OpenApiTypes.STR, description='Search in title/content'),
            OpenApiParameter('tag', OpenApiTypes.STR, description='Filter by tag'),
        ],
    )
    def get(self, request):
        shares = Share.objects.filter(
            shared_with=request.user, resource_type='note'
        ).select_related('shared_by')
        shared_ids = [share.resource_id for share in shares]
        shared_by_map = {share.resource_id: share.shared_by.name for share in shares}
        qs = Note.objects.filter(
            Q(tenant=request.tenant, user=request.user) | Q(pk__in=shared_ids)
        ).distinct()
        category = request.query_params.get('category')
        search = request.query_params.get('search')
        tag = request.query_params.get('tag')
        if category:
            qs = qs.filter(category__pk=category)
        if search:
            qs = qs.filter(Q(title__icontains=search) | Q(content__icontains=search))
        if tag:
            qs = qs.filter(tags__contains=[tag])
        notes = NoteSerializer(
            qs, many=True, context={'request': request, 'shared_by_map': shared_by_map}
        ).data
        return Response({'results': notes, 'count': len(notes), 'notes': notes})

    @extend_schema(tags=['app-notes'], summary='Create note')
    def post(self, request):
        if not _user_has_permission(request.user, 'notes.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = Note.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'notes', count)
        serializer = NoteCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        category_id = data.pop('category', None)
        category = None
        if category_id:
            category = _get_category(category_id, request.tenant, request.user)
        note = Note.objects.create(
            tenant=request.tenant,
            user=request.user,
            category=category,
            **data,
        )
        return Response(NoteSerializer(note).data, status=status.HTTP_201_CREATED)


class NoteTagsView(APIView):
    """Distinct tags the current user has used across their own notes."""

    permission_classes = [HasPermission('notes.read')]

    @extend_schema(tags=['app-notes'], summary='List distinct tags used by the current user')
    def get(self, request):
        notes = Note.objects.filter(tenant=request.tenant, user=request.user).only('tags')
        all_tags: set[str] = set()
        for note in notes:
            all_tags.update(note.tags)
        return Response({'tags': sorted(all_tags)})


class NotesImportView(AuditMixin, APIView):
    """Bulk import notes from a parsed file (client sends validated JSON rows)."""

    permission_classes = [HasPermission('notes.create'), HasFeature('notes_import')]

    @extend_schema(tags=['app-notes'], summary='Bulk import notes')
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
            serializer = NoteCreateUpdateSerializer(data=raw if isinstance(raw, dict) else {})
            if not serializer.is_valid():
                errors.append({'index': idx, 'errors': serializer.errors})
                continue
            data = serializer.validated_data.copy()
            data.pop('category', None)  # imports ignore category FK
            valid.append(data)

        current = Note.objects.filter(tenant=request.tenant, user=request.user).count()
        plan = getattr(request.tenant, 'plan', 'free')
        limit = get_plan_limit(plan, 'notes')
        allowed = len(valid) if limit is None else max(0, limit - current)
        to_create = valid[:allowed]
        skipped = len(valid) - len(to_create)

        created = len(Note.objects.bulk_create(
            [Note(tenant=request.tenant, user=request.user, **d) for d in to_create]
        ))

        self.log_action(
            request,
            action='notes.import',
            resource_type='Note',
            extra={
                'created': created,
                'skipped': skipped,
                'errors': len(errors),
                'source': request.data.get('source', ''),
            },
        )
        return Response({'created': created, 'skipped': skipped, 'errors': errors})


class NoteDetailView(APIView):
    permission_classes = [HasPermission('notes.read')]

    @extend_schema(tags=['app-notes'], summary='Get note detail')
    def get(self, request, pk):
        note = _get_note(pk, request.tenant, request.user)
        if not note:
            return _NOT_FOUND
        return Response({'note': NoteSerializer(note).data})

    @extend_schema(tags=['app-notes'], summary='Update note')
    def patch(self, request, pk):
        if not _user_has_permission(request.user, 'notes.update'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        note = _get_note(pk, request.tenant, request.user)
        if not note:
            return _NOT_FOUND
        serializer = NoteCreateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        category_id = data.pop('category', None)
        if category_id is not None:
            note.category = _get_category(category_id, request.tenant, request.user)
        for field, value in data.items():
            setattr(note, field, value)
        note.save()
        return Response(NoteSerializer(note).data)

    @extend_schema(tags=['app-notes'], summary='Delete note')
    def delete(self, request, pk):
        if not _user_has_permission(request.user, 'notes.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        note = _get_note(pk, request.tenant, request.user)
        if not note:
            return _NOT_FOUND
        note.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotePinView(APIView):
    permission_classes = [HasPermission('notes.read')]

    @extend_schema(tags=['app-notes'], summary='Toggle note pin status')
    def patch(self, request, pk):
        note = _get_note(pk, request.tenant, request.user)
        if not note:
            return _NOT_FOUND
        note.is_pinned = not note.is_pinned
        note.save(update_fields=['is_pinned', 'updated_at'])
        return Response(NoteSerializer(note).data)


class NoteCategoryListCreateView(APIView):
    permission_classes = [HasPermission('notes.read')]

    @extend_schema(tags=['app-notes'], summary='List note categories')
    def get(self, request):
        categories = NoteCategory.objects.filter(tenant=request.tenant, user=request.user)
        cats = NoteCategorySerializer(categories, many=True).data
        return Response({'results': cats, 'count': len(cats), 'categories': cats})

    @extend_schema(tags=['app-notes'], summary='Create note category')
    def post(self, request):
        serializer = NoteCategorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        category = serializer.save(tenant=request.tenant, user=request.user)
        return Response(NoteCategorySerializer(category).data, status=status.HTTP_201_CREATED)


class NoteCategoryDetailView(APIView):
    permission_classes = [HasPermission('notes.read')]

    @extend_schema(tags=['app-notes'], summary='Delete note category')
    def delete(self, request, pk):
        category = _get_category(pk, request.tenant, request.user)
        if not category:
            return _NOT_FOUND
        category.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
