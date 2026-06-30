"""
Contacts views — address book CRUD with groups and CSV export.

URL namespace: /api/v1/app/contacts/

Endpoints:
  GET    /app/contacts/                  → list contacts (supports ?group= ?search=)
  POST   /app/contacts/                  → create contact
  GET    /app/contacts/<pk>/             → contact detail
  PATCH  /app/contacts/<pk>/             → update contact
  DELETE /app/contacts/<pk>/             → delete contact
  GET    /app/contacts/groups/           → list groups (HasFeature contact_groups)
  POST   /app/contacts/groups/           → create group
  GET    /app/contacts/groups/<pk>/      → group detail
  DELETE /app/contacts/groups/<pk>/      → delete group
  GET    /app/contacts/export/           → CSV export (HasFeature contact_export)
"""
import csv

from django.db.models import Q
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.contacts.models import Contact, ContactGroup
from apps.contacts.serializers import (
    ContactCreateUpdateSerializer,
    ContactGroupSerializer,
    ContactSerializer,
)
from apps.rbac.permissions import HasFeature, HasPermission, check_plan_limit, _user_has_permission
from apps.sharing.models import Share
from core.mixins import AuditMixin
from utils.plans import get_plan_limit

_IMPORT_MAX_ROWS = 1000

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)


def _get_contact(pk, tenant, user):
    try:
        return Contact.objects.get(pk=pk, tenant=tenant, user=user)
    except Contact.DoesNotExist:
        return None


def _get_group(pk, tenant, user):
    try:
        return ContactGroup.objects.get(pk=pk, tenant=tenant, user=user)
    except ContactGroup.DoesNotExist:
        return None


