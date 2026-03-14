"""
Tasks views — Kanban boards, tasks, subtasks and comments.

URL namespace: /api/v1/app/tasks/

Endpoints:
  GET    /app/tasks/boards/                    → list boards
  POST   /app/tasks/boards/                    → create board
  GET    /app/tasks/boards/<pk>/               → board detail
  PATCH  /app/tasks/boards/<pk>/               → update board
  DELETE /app/tasks/boards/<pk>/               → delete board
  GET    /app/tasks/                           → list tasks (supports ?board= ?status= ?assignee= ?priority= ?search=)
  POST   /app/tasks/                           → create task
  PATCH  /app/tasks/reorder/                   → bulk reorder tasks
  GET    /app/tasks/<pk>/                      → task detail (with subtasks)
  PATCH  /app/tasks/<pk>/                      → update task
  DELETE /app/tasks/<pk>/                      → delete task
  GET    /app/tasks/<task_pk>/comments/        → list comments
  POST   /app/tasks/<task_pk>/comments/        → create comment
"""
from django.contrib.auth import get_user_model
from drf_spectacular.utils import OpenApiParameter, extend_schema
from drf_spectacular.types import OpenApiTypes
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.permissions import HasFeature, HasPermission, _user_has_permission, check_plan_limit
from apps.tasks.models import Task, TaskBoard, TaskComment
from apps.tasks.serializers import (
    TaskBoardCreateSerializer,
    TaskBoardSerializer,
    TaskCommentCreateSerializer,
    TaskCommentSerializer,
    TaskCreateUpdateSerializer,
    TaskSerializer,
)
from core.mixins import AuditMixin
from utils.plans import plan_has_feature

User = get_user_model()

_NOT_FOUND = Response(
    {'error': {'code': 'not_found', 'message': 'Not found.'}}, status=404
)


def _get_board(pk, tenant):
    try:
        return TaskBoard.objects.get(pk=pk, tenant=tenant)
    except TaskBoard.DoesNotExist:
        return None


def _get_task(pk, tenant):
    try:
        return Task.objects.get(pk=pk, tenant=tenant)
    except Task.DoesNotExist:
        return None


def _get_comment(pk, task):
    try:
        return TaskComment.objects.get(pk=pk, task=task)
    except TaskComment.DoesNotExist:
        return None


