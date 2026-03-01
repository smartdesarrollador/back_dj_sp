"""
Support views — ticket management with comments and CSV export.

URL namespace: /api/v1/support/

Endpoints:
  GET    /support/tickets/              → list tickets (filtered by role)
  POST   /support/tickets/             → create ticket
  GET    /support/tickets/export/      → export tickets as CSV
  GET    /support/tickets/<pk>/        → ticket detail
  PATCH  /support/tickets/<pk>/        → update ticket (status, priority, assigned_to)
  POST   /support/tickets/<pk>/close/  → close ticket
  GET    /support/tickets/<pk>/comments/  → list comments
  POST   /support/tickets/<pk>/comments/  → add comment
"""
import csv
import io

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasFeature, HasPermission, _user_has_permission
from apps.support.models import SupportTicket, TicketComment
from apps.support.serializers import (
    SupportTicketSerializer,
    TicketCommentCreateSerializer,
    TicketCommentSerializer,
    TicketCreateSerializer,
    TicketUpdateSerializer,
)
from core.mixins import AuditMixin

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)


def _is_agent(user) -> bool:
    """Return True if the user has agent-level access (superuser or support.assign perm)."""
    return user.is_superuser or _user_has_permission(user, 'support.assign')


def _get_ticket(pk, tenant, user):
    """Return SupportTicket scoped by tenant. Agents see all; clients see only their own."""
    try:
        qs = SupportTicket.objects.filter(pk=pk, tenant=tenant)
        if not _is_agent(user):
            qs = qs.filter(client=user)
        return qs.get()
    except SupportTicket.DoesNotExist:
        return None


class TicketListCreateView(AuditMixin, APIView):
    permission_classes = [HasPermission('support.read')]

    @extend_schema(
        tags=['support'],
        summary='List support tickets',
        parameters=[
            OpenApiParameter('status', OpenApiTypes.STR, description='Filter by status'),
            OpenApiParameter('priority', OpenApiTypes.STR, description='Filter by priority'),
            OpenApiParameter('category', OpenApiTypes.STR, description='Filter by category'),
            OpenApiParameter('search', OpenApiTypes.STR, description='Search in subject/client_email'),
        ],
    )
    def get(self, request):
        qs = SupportTicket.objects.filter(tenant=request.tenant)
        if not _is_agent(request.user):
            qs = qs.filter(client=request.user)

        status_filter = request.query_params.get('status')
        priority_filter = request.query_params.get('priority')
        category_filter = request.query_params.get('category')
        search = request.query_params.get('search')

        if status_filter:
            qs = qs.filter(status=status_filter)
        if priority_filter:
            qs = qs.filter(priority=priority_filter)
        if category_filter:
            qs = qs.filter(category=category_filter)
        if search:
            qs = (
                qs.filter(subject__icontains=search)
                | SupportTicket.objects.filter(
                    tenant=request.tenant, client_email__icontains=search
                )
            ).distinct()
            if not _is_agent(request.user):
                qs = qs.filter(client=request.user)

        return Response({'tickets': SupportTicketSerializer(qs, many=True).data})

    @extend_schema(tags=['support'], summary='Create support ticket')
    def post(self, request):
        if not _user_has_permission(request.user, 'support.create'):
            raise PermissionDenied()
        serializer = TicketCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ticket = SupportTicket.objects.create(
            tenant=request.tenant,
            client=request.user,
            client_email=request.data.get('client_email') or request.user.email,
            **serializer.validated_data,
        )
        self.log_action(
            request,
            action='ticket_created',
            resource_type='support_ticket',
            resource_id=str(ticket.pk),
        )
        return Response(
            {'ticket': SupportTicketSerializer(ticket).data},
            status=status.HTTP_201_CREATED,
        )


