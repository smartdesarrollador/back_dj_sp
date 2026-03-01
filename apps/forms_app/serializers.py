"""
Serializers for the Forms module.
"""
from rest_framework import serializers

from apps.forms_app.models import Form, FormQuestion, FormResponse


class FormQuestionSerializer(serializers.ModelSerializer):
    class Meta:
        model = FormQuestion
        fields = ['id', 'order', 'label', 'question_type', 'options', 'required']
        read_only_fields = ['id']


class FormQuestionCreateSerializer(serializers.Serializer):
    label = serializers.CharField(max_length=255)
    question_type = serializers.ChoiceField(choices=FormQuestion.TYPES)
    order = serializers.IntegerField(required=False, default=0)
    options = serializers.ListField(
        child=serializers.CharField(max_length=100), required=False, default=list
    )
    required = serializers.BooleanField(required=False, default=False)


class FormSerializer(serializers.ModelSerializer):
    questions = FormQuestionSerializer(many=True, read_only=True)

    class Meta:
        model = Form
        fields = [
            'id', 'title', 'description', 'status', 'public_url_slug',
            'response_count', 'questions', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'response_count', 'created_at', 'updated_at']


class FormCreateUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    questions = FormQuestionCreateSerializer(many=True, required=False, default=list)


class FormResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = FormResponse
        fields = ['id', 'data', 'submitted_at']
        read_only_fields = ['id', 'submitted_at']


class PublicSubmitSerializer(serializers.Serializer):
    data = serializers.DictField(child=serializers.CharField(allow_blank=True))
