from django.db.models import F
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.releases.models import DesktopRelease
from apps.releases.serializers import (
    DesktopReleaseCreateSerializer,
    DesktopReleaseSerializer,
    DesktopReleaseUpdateSerializer,
)

_NOT_FOUND = {'error': {'code': 'not_found', 'message': 'Release not found.'}}


class ReleaseListView(APIView):
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        tags=['admin-releases'],
        summary='List all desktop releases',
        parameters=[
            OpenApiParameter('platform', OpenApiTypes.STR, description='Filter by platform'),
            OpenApiParameter('published', OpenApiTypes.BOOL, description='Filter by published status'),
        ],
        responses={200: OpenApiResponse(description='{ releases: [...] }')},
    )
    def get(self, request):
        qs = DesktopRelease.objects.all()
        if platform := request.query_params.get('platform'):
            qs = qs.filter(platform=platform)
        if (pub := request.query_params.get('published')) is not None:
            qs = qs.filter(is_published=pub.lower() in ('true', '1'))
        serializer = DesktopReleaseSerializer(qs, many=True, context={'request': request})
        return Response({'releases': serializer.data})

    @extend_schema(
        tags=['admin-releases'],
        summary='Upload a new desktop release (multipart/form-data)',
        request=DesktopReleaseCreateSerializer,
        responses={
            201: DesktopReleaseSerializer,
            400: OpenApiResponse(description='Validation error'),
        },
    )
    def post(self, request):
        serializer = DesktopReleaseCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'error': {'code': 'validation_error', 'detail': serializer.errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        release = serializer.save()
        return Response(
            DesktopReleaseSerializer(release, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )


class ReleaseDetailView(APIView):
    permission_classes = [IsAdminUser]

    def _get_release(self, pk):
        try:
            return DesktopRelease.objects.get(pk=pk)
        except DesktopRelease.DoesNotExist:
            return None

    @extend_schema(
        tags=['admin-releases'],
        summary='Update release metadata (publish/unpublish, edit notes)',
        request=DesktopReleaseUpdateSerializer,
        responses={
            200: DesktopReleaseSerializer,
            400: OpenApiResponse(description='Validation error'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    def patch(self, request, pk):
        release = self._get_release(pk)
        if release is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        serializer = DesktopReleaseUpdateSerializer(release, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(
                {'error': {'code': 'validation_error', 'detail': serializer.errors}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer.save()
        return Response(DesktopReleaseSerializer(release, context={'request': request}).data)

    @extend_schema(
        tags=['admin-releases'],
        summary='Delete a desktop release and its file',
        responses={
            204: OpenApiResponse(description='Deleted'),
            404: OpenApiResponse(description='Not found'),
        },
    )
    def delete(self, request, pk):
        release = self._get_release(pk)
        if release is None:
            return Response(_NOT_FOUND, status=status.HTTP_404_NOT_FOUND)
        if release.file:
            release.file.delete(save=False)
        release.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LatestReleaseView(APIView):
    """
    Returns the latest published release(s).

    GET /api/v1/public/desktop/latest/?platform=windows  → { release: {...} }
    GET /api/v1/public/desktop/latest/                   → { releases: [...] }
    """
    permission_classes = [AllowAny]

    @extend_schema(
        tags=['public-releases'],
        summary='Get latest published desktop release',
        parameters=[
            OpenApiParameter(
                'platform', OpenApiTypes.STR,
                description='Platform: windows, macos, linux',
            ),
        ],
        responses={200: OpenApiResponse(description='{ release: {...} } or { releases: [...] }')},
    )
    def get(self, request):
        platform = request.query_params.get('platform')
        if platform:
            release = DesktopRelease.objects.filter(
                is_published=True, platform=platform
            ).first()
            if release is None:
                return Response(
                    {'error': {'code': 'not_found', 'message': 'No published release found.'}},
                    status=status.HTTP_404_NOT_FOUND,
                )
            DesktopRelease.objects.filter(pk=release.pk).update(
                download_count=F('download_count') + 1
            )
            return Response(
                {'release': DesktopReleaseSerializer(release, context={'request': request}).data}
            )

        releases = []
        for p in ['windows', 'macos', 'linux']:
            r = DesktopRelease.objects.filter(is_published=True, platform=p).first()
            if r:
                DesktopRelease.objects.filter(pk=r.pk).update(
                    download_count=F('download_count') + 1
                )
                releases.append(
                    DesktopReleaseSerializer(r, context={'request': request}).data
                )
        return Response({'releases': releases})
