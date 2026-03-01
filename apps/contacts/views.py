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
from apps.rbac.permissions import HasFeature, HasPermission, check_plan_limit

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
        qs = Contact.objects.filter(tenant=request.tenant, user=request.user)
        group = request.query_params.get('group')
        search = request.query_params.get('search')
        if group:
            qs = qs.filter(group__pk=group)
        if search:
            qs = qs.filter(first_name__icontains=search) | Contact.objects.filter(
                tenant=request.tenant, user=request.user, last_name__icontains=search
            ) | Contact.objects.filter(
                tenant=request.tenant, user=request.user, email__icontains=search
            )
            qs = qs.distinct()
        return Response({'contacts': ContactSerializer(qs, many=True).data})

    @extend_schema(tags=['app-contacts'], summary='Create contact')
    def post(self, request):
        if not request.user.has_perm('contacts.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = Contact.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'contacts', count)
        serializer = ContactCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()
        group_id = data.pop('group', None)
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
        if not request.user.has_perm('contacts.update'):
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
        for field, value in data.items():
            setattr(contact, field, value)
        contact.save()
        return Response(ContactSerializer(contact).data)

    @extend_schema(tags=['app-contacts'], summary='Delete contact')
    def delete(self, request, pk):
        if not request.user.has_perm('contacts.delete'):
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
        return Response({'groups': ContactGroupSerializer(groups, many=True).data})

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
