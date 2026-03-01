"""
Admin views for User management.

Endpoints:
  GET    /api/v1/admin/users/                       → List users in tenant
  POST   /api/v1/admin/users/create/                → Create user
  POST   /api/v1/admin/users/invite/                → Invite user via email
  GET    /api/v1/admin/users/<pk>/                  → User detail
  PATCH  /api/v1/admin/users/<pk>/update/           → Update user
  POST   /api/v1/admin/users/<pk>/suspend/          → Toggle is_active
  POST   /api/v1/admin/users/<pk>/roles/            → Assign role to user
  DELETE /api/v1/admin/users/<pk>/roles/<role_pk>/  → Remove role from user
"""
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.auth_app.serializers import (
    AdminUserCreateSerializer,
    AdminUserListSerializer,
    AdminUserUpdateSerializer,
    InviteUserSerializer,
    UserRoleAssignSerializer,
)
from apps.auth_app.tokens import create_email_verification_token
from apps.rbac.models import Role, UserRole
from apps.rbac.permissions import HasPermission, check_plan_limit
from apps.rbac.serializers import UserRoleSerializer

User = get_user_model()


def _count_active_owners(tenant) -> int:
    """Count active users with the system Owner role in the given tenant."""
    return UserRole.objects.filter(
        role__name='Owner',
        role__is_system_role=True,
        user__tenant=tenant,
        user__is_active=True,
    ).count()


class UserListView(APIView):
    permission_classes = [HasPermission('users.read')]

    def get(self, request):
        users = User.objects.filter(tenant=request.tenant).order_by('-created_at')
        return Response({'users': AdminUserListSerializer(users, many=True).data})


class UserCreateView(APIView):
    permission_classes = [HasPermission('users.create')]

    def post(self, request):
        serializer = AdminUserCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        current_count = User.objects.filter(tenant=request.tenant).count()
        check_plan_limit(request.user, 'users', current_count)
        user = serializer.save()
        return Response(AdminUserListSerializer(user).data, status=status.HTTP_201_CREATED)


class UserInviteView(APIView):
    permission_classes = [HasPermission('users.invite')]

    def post(self, request):
        serializer = InviteUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email']
        role_id = serializer.validated_data.get('role_id')

        user = User.objects.create_user(
            email=email,
            name=email.split('@')[0],
            password=uuid.uuid4().hex,
            tenant=request.tenant,
            is_active=False,
        )

        if role_id:
            try:
                role = Role.objects.get(pk=role_id)
                if role.is_system_role or role.tenant_id == request.tenant.id:
                    UserRole.objects.get_or_create(user=user, role=role)
            except Role.DoesNotExist:
                pass

        token = create_email_verification_token(str(user.id))
        link = f'{settings.FRONTEND_URL}/accept-invite?token={token}'
        send_mail(
            subject='Invitation',
            message=f'You have been invited. Accept here: {link}',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=True,
        )
        return Response({'message': 'Invitation sent.'}, status=status.HTTP_201_CREATED)


class UserDetailView(APIView):
    permission_classes = [HasPermission('users.read')]

    def get(self, request, pk):
        try:
            user = User.objects.get(pk=pk, tenant=request.tenant)
        except User.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(AdminUserListSerializer(user).data)


class UserUpdateView(APIView):
    permission_classes = [HasPermission('users.update')]

    def patch(self, request, pk):
        try:
            user = User.objects.get(pk=pk, tenant=request.tenant)
        except User.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = AdminUserUpdateSerializer(user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        user.refresh_from_db()
        return Response(AdminUserListSerializer(user).data)


class UserSuspendView(APIView):
    permission_classes = [HasPermission('users.update')]

    def post(self, request, pk):
        try:
            user = User.objects.get(pk=pk, tenant=request.tenant)
        except User.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # Protect the last active Owner
        if user.is_active:
            is_owner = user.user_roles.filter(
                role__name='Owner', role__is_system_role=True
            ).exists()
            if is_owner and _count_active_owners(request.tenant) <= 1:
                return Response(
                    {'detail': 'Cannot suspend the last active Owner.'},
                    status=status.HTTP_403_FORBIDDEN,
                )

        user.is_active = not user.is_active
        user.save(update_fields=['is_active'])
        return Response(AdminUserListSerializer(user).data)


class UserRoleAssignView(APIView):
    permission_classes = [HasPermission('roles.assign')]

    def post(self, request, pk):
        try:
            user = User.objects.get(pk=pk, tenant=request.tenant)
        except User.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        serializer = UserRoleAssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        role_id = serializer.validated_data['role_id']

        try:
            role = Role.objects.get(pk=role_id)
        except Role.DoesNotExist:
            return Response({'detail': 'Role not found.'}, status=status.HTTP_404_NOT_FOUND)

        if not role.is_system_role and role.tenant_id != request.tenant.id:
            return Response(
                {'detail': 'Cannot assign role from another tenant.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        user_role, _ = UserRole.objects.get_or_create(user=user, role=role)
        return Response(UserRoleSerializer(user_role).data, status=status.HTTP_201_CREATED)


class UserRoleRemoveView(APIView):
    permission_classes = [HasPermission('roles.assign')]

    def delete(self, request, pk, role_pk):
        try:
            user = User.objects.get(pk=pk, tenant=request.tenant)
        except User.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        try:
            user_role = UserRole.objects.select_related('role').get(user=user, role_id=role_pk)
        except UserRole.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)

        # Protect the last active Owner
        if (
            user_role.role.is_system_role
            and user_role.role.name == 'Owner'
            and _count_active_owners(request.tenant) <= 1
        ):
            return Response(
                {'detail': 'Cannot remove the last Owner role.'},
                status=status.HTTP_403_FORBIDDEN,
            )

        user_role.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
