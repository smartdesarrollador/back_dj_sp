"""
Serializers for the Tasks module.
"""
from rest_framework import serializers

from apps.tasks.models import Task, TaskBoard, TaskComment


class TaskBoardSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskBoard
        fields = ['id', 'name', 'description', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class TaskBoardCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default='')


class TaskSerializer(serializers.ModelSerializer):
    board_id = serializers.UUIDField(source='board.id', read_only=True)
    parent_task_id = serializers.UUIDField(source='parent_task.id', read_only=True, allow_null=True)
    assignee_id = serializers.UUIDField(source='assignee.id', read_only=True, allow_null=True)
    assignee_name = serializers.SerializerMethodField()
    subtasks_count = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = [
            'id', 'board_id', 'parent_task_id', 'title', 'description',
            'status', 'priority', 'assignee_id', 'assignee_name',
            'due_date', 'order', 'subtasks_count', 'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_assignee_name(self, obj) -> str | None:
        if obj.assignee:
            return getattr(obj.assignee, 'name', str(obj.assignee))
        return None

    def get_subtasks_count(self, obj) -> int:
        return obj.subtasks.count()


class TaskCreateUpdateSerializer(serializers.Serializer):
    board = serializers.UUIDField(required=False)
    title = serializers.CharField(max_length=500)
    description = serializers.CharField(required=False, allow_blank=True, default='')
    status = serializers.ChoiceField(choices=Task.STATUS_CHOICES, required=False, default='todo')
    priority = serializers.ChoiceField(choices=Task.PRIORITY_CHOICES, required=False, default='medium')
    assignee = serializers.UUIDField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    parent_task = serializers.UUIDField(required=False, allow_null=True)
    order = serializers.IntegerField(required=False, min_value=0, default=0)


class TaskCommentSerializer(serializers.ModelSerializer):
    task_id = serializers.UUIDField(source='task.id', read_only=True)
    user_id = serializers.UUIDField(source='user.id', read_only=True, allow_null=True)
    user_name = serializers.SerializerMethodField()

    class Meta:
        model = TaskComment
        fields = ['id', 'task_id', 'user_id', 'user_name', 'content', 'created_at']
        read_only_fields = ['id', 'created_at']

    def get_user_name(self, obj) -> str | None:
        if obj.user:
            return getattr(obj.user, 'name', str(obj.user))
        return None


class TaskCommentCreateSerializer(serializers.Serializer):
    content = serializers.CharField()