class ContactListCreateView(APIView):
    permission_classes = [HasPermission('contacts.read')]

    @extend_schema(
        tags=['app-contacts'],
        summary='List contacts',
        parameters=[
            OpenApiParameter('group', OpenApiTypes.UUID, description='Filter by group'),
            OpenApiParameter('search', OpenApiTypes.STR, description='Search by name/email'),
        ],
    )
    def get(self, request):
        shared_ids = Share.objects.filter(
            shared_with=request.user, resource_type='contact'
        ).values_list('resource_id', flat=True)
        qs = Contact.objects.filter(
            Q(tenant=request.tenant, user=request.user) | Q(pk__in=shared_ids)
        ).distinct()
        group = request.query_params.get('group')
        search = request.query_params.get('search')
        if group:
            qs = qs.filter(group__pk=group)
        if search:
            qs = qs.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(email__icontains=search)
            )
        contacts = ContactSerializer(qs, many=True).data
        return Response({'results': contacts, 'count': len(contacts), 'contacts': contacts})

    @extend_schema(tags=['app-contacts'], summary='Create contact')
    def post(self, request):
        if not _user_has_permission(request.user, 'contacts.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = Contact.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'contacts', count)
        serializer = ContactCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        group_id = data.pop('group', None)
        # Split name → first_name / last_name if name provided
        full_name = data.pop('name', '').strip()
        if full_name and not data.get('first_name'):
            parts = full_name.split(' ', 1)
            data['first_name'] = parts[0]
            data['last_name'] = parts[1] if len(parts) > 1 else data.get('last_name', '')
        group = None
        if group_id:
            group = _get_group(group_id, request.tenant, request.user)
        contact = Contact.objects.create(
            tenant=request.tenant,
            user=request.user,
            group=group,
            **data,
        )
        return Response(ContactSerializer(contact).data, status=status.HTTP_201_CREATED)


class ContactImportView(AuditMixin, APIView):
    """Bulk import contacts from a parsed file (client sends validated JSON rows)."""

    permission_classes = [HasPermission('contacts.create'), HasFeature('contact_import')]

    @extend_schema(tags=['app-contacts'], summary='Bulk import contacts')
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

        # Validate every row; invalid rows are reported but don't abort the batch.
        valid: list[dict] = []
        errors: list[dict] = []
        for idx, raw in enumerate(items):
            serializer = ContactCreateUpdateSerializer(data=raw if isinstance(raw, dict) else {})
            if not serializer.is_valid():
                errors.append({'index': idx, 'errors': serializer.errors})
                continue
            data = serializer.validated_data.copy()
            data.pop('group', None)  # imports ignore group FK
            full_name = data.pop('name', '').strip()
            if full_name and not data.get('first_name'):
                parts = full_name.split(' ', 1)
                data['first_name'] = parts[0]
                data['last_name'] = parts[1] if len(parts) > 1 else data.get('last_name', '')
            valid.append(data)

        # Enforce the plan limit partially: create up to the remaining slots, skip the rest.
        current = Contact.objects.filter(tenant=request.tenant, user=request.user).count()
        plan = getattr(request.tenant, 'plan', 'free')
        limit = get_plan_limit(plan, 'contacts')
        allowed = len(valid) if limit is None else max(0, limit - current)
        to_create = valid[:allowed]
        skipped = len(valid) - len(to_create)

        created = len(Contact.objects.bulk_create(
            [Contact(tenant=request.tenant, user=request.user, **d) for d in to_create]
        ))

        self.log_action(
            request,
            action='contacts.import',
            resource_type='Contact',
            extra={
                'created': created,
                'skipped': skipped,
                'errors': len(errors),
                'source': request.data.get('source', ''),
            },
        )
        return Response({'created': created, 'skipped': skipped, 'errors': errors})


class ContactDetailView(APIView):
    permission_classes = [HasPermission('contacts.read')]

    @extend_schema(tags=['app-contacts'], summary='Get contact detail')
    def get(self, request, pk):
        contact = _get_contact(pk, request.tenant, request.user)
        if not contact:
            return _NOT_FOUND
        return Response({'contact': ContactSerializer(contact).data})

    @extend_schema(tags=['app-contacts'], summary='Update contact')
    def patch(self, request, pk):
        if not _user_has_permission(request.user, 'contacts.update'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        contact = _get_contact(pk, request.tenant, request.user)
        if not contact:
            return _NOT_FOUND
        serializer = ContactCreateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        group_id = data.pop('group', None)
        if group_id is not None:
            contact.group = _get_group(group_id, request.tenant, request.user)
        full_name = data.pop('name', '').strip()
        if full_name:
            parts = full_name.split(' ', 1)
            data['first_name'] = parts[0]
            data['last_name'] = parts[1] if len(parts) > 1 else ''
        for field, value in data.items():
            setattr(contact, field, value)
        contact.save()
        return Response(ContactSerializer(contact).data)

    @extend_schema(tags=['app-contacts'], summary='Delete contact')
    def delete(self, request, pk):
        if not _user_has_permission(request.user, 'contacts.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        contact = _get_contact(pk, request.tenant, request.user)
        if not contact:
            return _NOT_FOUND
        contact.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ContactGroupListCreateView(APIView):
    permission_classes = [HasPermission('contacts.read'), HasFeature('contact_groups')]

    @extend_schema(tags=['app-contacts'], summary='List contact groups')
    def get(self, request):
        groups = ContactGroup.objects.filter(tenant=request.tenant, user=request.user)
        groups_data = ContactGroupSerializer(groups, many=True).data
        return Response({'results': groups_data, 'count': len(groups_data), 'groups': groups_data})

    @extend_schema(tags=['app-contacts'], summary='Create contact group')
    def post(self, request):
        serializer = ContactGroupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        group = serializer.save(tenant=request.tenant, user=request.user)
        return Response(ContactGroupSerializer(group).data, status=status.HTTP_201_CREATED)


class ContactGroupDetailView(APIView):
    permission_classes = [HasPermission('contacts.read'), HasFeature('contact_groups')]

    @extend_schema(tags=['app-contacts'], summary='Delete contact group')
    def delete(self, request, pk):
        group = _get_group(pk, request.tenant, request.user)
        if not group:
            return _NOT_FOUND
        group.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ContactExportView(APIView):
    permission_classes = [HasPermission('contacts.read'), HasFeature('contact_export')]

    @extend_schema(tags=['app-contacts'], summary='Export contacts as CSV')
    def get(self, request):
        contacts = Contact.objects.filter(tenant=request.tenant, user=request.user)
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="contacts.csv"'
        writer = csv.writer(response)
        writer.writerow(['first_name', 'last_name', 'email', 'phone', 'company', 'job_title'])
        for c in contacts:
            writer.writerow([c.first_name, c.last_name, c.email, c.phone, c.company, c.job_title])
        return response
