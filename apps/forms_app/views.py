"""
Forms views — form builder CRUD with questions, responses and CSV export.

URL namespace: /api/v1/app/forms/

Endpoints:
  GET    /app/forms/                       → list forms
  POST   /app/forms/                       → create form with questions
  GET    /app/forms/<pk>/                  → form detail
  PATCH  /app/forms/<pk>/                  → update form
  DELETE /app/forms/<pk>/                  → delete form
  POST   /app/forms/<pk>/activate/         → change status to active
  GET    /app/forms/<pk>/responses/        → list responses
  GET    /app/forms/<pk>/export/           → export responses as CSV
  POST   /app/forms/public/<slug>/submit/  → public form submission (no auth)
"""
import csv
import io

from django.db import transaction
from django.db.models import F
from django.http import HttpResponse
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.forms_app.models import Form, FormQuestion, FormResponse
from apps.forms_app.serializers import (
    FormCreateUpdateSerializer,
    FormResponseSerializer,
    FormSerializer,
    PublicSubmitSerializer,
)
from apps.rbac.permissions import HasFeature, HasPermission, check_plan_limit

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)


def _get_form(pk, tenant, user):
    """Return Form for tenant+user or None."""
    try:
        return Form.objects.get(pk=pk, tenant=tenant, user=user)
    except Form.DoesNotExist:
        return None


def _create_questions(form, questions_data):
    """Bulk-create FormQuestion instances for a form."""
    FormQuestion.objects.bulk_create([
        FormQuestion(
            form=form,
            label=q['label'],
            question_type=q['question_type'],
            order=q.get('order', 0),
            options=q.get('options', []),
            required=q.get('required', False),
        )
        for q in questions_data
    ])


class FormListCreateView(APIView):
    permission_classes = [HasPermission('forms.read')]

    def get(self, request):
        qs = Form.objects.filter(tenant=request.tenant, user=request.user)
        status_filter = request.query_params.get('status')
        search = request.query_params.get('search')
        if status_filter:
            qs = qs.filter(status=status_filter)
        if search:
            qs = qs.filter(title__icontains=search)
        return Response({'forms': FormSerializer(qs, many=True).data})

    def post(self, request):
        if not request.user.has_perm('forms.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = Form.objects.filter(tenant=request.tenant, user=request.user).count()
        check_plan_limit(request.user, 'forms', count)
        serializer = FormCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        questions_data = serializer.validated_data.pop('questions', [])
        # Check question limit
        if questions_data:
            check_plan_limit(request.user, 'form_questions', len(questions_data) - 1)
        with transaction.atomic():
            form = Form.objects.create(
                tenant=request.tenant,
                user=request.user,
                **serializer.validated_data,
            )
            _create_questions(form, questions_data)
        return Response(
            {'form': FormSerializer(form).data}, status=status.HTTP_201_CREATED
        )


class FormDetailView(APIView):
    permission_classes = [HasPermission('forms.read')]

    def get(self, request, pk):
        form = _get_form(pk, request.tenant, request.user)
        if not form:
            return _NOT_FOUND
        return Response({'form': FormSerializer(form).data})

    def patch(self, request, pk):
        if not request.user.has_perm('forms.update'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        form = _get_form(pk, request.tenant, request.user)
        if not form:
            return _NOT_FOUND
        serializer = FormCreateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        questions_data = serializer.validated_data.pop('questions', None)
        for field, value in serializer.validated_data.items():
            setattr(form, field, value)
        with transaction.atomic():
            form.save()
            if questions_data is not None:
                form.questions.all().delete()
                _create_questions(form, questions_data)
        return Response({'form': FormSerializer(form).data})

    def delete(self, request, pk):
        if not request.user.has_perm('forms.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        form = _get_form(pk, request.tenant, request.user)
        if not form:
            return _NOT_FOUND
        form.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class FormActivateView(APIView):
    permission_classes = [HasPermission('forms.activate')]

    def post(self, request, pk):
        form = _get_form(pk, request.tenant, request.user)
        if not form:
            return _NOT_FOUND
        form.status = 'active'
        form.save(update_fields=['status', 'updated_at'])
        return Response({'form': FormSerializer(form).data})


class FormResponsesView(APIView):
    permission_classes = [HasPermission('forms.read')]

    def get(self, request, pk):
        form = _get_form(pk, request.tenant, request.user)
        if not form:
            return _NOT_FOUND
        responses = FormResponse.objects.filter(form=form)
        return Response({'responses': FormResponseSerializer(responses, many=True).data})


class FormExportView(APIView):
    permission_classes = [HasPermission('forms.read'), HasFeature('form_export_csv')]

    def get(self, request, pk):
        form = _get_form(pk, request.tenant, request.user)
        if not form:
            return _NOT_FOUND
        questions = list(form.questions.all().order_by('order'))
        responses = list(FormResponse.objects.filter(form=form))
        output = io.StringIO()
        writer = csv.writer(output)
        headers = [q.label for q in questions] + ['submitted_at']
        writer.writerow(headers)
        for resp in responses:
            row = [resp.data.get(str(q.id), resp.data.get(q.label, '')) for q in questions]
            row.append(resp.submitted_at.isoformat())
            writer.writerow(row)
        content = output.getvalue()
        response = HttpResponse(content, content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="form_{form.id}_responses.csv"'
        return response


class PublicFormSubmitView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request, slug):
        try:
            form = Form.objects.get(public_url_slug=slug, status='active')
        except Form.DoesNotExist:
            return _NOT_FOUND
        serializer = PublicSubmitSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        submitted_data = serializer.validated_data['data']
        # Validate required fields
        required_questions = form.questions.filter(required=True)
        missing = [
            q.label for q in required_questions
            if not submitted_data.get(str(q.id)) and not submitted_data.get(q.label)
        ]
        if missing:
            return Response(
                {'error': {'code': 'missing_required_fields', 'fields': missing}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Check response limit (tenant-level)
        response_count = FormResponse.objects.filter(form=form).count()
        from utils.plans import get_plan_limit
        limit = get_plan_limit(form.tenant.plan, 'form_responses')
        if limit is not None and response_count >= limit:
            return Response(
                {'error': {'code': 'response_limit_reached', 'message': 'Response limit reached.'}},
                status=status.HTTP_402_PAYMENT_REQUIRED,
            )
        respondent_ip = request.META.get('REMOTE_ADDR')
        with transaction.atomic():
            FormResponse.objects.create(
                form=form,
                data=submitted_data,
                respondent_ip=respondent_ip,
            )
            Form.objects.filter(pk=form.pk).update(response_count=F('response_count') + 1)
        return Response({'status': 'submitted'}, status=status.HTTP_201_CREATED)
