"""
Bookmarks views — URL bookmarks CRUD with tags filtering and collections.

URL namespace: /api/v1/app/bookmarks/

Endpoints:
  GET    /app/bookmarks/                      → list bookmarks (supports ?collection= ?search= ?tag=)
  POST   /app/bookmarks/                      → create bookmark
  GET    /app/bookmarks/<pk>/                 → bookmark detail
  PATCH  /app/bookmarks/<pk>/                 → update bookmark
  DELETE /app/bookmarks/<pk>/                 → delete bookmark
  GET    /app/bookmarks/collections/          → list collections (HasFeature bookmark_collections)
  POST   /app/bookmarks/collections/          → create collection
  GET    /app/bookmarks/collections/<pk>/     → collection detail
  DELETE /app/bookmarks/collections/<pk>/     → delete collection
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.bookmarks.models import Bookmark, BookmarkCollection
from apps.bookmarks.serializers import (
    BookmarkCollectionSerializer,
    BookmarkCreateUpdateSerializer,
    BookmarkSerializer,
)
from apps.rbac.permissions import HasFeature, HasPermission, check_plan_limit

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)


def _get_bookmark(pk, tenant, user):
    try:
        return Bookmark.objects.get(pk=pk, tenant=tenant, user=user)
    except Bookmark.DoesNotExist:
        return None


def _get_collection(pk, tenant, user):
    try:
        return BookmarkCollection.objects.get(pk=pk, tenant=tenant, user=user)
    except BookmarkCollection.DoesNotExist:
        return None


class BookmarkListCreateView(APIView):
    permission_classes = [HasPermission('bookmarks.read')]

    def get(self, request):
        qs = Bookmark.objects.filter(tenant=request.tenant, user=request.user)
        collection = request.query_params.get('collection')
        search = request.query_params.get('search')
        tag = request.query_params.get('tag')
        if collection:
            qs = qs.filter(collection__pk=collection)
        if search:
            qs = qs.filter(title__icontains=search) | Bookmark.objects.filter(
                tenant=request.tenant, user=request.user, url__icontains=search
            )
            qs = qs.distinct()
        if tag:
            qs = qs.filter(tags__contains=[tag])
        return Response({'bookmarks': BookmarkSerializer(qs, many=True).data})

    def post(self, request):
        if not request.user.has_perm('bookmarks.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = Bookmark.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'bookmarks', count)
        serializer = BookmarkCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        collection_id = data.pop('collection', None)
        collection = None
        if collection_id:
            collection = _get_collection(collection_id, request.tenant, request.user)
        bookmark = Bookmark.objects.create(
            tenant=request.tenant,
            user=request.user,
            collection=collection,
            **data,
        )
        return Response(BookmarkSerializer(bookmark).data, status=status.HTTP_201_CREATED)


class BookmarkDetailView(APIView):
    permission_classes = [HasPermission('bookmarks.read')]

    def get(self, request, pk):
        bookmark = _get_bookmark(pk, request.tenant, request.user)
        if not bookmark:
            return _NOT_FOUND
        return Response({'bookmark': BookmarkSerializer(bookmark).data})

    def patch(self, request, pk):
        if not request.user.has_perm('bookmarks.update'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        bookmark = _get_bookmark(pk, request.tenant, request.user)
        if not bookmark:
            return _NOT_FOUND
        serializer = BookmarkCreateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        collection_id = data.pop('collection', None)
        if collection_id is not None:
            bookmark.collection = _get_collection(collection_id, request.tenant, request.user)
        for field, value in data.items():
            setattr(bookmark, field, value)
        bookmark.save()
        return Response(BookmarkSerializer(bookmark).data)

    def delete(self, request, pk):
        if not request.user.has_perm('bookmarks.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        bookmark = _get_bookmark(pk, request.tenant, request.user)
        if not bookmark:
            return _NOT_FOUND
        bookmark.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class BookmarkCollectionListCreateView(APIView):
    permission_classes = [HasPermission('bookmarks.read'), HasFeature('bookmark_collections')]

    def get(self, request):
        collections = BookmarkCollection.objects.filter(tenant=request.tenant, user=request.user)
        return Response({'collections': BookmarkCollectionSerializer(collections, many=True).data})

    def post(self, request):
        serializer = BookmarkCollectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        collection = serializer.save(tenant=request.tenant, user=request.user)
        return Response(
            BookmarkCollectionSerializer(collection).data, status=status.HTTP_201_CREATED
        )


class BookmarkCollectionDetailView(APIView):
    permission_classes = [HasPermission('bookmarks.read'), HasFeature('bookmark_collections')]

    def delete(self, request, pk):
        collection = _get_collection(pk, request.tenant, request.user)
        if not collection:
            return _NOT_FOUND
        collection.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