class TaskBoardListCreateView(APIView):
    permission_classes = [HasPermission('tasks.read')]

    @extend_schema(tags=['app-tasks'], summary='List task boards')
    def get(self, request):
        boards = TaskBoard.objects.filter(tenant=request.tenant)
        return Response({'boards': TaskBoardSerializer(boards, many=True).data})

    @extend_schema(tags=['app-tasks'], summary='Create task board')
    def post(self, request):
        if not _user_has_permission(request.user, 'boards.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = TaskBoard.objects.filter(tenant=request.tenant).count()
        check_plan_limit(request.user, 'task_boards', count)
        serializer = TaskBoardCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        board = TaskBoard.objects.create(
            tenant=request.tenant,
            created_by=request.user,
            **serializer.validated_data,
        )
        return Response(TaskBoardSerializer(board).data, status=status.HTTP_201_CREATED)


class TaskBoardDetailView(APIView):
    permission_classes = [HasPermission('tasks.read')]

    @extend_schema(tags=['app-tasks'], summary='Get task board detail')
    def get(self, request, pk):
        board = _get_board(pk, request.tenant)
        if not board:
            return _NOT_FOUND
        return Response({'board': TaskBoardSerializer(board).data})

    @extend_schema(tags=['app-tasks'], summary='Update task board')
    def patch(self, request, pk):
        if not _user_has_permission(request.user, 'boards.admin'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        board = _get_board(pk, request.tenant)
        if not board:
            return _NOT_FOUND
        serializer = TaskBoardCreateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for field, value in serializer.validated_data.items():
            setattr(board, field, value)
        board.save()
        return Response(TaskBoardSerializer(board).data)

    @extend_schema(tags=['app-tasks'], summary='Delete task board')
    def delete(self, request, pk):
        if not _user_has_permission(request.user, 'boards.admin'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        board = _get_board(pk, request.tenant)
        if not board:
            return _NOT_FOUND
        board.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskListCreateView(APIView):
    permission_classes = [HasPermission('tasks.read')]

    @extend_schema(
        tags=['app-tasks'],
        summary='List tasks',
        parameters=[
            OpenApiParameter('board', OpenApiTypes.UUID, description='Filter by board'),
            OpenApiParameter('status', OpenApiTypes.STR, description='Filter by status'),
            OpenApiParameter('assignee', OpenApiTypes.UUID, description='Filter by assignee'),
            OpenApiParameter('priority', OpenApiTypes.STR, description='Filter by priority'),
            OpenApiParameter('search', OpenApiTypes.STR, description='Search in title'),
        ],
    )
    def get(self, request):
        qs = Task.objects.filter(tenant=request.tenant)
        board = request.query_params.get('board')
        task_status = request.query_params.get('status')
        assignee = request.query_params.get('assignee')
        priority = request.query_params.get('priority')
        search = request.query_params.get('search')
        if board:
            qs = qs.filter(board__pk=board)
        if task_status:
            qs = qs.filter(status=task_status)
        if assignee:
            qs = qs.filter(assignee__pk=assignee)
        if priority:
            qs = qs.filter(priority=priority)
        if search:
            qs = qs.filter(title__icontains=search)
        return Response({'tasks': TaskSerializer(qs, many=True).data})

    @extend_schema(tags=['app-tasks'], summary='Create task')
    def post(self, request):
        if not _user_has_permission(request.user, 'tasks.create'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        count = Task.objects.filter(tenant=request.tenant).count()
        check_plan_limit(request.user, 'tasks', count)
        serializer = TaskCreateUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()

        board_id = data.pop('board', None)
        assignee_id = data.pop('assignee', None)
        parent_task_id = data.pop('parent_task', None)

        board = None
        if board_id:
            try:
                board = TaskBoard.objects.get(pk=board_id, tenant=request.tenant)
            except TaskBoard.DoesNotExist:
                return Response(
                    {'error': {'code': 'not_found', 'message': 'Board not found.'}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            board, _ = TaskBoard.objects.get_or_create(
                tenant=request.tenant,
                name='General',
                defaults={'created_by': request.user},
            )

        assignee = None
        if assignee_id:
            if not plan_has_feature(request.tenant.plan, 'task_assign'):
                from core.exceptions import FeatureNotAvailable
                raise FeatureNotAvailable()
            try:
                assignee = User.objects.get(pk=assignee_id, tenant=request.tenant)
            except User.DoesNotExist:
                return Response(
                    {'error': {'code': 'not_found', 'message': 'Assignee not found.'}},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        parent_task = None
        if parent_task_id:
            try:
                parent_task = Task.objects.get(pk=parent_task_id, tenant=request.tenant)
            except Task.DoesNotExist:
                return Response(
                    {'error': {'code': 'not_found', 'message': 'Parent task not found.'}},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        task = Task.objects.create(
            tenant=request.tenant,
            board=board,
            assignee=assignee,
            parent_task=parent_task,
            created_by=request.user,
            **data,
        )
        return Response(TaskSerializer(task).data, status=status.HTTP_201_CREATED)


class TaskReorderView(APIView):
    permission_classes = [HasPermission('tasks.read')]

    @extend_schema(tags=['app-tasks'], summary='Bulk reorder tasks')
    def patch(self, request):
        items = request.data if isinstance(request.data, list) else []
        tasks_to_update = []
        for item in items:
            try:
                task = Task.objects.get(pk=item['id'], tenant=request.tenant)
                task.order = int(item['order'])
                tasks_to_update.append(task)
            except (Task.DoesNotExist, KeyError, ValueError):
                continue
        Task.objects.bulk_update(tasks_to_update, ['order'])
        return Response({'updated': len(tasks_to_update)})


class TaskDetailView(AuditMixin, APIView):
    permission_classes = [HasPermission('tasks.read')]

    @extend_schema(tags=['app-tasks'], summary='Get task detail with subtasks')
    def get(self, request, pk):
        task = _get_task(pk, request.tenant)
        if not task:
            return _NOT_FOUND
        data = TaskSerializer(task).data
        subtasks = Task.objects.filter(parent_task=task)
        data['subtasks'] = TaskSerializer(subtasks, many=True).data
        return Response({'task': data})

    @extend_schema(tags=['app-tasks'], summary='Update task')
    def patch(self, request, pk):
        if not _user_has_permission(request.user, 'tasks.update'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        task = _get_task(pk, request.tenant)
        if not task:
            return _NOT_FOUND
        serializer = TaskCreateUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data.copy()

        if 'assignee' in data:
            assignee_id = data.pop('assignee')
            if assignee_id is None:
                task.assignee = None
            else:
                if not plan_has_feature(request.tenant.plan, 'task_assign'):
                    from core.exceptions import FeatureNotAvailable
                    raise FeatureNotAvailable()
                try:
                    task.assignee = User.objects.get(pk=assignee_id, tenant=request.tenant)
                except User.DoesNotExist:
                    return Response(
                        {'error': {'code': 'not_found', 'message': 'Assignee not found.'}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        data.pop('board', None)
        data.pop('parent_task', None)

        for field, value in data.items():
            setattr(task, field, value)
        task.save()
        return Response(TaskSerializer(task).data)

    @extend_schema(tags=['app-tasks'], summary='Delete task')
    def delete(self, request, pk):
        if not _user_has_permission(request.user, 'tasks.delete'):
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied()
        task = _get_task(pk, request.tenant)
        if not task:
            return _NOT_FOUND
        self.log_action(request, 'delete', 'task', str(task.pk))
        task.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TaskCommentListCreateView(APIView):
    permission_classes = [HasPermission('tasks.read')]

    @extend_schema(tags=['app-tasks'], summary='List task comments')
    def get(self, request, task_pk):
        task = _get_task(task_pk, request.tenant)
        if not task:
            return _NOT_FOUND
        comments = TaskComment.objects.filter(task=task)
        return Response({'comments': TaskCommentSerializer(comments, many=True).data})

    @extend_schema(tags=['app-tasks'], summary='Create task comment')
    def post(self, request, task_pk):
        task = _get_task(task_pk, request.tenant)
        if not task:
            return _NOT_FOUND
        serializer = TaskCommentCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comment = TaskComment.objects.create(
            task=task,
            user=request.user,
            **serializer.validated_data,
        )
        return Response(TaskCommentSerializer(comment).data, status=status.HTTP_201_CREATED)
