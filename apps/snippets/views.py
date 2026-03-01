"""
Snippets views — CRUD for code snippets.

URL namespace: /api/v1/app/snippets/

Endpoints:
  GET    /app/snippets/        → list snippets (supports ?language= ?tag= ?search=)
  POST   /app/snippets/        → create snippet
  GET    /app/snippets/<pk>/   → snippet detail
  PATCH  /app/snippets/<pk>/   → update snippet
  DELETE /app/snippets/<pk>/   → delete snippet
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasPermission, check_plan_limit
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

    def get(self, request):
        qs = CodeSnippet.objects.filter(tenant=request.tenant, user=request.user)
        language = request.query_params.get('language')
        tag = request.query_params.get('tag')
        search = request.query_params.get('search')
        if language:
            qs = qs.filter(language=language)
        if tag:
            qs = qs.filter(tags__contains=[tag])
        if search:
            qs = qs.filter(title__icontains=search) | CodeSnippet.objects.filter(
                tenant=request.tenant,
                user=request.user,
                description__icontains=search,
            ) | CodeSnippet.objects.filter(
                tenant=request.tenant,
                user=request.user,
                code__icontains=search,
            )
            qs = qs.distinct()
        return Response({'snippets': CodeSnippetSerializer(qs, many=True).data})

    def post(self, request):
        if not request.user.has_perm('snippets.create'):
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


class CodeSnippetDetailView(APIView):
    permission_classes = [HasPermission('snippets.read')]

    def get(self, request, pk):
        snippet = _get_object(pk, request.tenant, request.user)
        if not snippet:
            return _NOT_FOUND
        return Response({'snippet': CodeSnippetSerializer(snippet).data})

    def patch(self, request, pk):
        if not request.user.has_perm('snippets.update'):
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

    def delete(self, request, pk):
        if not request.user.has_perm('snippets.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        snippet = _get_object(pk, request.tenant, request.user)
        if not snippet:
            return _NOT_FOUND
        snippet.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
