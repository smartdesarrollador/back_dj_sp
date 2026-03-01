"""
Notes views — CRUD for personal notes with category filtering and pinning.

URL namespace: /api/v1/app/notes/

Endpoints:
  GET    /app/notes/           → list notes (supports ?category= ?search=)
  POST   /app/notes/           → create note
  GET    /app/notes/<pk>/      → note detail
  PATCH  /app/notes/<pk>/      → update note
  DELETE /app/notes/<pk>/      → delete note
  PATCH  /app/notes/<pk>/pin/  → toggle pin
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.notes.models import Note
from apps.notes.serializers import NoteCreateUpdateSerializer, NoteSerializer
from apps.rbac.permissions import HasPermission, check_plan_limit

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)


def _get_note(pk, tenant, user):
    """Return Note for tenant+user or None."""
    try:
        return Note.objects.get(pk=pk, tenant=tenant, user=user)
    except Note.DoesNotExist:
        return None


class NoteListCreateView(APIView):
    permission_classes = [HasPermission('notes.read')]

    def get(self, request):
        qs = Note.objects.filter(tenant=request.tenant, user=request.user)
        category = request.query_params.get('category')
        search = request.query_params.get('search')
        if category:
            qs = qs.filter(category=category)
        if search:
            qs = qs.filter(title__icontains=search) | Note.objects.filter(
                tenant=request.tenant, user=request.user, content__icontains=search
            )
            qs = qs.distinct()
        return Response({'notes': NoteSerializer(qs, many=True).data})

    def post(self, request):
        if not request.user.has_perm('notes.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = Note.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'notes', count)
        serializer = NoteCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        note = Note.objects.create(
            tenant=request.tenant,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(NoteSerializer(note).data, status=status.HTTP_201_CREATED)


class NoteDetailView(APIView):
    permission_classes = [HasPermission('notes.read')]

    def get(self, request, pk):
        note = _get_note(pk, request.tenant, request.user)
        if not note:
            return _NOT_FOUND
        return Response({'note': NoteSerializer(note).data})

    def patch(self, request, pk):
        if not request.user.has_perm('notes.update'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        note = _get_note(pk, request.tenant, request.user)
        if not note:
            return _NOT_FOUND
        serializer = NoteCreateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(note, field, value)
        note.save()
        return Response(NoteSerializer(note).data)

    def delete(self, request, pk):
        if not request.user.has_perm('notes.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        note = _get_note(pk, request.tenant, request.user)
        if not note:
            return _NOT_FOUND
        note.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class NotePinView(APIView):
    permission_classes = [HasPermission('notes.read')]

    def patch(self, request, pk):
        note = _get_note(pk, request.tenant, request.user)
        if not note:
            return _NOT_FOUND
        note.is_pinned = not note.is_pinned
        note.save(update_fields=['is_pinned', 'updated_at'])
        return Response(NoteSerializer(note).data)