class TicketDetailView(AuditMixin, APIView):
    permission_classes = [HasPermission('support.read')]

    @extend_schema(tags=['support'], summary='Get ticket detail')
    def get(self, request, pk):
        ticket = _get_ticket(pk, request.tenant, request.user)
        if not ticket:
            return _NOT_FOUND
        return Response({'ticket': SupportTicketSerializer(ticket).data})

    @extend_schema(tags=['support'], summary='Update ticket (status, priority, assigned_to)')
    def patch(self, request, pk):
        if not _user_has_permission(request.user, 'support.update'):
            raise PermissionDenied()
        ticket = _get_ticket(pk, request.tenant, request.user)
        if not ticket:
            return _NOT_FOUND
        serializer = TicketUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        new_status = serializer.validated_data.get('status')
        if new_status == 'resolved' and ticket.status != 'resolved':
            ticket.resolved_at = timezone.now()

        for field, value in serializer.validated_data.items():
            if field == 'assigned_to':
                if value is not None:
                    from django.contrib.auth import get_user_model
                    User = get_user_model()
                    try:
                        ticket.assigned_to = User.objects.get(pk=value, tenant=request.tenant)
                    except User.DoesNotExist:
                        return Response(
                            {'error': {'code': 'not_found', 'message': 'Assigned user not found.'}},
                            status=404,
                        )
                else:
                    ticket.assigned_to = None
            else:
                setattr(ticket, field, value)

        ticket.save()
        self.log_action(
            request,
            action='ticket_updated',
            resource_type='support_ticket',
            resource_id=str(ticket.pk),
        )
        return Response({'ticket': SupportTicketSerializer(ticket).data})


class TicketCloseView(AuditMixin, APIView):
    permission_classes = [HasPermission('support.close')]

    @extend_schema(tags=['support'], summary='Close a support ticket')
    def post(self, request, pk):
        ticket = _get_ticket(pk, request.tenant, request.user)
        if not ticket:
            return _NOT_FOUND
        ticket.status = 'closed'
        ticket.save(update_fields=['status', 'updated_at'])
        self.log_action(
            request,
            action='ticket_closed',
            resource_type='support_ticket',
            resource_id=str(ticket.pk),
        )
        return Response({'ticket': SupportTicketSerializer(ticket).data})


class TicketCommentView(APIView):
    permission_classes = [HasPermission('support.read')]

    @extend_schema(tags=['support'], summary='List ticket comments')
    def get(self, request, pk):
        ticket = _get_ticket(pk, request.tenant, request.user)
        if not ticket:
            return _NOT_FOUND
        comments = TicketComment.objects.filter(ticket=ticket)
        return Response({'comments': TicketCommentSerializer(comments, many=True).data})

    @extend_schema(tags=['support'], summary='Add comment to ticket')
    def post(self, request, pk):
        if not _user_has_permission(request.user, 'support.update'):
            raise PermissionDenied()
        ticket = _get_ticket(pk, request.tenant, request.user)
        if not ticket:
            return _NOT_FOUND
        serializer = TicketCommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role = 'agent' if _is_agent(request.user) else 'client'
        comment = TicketComment.objects.create(
            ticket=ticket,
            author=getattr(request.user, 'name', str(request.user)),
            role=role,
            message=serializer.validated_data['message'],
        )
        return Response(
            {'comment': TicketCommentSerializer(comment).data},
            status=status.HTTP_201_CREATED,
        )


class TicketExportView(APIView):
    permission_classes = [HasPermission('support.read'), HasFeature('support_export')]

    @extend_schema(tags=['support'], summary='Export tickets as CSV')
    def get(self, request):
        qs = SupportTicket.objects.filter(tenant=request.tenant).order_by('-created_at')
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            'reference', 'status', 'subject', 'category', 'priority',
            'client_email', 'created_at', 'resolved_at',
        ])
        for ticket in qs:
            writer.writerow([
                ticket.reference,
                ticket.status,
                ticket.subject,
                ticket.category,
                ticket.priority,
                ticket.client_email,
                ticket.created_at.isoformat(),
                ticket.resolved_at.isoformat() if ticket.resolved_at else '',
            ])
        content = output.getvalue()
        response = HttpResponse(content, content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="support_tickets.csv"'
        return response
