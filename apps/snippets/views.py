"""
Snippets views — CRUD for code snippets.

URL namespace: /api/v1/app/snippets/

Endpoints:
  GET    /app/snippets/        → list snippets (supports ?language= ?tag= ?search=)
  POST   /app/snippets/        → create snippet
  GET    /app/snippets/tags/   → list distinct tags used by the current user
  GET    /app/snippets/<pk>/   → snippet detail
  PATCH  /app/snippets/<pk>/   → update snippet
  DELETE /app/snippets/<pk>/   → delete snippet
"""
from django.db.models import Q
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermission, _user_has_permission, check_plan_limit
from apps.sharing.models import Share
from apps.snippets.models import CodeSnippet
from apps.snippets.serializers import CodeSnippetCreateUpdateSerializer, CodeSnippetSerializer

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)


def _get_object(pk, tenant, user):
    """Return CodeSnippet for tenant+user or None."""
    try:
        return CodeSnippet.objects.get(pk=pk, tenant=tenant, user=user)
    except CodeSnippet.DoesNotExist:
        return None


class CodeSnippetListCreateView(APIView):
    permission_classes = [HasPermission('snippets.read')]

    @extend_schema(
        tags=['app-devops'],
        summary='List code snippets',
        parameters=[
            OpenApiParameter('language', OpenApiTypes.STR, description='Filter by programming language'),
            OpenApiParameter('tag', OpenApiTypes.STR, description='Filter by tag'),
            OpenApiParameter('search', OpenApiTypes.STR, description='Search in title/description/code'),
            OpenApiParameter('page', OpenApiTypes.INT, description='Page number. Omit to get all results unpaginated.'),
            OpenApiParameter('per_page', OpenApiTypes.INT, description='Results per page (default: 20, max: 100)'),
        ],
    )
    def get(self, request):
        shares = Share.objects.filter(
            shared_with=request.user, resource_type='snippet'
        ).select_related('shared_by')
        shared_ids = [share.resource_id for share in shares]
        shared_by_map = {share.resource_id: share.shared_by.name for share in shares}
        qs = CodeSnippet.objects.filter(
            Q(tenant=request.tenant, user=request.user) | Q(pk__in=shared_ids)
        ).distinct()
        language = request.query_params.get('language')
        tag = request.query_params.get('tag')
        search = request.query_params.get('search')
        if language:
            qs = qs.filter(language=language)
        if tag:
            qs = qs.filter(tags__contains=[tag])
        if search:
            qs = qs.filter(
                Q(title__icontains=search) |
                Q(description__icontains=search) |
                Q(code__icontains=search)
            )

        context = {'request': request, 'shared_by_map': shared_by_map}
        raw_page = request.query_params.get('page')

        if raw_page is None:
            snippets = CodeSnippetSerializer(qs, many=True, context=context).data
            return Response({'snippets': snippets})

        total = qs.count()
        try:
            page = max(1, int(raw_page))
            per_page = min(100, max(1, int(request.query_params.get('per_page', 20))))
        except (ValueError, TypeError):
            page = 1
            per_page = 20

        offset = (page - 1) * per_page
        snippets = CodeSnippetSerializer(qs[offset:offset + per_page], many=True, context=context).data
        return Response({
            'snippets': snippets,
            'pagination': {'page': page, 'per_page': per_page, 'total': total},
        })

    @extend_schema(tags=['app-devops'], summary='Create code snippet')
    def post(self, request):
        if not _user_has_permission(request.user, 'snippets.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = CodeSnippet.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'snippets', count)
        serializer = CodeSnippetCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        snippet = CodeSnippet.objects.create(
            tenant=request.tenant,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(CodeSnippetSerializer(snippet).data, status=status.HTTP_201_CREATED)


class SnippetTagsView(APIView):
    """Distinct tags the current user has used across their own snippets."""

    permission_classes = [HasPermission('snippets.read')]

    @extend_schema(tags=['app-devops'], summary='List distinct tags used by the current user')
    def get(self, request):
        snippets = CodeSnippet.objects.filter(tenant=request.tenant, user=request.user).only('tags')
        all_tags: set[str] = set()
        for snippet in snippets:
            all_tags.update(snippet.tags)
        return Response({'tags': sorted(all_tags)})


class CodeSnippetDetailView(APIView):
    permission_classes = [HasPermission('snippets.read')]

    @extend_schema(tags=['app-devops'], summary='Get snippet detail')
    def get(self, request, pk):
        snippet = _get_object(pk, request.tenant, request.user)
        if not snippet:
            return _NOT_FOUND
        return Response({'snippet': CodeSnippetSerializer(snippet).data})

    @extend_schema(tags=['app-devops'], summary='Update snippet')
    def patch(self, request, pk):
        if not _user_has_permission(request.user, 'snippets.update'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        snippet = _get_object(pk, request.tenant, request.user)
        if not snippet:
            return _NOT_FOUND
        serializer = CodeSnippetCreateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(snippet, field, value)
        snippet.save()
        return Response(CodeSnippetSerializer(snippet).data)

    @extend_schema(tags=['app-devops'], summary='Delete snippet')
    def delete(self, request, pk):
        if not _user_has_permission(request.user, 'snippets.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        snippet = _get_object(pk, request.tenant, request.user)
        if not snippet:
            return _NOT_FOUND
        snippet.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
