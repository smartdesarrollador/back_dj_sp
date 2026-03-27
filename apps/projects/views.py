"""
Projects views — credential vault CRUD with AES-256 encryption.

URL namespace: /api/v1/app/projects/

Endpoints:
  GET    /app/projects/                                          → list projects
  POST   /app/projects/create/                                   → create project
  GET    /app/projects/<pk>/                                     → project detail
  PATCH  /app/projects/<pk>/update/                              → update project
  DELETE /app/projects/<pk>/delete/                              → delete project
  POST   /app/projects/<pk>/sections/                            → create section
  PATCH  /app/projects/<pk>/sections/reorder/                    → reorder sections
  PATCH  /app/projects/<pk>/sections/<sk>/                       → update section
  DELETE /app/projects/<pk>/sections/<sk>/delete/                → delete section
  POST   /app/projects/<pk>/sections/<sk>/items/                 → create item
  PATCH  /app/projects/<pk>/sections/<sk>/items/<ik>/            → update item
  DELETE /app/projects/<pk>/sections/<sk>/items/<ik>/delete/     → delete item
  POST   /app/projects/<pk>/sections/<sk>/items/<ik>/fields/     → create field
  PATCH  /app/projects/<pk>/sections/<sk>/items/<ik>/fields/<fk>/→ update field
  DELETE /app/projects/<pk>/sections/<sk>/items/<ik>/fields/<fk>/delete/ → delete field
  POST   /app/projects/<pk>/sections/<sk>/items/<ik>/reveal/<fk>/→ reveal password
  GET    /app/projects/<pk>/members/                             → list members
  POST   /app/projects/<pk>/members/add/                         → add member
  DELETE /app/projects/<pk>/members/<mk>/                        → remove member
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.models import AuditLog
from apps.projects.models import (
    Project,
    ProjectItem,
    ProjectItemField,
    ProjectMember,
    ProjectSection,
)
from apps.projects.serializers import (
    FieldCreateUpdateSerializer,
    ProjectCreateUpdateSerializer,
    ProjectItemFieldSerializer,
    ProjectItemSerializer,
    ProjectMemberSerializer,
    ProjectSectionSerializer,
    ProjectSerializer,
)
from apps.rbac.permissions import HasPermission, check_plan_limit
from utils.encryption import decrypt_value

User = get_user_model()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _get_project(pk, tenant):
    """Return Project for tenant or raise 404."""
    try:
        return Project.objects.get(pk=pk, tenant=tenant)
    except Project.DoesNotExist:
        return None


def _get_section(sk, project):
    try:
        return ProjectSection.objects.get(pk=sk, project=project)
    except ProjectSection.DoesNotExist:
        return None


def _get_item(ik, section):
    try:
        return ProjectItem.objects.get(pk=ik, section=section)
    except ProjectItem.DoesNotExist:
        return None


def _get_field(fk, item):
    try:
        return ProjectItemField.objects.get(pk=fk, item=item)
    except ProjectItemField.DoesNotExist:
        return None


_NOT_FOUND = Response({'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404)


# ─── Project Views ─────────────────────────────────────────────────────────────

class ProjectListView(APIView):
    permission_classes = [HasPermission('projects.read')]

    @extend_schema(tags=['app-projects'], summary='List projects')
    def get(self, request):
        projects = Project.objects.filter(
            tenant=request.tenant
        ).prefetch_related('sections__items__fields', 'members')
        serializer = ProjectSerializer(projects, many=True)
        data = serializer.data
        return Response({'results': data, 'count': len(data), 'projects': data})


class ProjectCreateView(APIView):
    permission_classes = [HasPermission('projects.create')]

    @extend_schema(tags=['app-projects'], summary='Create project')
    def post(self, request):
        count = Project.objects.filter(tenant=request.tenant).count()
        check_plan_limit(request.user, 'projects', count)

        serializer = ProjectCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        project = serializer.save(tenant=request.tenant, created_by=request.user)
        return Response(ProjectSerializer(project).data, status=status.HTTP_201_CREATED)


class ProjectDetailView(APIView):
    permission_classes = [HasPermission('projects.read')]

    @extend_schema(tags=['app-projects'], summary='Get project detail')
    def get(self, request, pk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        serializer = ProjectSerializer(project)
        return Response({'project': serializer.data})


class ProjectUpdateView(APIView):
    permission_classes = [HasPermission('projects.update')]

    @extend_schema(tags=['app-projects'], summary='Update project')
    def patch(self, request, pk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        serializer = ProjectCreateUpdateSerializer(project, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProjectSerializer(project).data)


class ProjectDeleteView(APIView):
    permission_classes = [HasPermission('projects.delete')]

    @extend_schema(tags=['app-projects'], summary='Delete project')
    def delete(self, request, pk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        project.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Section Views ─────────────────────────────────────────────────────────────

class SectionCreateView(APIView):
    permission_classes = [HasPermission('projects.sections')]

    @extend_schema(tags=['app-projects'], summary='Create section in project')
    def post(self, request, pk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND

        count = project.sections.count()
        check_plan_limit(request.user, 'sections_per_project', count)

        serializer = ProjectSectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        section = serializer.save(project=project)
        return Response(ProjectSectionSerializer(section).data, status=status.HTTP_201_CREATED)


class SectionReorderView(APIView):
    permission_classes = [HasPermission('projects.sections')]

    @extend_schema(tags=['app-projects'], summary='Reorder sections')
    @transaction.atomic
    def patch(self, request, pk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND

        # Expects: {"order": [{"id": "<uuid>", "order": 0}, ...]}
        order_data = request.data.get('order', [])
        for entry in order_data:
            ProjectSection.objects.filter(
                pk=entry['id'], project=project
            ).update(order=entry['order'])

        sections = project.sections.all()
        return Response({'sections': ProjectSectionSerializer(sections, many=True).data})


class SectionUpdateView(APIView):
    permission_classes = [HasPermission('projects.sections')]

    @extend_schema(tags=['app-projects'], summary='Update section')
    def patch(self, request, pk, sk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        section = _get_section(sk, project)
        if not section:
            return _NOT_FOUND
        serializer = ProjectSectionSerializer(section, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProjectSectionSerializer(section).data)


class SectionDeleteView(APIView):
    permission_classes = [HasPermission('projects.sections')]

    @extend_schema(tags=['app-projects'], summary='Delete section')
    def delete(self, request, pk, sk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        section = _get_section(sk, project)
        if not section:
            return _NOT_FOUND
        section.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Item Views ────────────────────────────────────────────────────────────────

class ItemCreateView(APIView):
    permission_classes = [HasPermission('credentials.manage')]

    @extend_schema(tags=['app-projects'], summary='Create item in section')
    def post(self, request, pk, sk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        section = _get_section(sk, project)
        if not section:
            return _NOT_FOUND

        # Count all items across the project for plan limit
        item_count = ProjectItem.objects.filter(section__project=project).count()
        check_plan_limit(request.user, 'items_per_project', item_count)

        serializer = ProjectItemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        item = serializer.save(section=section)
        return Response(ProjectItemSerializer(item).data, status=status.HTTP_201_CREATED)


class ItemUpdateView(APIView):
    permission_classes = [HasPermission('credentials.manage')]

    @extend_schema(tags=['app-projects'], summary='Update item')
    def patch(self, request, pk, sk, ik):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        section = _get_section(sk, project)
        if not section:
            return _NOT_FOUND
        item = _get_item(ik, section)
        if not item:
            return _NOT_FOUND
        serializer = ProjectItemSerializer(item, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProjectItemSerializer(item).data)


class ItemDeleteView(APIView):
    permission_classes = [HasPermission('credentials.manage')]

    @extend_schema(tags=['app-projects'], summary='Delete item')
    def delete(self, request, pk, sk, ik):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        section = _get_section(sk, project)
        if not section:
            return _NOT_FOUND
        item = _get_item(ik, section)
        if not item:
            return _NOT_FOUND
        item.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Field Views ───────────────────────────────────────────────────────────────

class FieldCreateView(APIView):
    permission_classes = [HasPermission('credentials.manage')]

    @extend_schema(tags=['app-projects'], summary='Create field in item')
    def post(self, request, pk, sk, ik):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        section = _get_section(sk, project)
        if not section:
            return _NOT_FOUND
        item = _get_item(ik, section)
        if not item:
            return _NOT_FOUND

        serializer = FieldCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        field = serializer.save(item=item)
        return Response(ProjectItemFieldSerializer(field).data, status=status.HTTP_201_CREATED)


class FieldUpdateView(APIView):
    permission_classes = [HasPermission('credentials.manage')]

    @extend_schema(tags=['app-projects'], summary='Update field')
    def patch(self, request, pk, sk, ik, fk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        section = _get_section(sk, project)
        if not section:
            return _NOT_FOUND
        item = _get_item(ik, section)
        if not item:
            return _NOT_FOUND
        field = _get_field(fk, item)
        if not field:
            return _NOT_FOUND

        # Allow re-encryption if value changes
        if 'value' in request.data:
            field.is_encrypted = False

        serializer = FieldCreateUpdateSerializer(field, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(ProjectItemFieldSerializer(field).data)


class FieldDeleteView(APIView):
    permission_classes = [HasPermission('credentials.manage')]

    @extend_schema(tags=['app-projects'], summary='Delete field')
    def delete(self, request, pk, sk, ik, fk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        section = _get_section(sk, project)
        if not section:
            return _NOT_FOUND
        item = _get_item(ik, section)
        if not item:
            return _NOT_FOUND
        field = _get_field(fk, item)
        if not field:
            return _NOT_FOUND
        field.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Reveal Password View ─────────────────────────────────────────────────────

class RevealPasswordView(APIView):
    permission_classes = [HasPermission('credentials.reveal')]

    @extend_schema(tags=['app-projects'], summary='Reveal encrypted field value')
    def post(self, request, pk, sk, ik, fk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        section = _get_section(sk, project)
        if not section:
            return _NOT_FOUND
        item = _get_item(ik, section)
        if not item:
            return _NOT_FOUND
        field = _get_field(fk, item)
        if not field:
            return _NOT_FOUND

        if not field.is_encrypted:
            return Response(
                {'error': {'code': 'not_encrypted', 'message': 'Field is not encrypted.'}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        plain = decrypt_value(field.value)

        try:
            AuditLog.objects.create(
                tenant=request.tenant,
                user=request.user,
                action='credentials.reveal',
                resource_type='ProjectItemField',
                resource_id=str(field.id),
                ip_address=request.META.get('REMOTE_ADDR'),
            )
        except Exception:
            # Audit failure must not block the response
            pass

        return Response({'value': plain})


# ─── Member Views ──────────────────────────────────────────────────────────────

class MemberListView(APIView):
    permission_classes = [HasPermission('projects.read')]

    @extend_schema(tags=['app-projects'], summary='List project members')
    def get(self, request, pk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        members = project.members.select_related('user').all()
        return Response({'members': ProjectMemberSerializer(members, many=True).data})


class MemberAddView(APIView):
    permission_classes = [HasPermission('projects.update')]

    @extend_schema(tags=['app-projects'], summary='Add member to project')
    def post(self, request, pk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND

        email = request.data.get('email')
        role = request.data.get('role', 'viewer')

        try:
            user = User.objects.get(email=email, tenant=request.tenant)
        except User.DoesNotExist:
            return Response(
                {'error': {'code': 'user_not_found', 'message': 'User not found in this tenant.'}},
                status=status.HTTP_404_NOT_FOUND,
            )

        member, created = ProjectMember.objects.get_or_create(
            project=project, user=user, defaults={'role': role}
        )
        if not created:
            member.role = role
            member.save(update_fields=['role'])

        return Response(
            ProjectMemberSerializer(member).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class MemberRemoveView(APIView):
    permission_classes = [HasPermission('projects.update')]

    @extend_schema(tags=['app-projects'], summary='Remove member from project')
    def delete(self, request, pk, mk):
        project = _get_project(pk, request.tenant)
        if not project:
            return _NOT_FOUND
        try:
            member = ProjectMember.objects.get(pk=mk, project=project)
        except ProjectMember.DoesNotExist:
            return _NOT_FOUND
        member.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
