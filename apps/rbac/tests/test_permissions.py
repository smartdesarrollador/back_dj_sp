"""
Tests para el middleware RBAC: HasPermission, HasFeature, check_plan_limit,
decoradores y TenantModelViewSet.
"""
import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory, APITestCase

from apps.rbac.models import Permission, Role, RolePermission, UserRole
from apps.rbac.permissions import (
    HasFeature,
    HasPermission,
    _user_has_permission,
    check_plan_limit,
)
from core.exceptions import FeatureNotAvailable, PlanLimitExceeded


_FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


# ─── Helpers ──────────────────────────────────────────────────────────────────

def make_tenant(plan: str = 'free'):
    from apps.tenants.models import Tenant
    slug = f'tenant-{uuid.uuid4().hex[:8]}'
    return Tenant.objects.create(
        name=slug,
        slug=slug,
        subdomain=slug,
        plan=plan,
    )


def make_user(tenant, email: str | None = None, is_superuser: bool = False):
    from apps.auth_app.models import User
    email = email or f'user-{uuid.uuid4().hex[:8]}@test.com'
    return User.objects.create_user(
        email=email,
        name='Test User',
        password='pass123',
        tenant=tenant,
        is_superuser=is_superuser,
        email_verified=True,
    )


def make_permission(codename: str = 'projects.create') -> Permission:
    resource, _, action = codename.partition('.')
    perm, _ = Permission.objects.get_or_create(
        codename=codename,
        defaults={'name': codename, 'resource': resource, 'action': action},
    )
    return perm


def make_role(tenant=None, name: str | None = None, inherits_from=None) -> Role:
    name = name or f'role-{uuid.uuid4().hex[:6]}'
    return Role.objects.create(
        tenant=tenant,
        name=name,
        inherits_from=inherits_from,
    )


def assign_role(user, role, expires_at=None) -> UserRole:
    return UserRole.objects.create(user=user, role=role, expires_at=expires_at)


def assign_permission(role, permission, scope='all') -> RolePermission:
    return RolePermission.objects.create(role=role, permission=permission, scope=scope)


# ─── HasPermission Tests ───────────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class HasPermissionTest(TestCase):
    def setUp(self):
        self.tenant = make_tenant('professional')
        self.user = make_user(self.tenant)
        self.perm = make_permission('projects.create')
        self.role = make_role(self.tenant)

    def _make_request(self, user):
        request = MagicMock()
        request.user = user
        return request

    @patch('apps.rbac.permissions.cache')
    def test_user_with_permission_passes(self, mock_cache):
        mock_cache.get.return_value = None
        assign_permission(self.role, self.perm)
        assign_role(self.user, self.role)

        result = _user_has_permission(self.user, 'projects.create')
        self.assertTrue(result)

    @patch('apps.rbac.permissions.cache')
    def test_user_without_permission_fails(self, mock_cache):
        mock_cache.get.return_value = None
        # No role assigned
        result = _user_has_permission(self.user, 'projects.create')
        self.assertFalse(result)

    def test_superuser_always_passes(self):
        superuser = make_user(self.tenant, is_superuser=True)
        result = _user_has_permission(superuser, 'any.permission')
        self.assertTrue(result)

    @patch('apps.rbac.permissions.cache')
    def test_expired_role_denied(self, mock_cache):
        mock_cache.get.return_value = None
        assign_permission(self.role, self.perm)
        past = timezone.now() - timedelta(hours=1)
        assign_role(self.user, self.role, expires_at=past)

        result = _user_has_permission(self.user, 'projects.create')
        self.assertFalse(result)

    @patch('apps.rbac.permissions.cache')
    def test_inherited_permission_via_parent_role(self, mock_cache):
        mock_cache.get.return_value = None
        parent_role = make_role(self.tenant, name='parent')
        assign_permission(parent_role, self.perm)
        child_role = make_role(self.tenant, name='child', inherits_from=parent_role)
        assign_role(self.user, child_role)

        result = _user_has_permission(self.user, 'projects.create')
        self.assertTrue(result)

    @patch('apps.rbac.permissions.cache')
    def test_cache_hit_returns_cached_value(self, mock_cache):
        mock_cache.get.return_value = True

        result = _user_has_permission(self.user, 'projects.create')
        self.assertTrue(result)
        mock_cache.get.assert_called_once()

    def test_has_permission_factory_returns_class(self):
        cls = HasPermission('projects.create')
        self.assertTrue(issubclass(cls, __import__('rest_framework.permissions', fromlist=['BasePermission']).BasePermission))
        self.assertIn('HasPermission', cls.__name__)

    @patch('apps.rbac.permissions._user_has_permission', return_value=False)
    def test_has_permission_class_denies(self, mock_check):
        cls = HasPermission('projects.create')
        perm_instance = cls()
        request = MagicMock()
        request.user.is_authenticated = True
        self.assertFalse(perm_instance.has_permission(request, None))

    @patch('apps.rbac.permissions._user_has_permission', return_value=True)
    def test_has_permission_class_allows(self, mock_check):
        cls = HasPermission('projects.create')
        perm_instance = cls()
        request = MagicMock()
        request.user.is_authenticated = True
        self.assertTrue(perm_instance.has_permission(request, None))


# ─── HasFeature Tests ──────────────────────────────────────────────────────────

class HasFeatureTest(TestCase):
    def _make_request(self, plan: str | None = None):
        request = MagicMock()
        if plan is not None:
            tenant = MagicMock()
            tenant.plan = plan
            request.tenant = tenant
        else:
            del request.tenant  # no tenant attribute
        return request

    def test_free_plan_no_custom_roles(self):
        cls = HasFeature('custom_roles')
        perm = cls()
        request = self._make_request('free')
        self.assertFalse(perm.has_permission(request, None))

    def test_pro_plan_has_custom_roles(self):
        cls = HasFeature('custom_roles')
        perm = cls()
        request = self._make_request('professional')
        self.assertTrue(perm.has_permission(request, None))

    def test_no_tenant_allowed(self):
        cls = HasFeature('custom_roles')
        perm = cls()
        request = MagicMock(spec=[])  # sin atributo 'tenant'
        self.assertTrue(perm.has_permission(request, None))

    def test_enterprise_has_sso(self):
        cls = HasFeature('sso')
        perm = cls()
        request = self._make_request('enterprise')
        self.assertTrue(perm.has_permission(request, None))

    def test_starter_no_sso(self):
        cls = HasFeature('sso')
        perm = cls()
        request = self._make_request('starter')
        self.assertFalse(perm.has_permission(request, None))

    def test_factory_returns_unique_class_per_feature(self):
        cls_a = HasFeature('mfa')
        cls_b = HasFeature('sso')
        self.assertIsNot(cls_a, cls_b)


# ─── CheckPlanLimit Tests ──────────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class CheckPlanLimitTest(TestCase):
    def _make_user_with_plan(self, plan: str):
        tenant = make_tenant(plan)
        return make_user(tenant)

    def test_free_at_limit_raises(self):
        user = self._make_user_with_plan('free')
        # Free plan: max_projects = 2
        with self.assertRaises(PlanLimitExceeded):
            check_plan_limit(user, 'projects', current_count=2)

    def test_free_below_limit_passes(self):
        user = self._make_user_with_plan('free')
        # 1 proyecto, límite es 2 → OK
        try:
            check_plan_limit(user, 'projects', current_count=1)
        except PlanLimitExceeded:
            self.fail('check_plan_limit raised PlanLimitExceeded unexpectedly')

    def test_enterprise_unlimited_never_raises(self):
        user = self._make_user_with_plan('enterprise')
        # Enterprise → max_projects = None → ilimitado
        try:
            check_plan_limit(user, 'projects', current_count=99999)
        except PlanLimitExceeded:
            self.fail('check_plan_limit raised PlanLimitExceeded for enterprise')

    def test_professional_users_limit(self):
        user = self._make_user_with_plan('professional')
        # Professional: max_users = 25
        with self.assertRaises(PlanLimitExceeded):
            check_plan_limit(user, 'users', current_count=25)

    def test_no_tenant_attribute_passes(self):
        user = MagicMock()
        del user.tenant  # sin tenant
        # No debe lanzar excepción
        try:
            check_plan_limit(user, 'projects', current_count=9999)
        except PlanLimitExceeded:
            self.fail('check_plan_limit raised PlanLimitExceeded when user has no tenant')


# ─── FeaturesView Tests ────────────────────────────────────────────────────────

@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class FeaturesViewTest(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.tenant = make_tenant('professional')
        self.user = make_user(self.tenant)

    def _auth(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {refresh.access_token}')

    def test_unauthenticated_returns_401(self):
        response = self.client.get('/api/v1/features/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_returns_features(self):
        self._auth()
        # Simular request.tenant via middleware patch
        with patch('apps.rbac.views.FeaturesView.get') as mock_get:
            mock_get.return_value = __import__('rest_framework.response', fromlist=['Response']).Response({
                'plan': 'professional',
                'features': {'custom_roles': True},
                'limits': {'users': 25},
            })
            response = self.client.get('/api/v1/features/')
            # La view existe y fue llamada (auth pasó)
            mock_get.assert_called_once()

    def test_response_structure(self):
        """Verifica la estructura de respuesta directamente en la view."""
        from apps.rbac.views import FeaturesView
        from rest_framework.test import APIRequestFactory

        factory = APIRequestFactory()
        request = factory.get('/api/v1/features/')

        # Simular usuario autenticado con tenant
        user = MagicMock()
        user.is_authenticated = True
        tenant = MagicMock()
        tenant.plan = 'starter'
        request.user = user
        request.tenant = tenant

        view = FeaturesView.as_view()
        # Parchar permission_classes para skip auth en test unitario
        with patch.object(FeaturesView, 'permission_classes', []):
            from rest_framework.request import Request
            drf_request = Request(request)
            drf_request.user = user
            drf_request.tenant = tenant

            response = FeaturesView().get(drf_request)

        self.assertIn('plan', response.data)
        self.assertIn('features', response.data)
        self.assertIn('limits', response.data)
        self.assertEqual(response.data['plan'], 'starter')
        self.assertIn('custom_roles', response.data['features'])
        self.assertIn('users', response.data['limits'])


# ─── TenantModelViewSet Tests ──────────────────────────────────────────────────

class TenantModelViewSetTest(TestCase):
    """Tests de integración para el filtrado y creación tenant-aware."""

    def test_get_serializer_context_includes_tenant(self):
        from utils.mixins import TenantModelViewSet

        viewset = TenantModelViewSet()
        request = MagicMock()
        tenant = MagicMock()
        request.tenant = tenant
        viewset.request = request
        viewset.format_kwarg = None

        # Parchar super() context
        with patch('utils.mixins.ModelViewSet.get_serializer_context', return_value={}):
            ctx = viewset.get_serializer_context()

        self.assertIn('tenant', ctx)
        self.assertIs(ctx['tenant'], tenant)

    def test_get_serializer_context_without_tenant(self):
        from utils.mixins import TenantModelViewSet

        viewset = TenantModelViewSet()
        request = MagicMock(spec=['user', 'method'])  # sin atributo 'tenant'
        viewset.request = request
        viewset.format_kwarg = None

        with patch('utils.mixins.ModelViewSet.get_serializer_context', return_value={}):
            ctx = viewset.get_serializer_context()

        self.assertNotIn('tenant', ctx)

    def test_get_queryset_filters_by_tenant(self):
        from utils.mixins import TenantModelViewSet

        mock_qs = MagicMock()
        mock_qs.filter.return_value = mock_qs

        viewset = TenantModelViewSet()
        tenant = MagicMock()
        request = MagicMock()
        request.tenant = tenant
        viewset.request = request

        with patch('utils.mixins.ModelViewSet.get_queryset', return_value=mock_qs):
            result = viewset.get_queryset()

        mock_qs.filter.assert_called_once_with(tenant=tenant)
        self.assertIs(result, mock_qs)

    def test_perform_create_injects_tenant(self):
        from utils.mixins import TenantModelViewSet

        viewset = TenantModelViewSet()
        tenant = MagicMock()
        request = MagicMock()
        request.tenant = tenant
        viewset.request = request

        serializer = MagicMock()
        viewset.perform_create(serializer)

        serializer.save.assert_called_once_with(tenant=tenant)


# ─── Decorator Tests ───────────────────────────────────────────────────────────

class RequirePermissionDecoratorTest(TestCase):
    def test_raises_not_authenticated_when_anonymous(self):
        from utils.decorators import require_permission
        from rest_framework.exceptions import NotAuthenticated

        @require_permission('tasks.create')
        def my_view(view_instance, request):
            return 'ok'

        request = MagicMock()
        request.user.is_authenticated = False

        with self.assertRaises(NotAuthenticated):
            my_view(None, request)

    @patch('apps.rbac.permissions._user_has_permission', return_value=False)
    def test_raises_permission_denied_without_codename(self, mock_check):
        from utils.decorators import require_permission
        from rest_framework.exceptions import PermissionDenied

        @require_permission('tasks.create')
        def my_view(view_instance, request):
            return 'ok'

        request = MagicMock()
        request.user.is_authenticated = True

        with self.assertRaises(PermissionDenied):
            my_view(None, request)

    @patch('apps.rbac.permissions._user_has_permission', return_value=True)
    def test_passes_through_with_permission(self, mock_check):
        from utils.decorators import require_permission

        @require_permission('tasks.create')
        def my_view(view_instance, request):
            return 'ok'

        request = MagicMock()
        request.user.is_authenticated = True

        result = my_view(None, request)
        self.assertEqual(result, 'ok')

    def test_preserves_wrapper_metadata(self):
        from utils.decorators import require_permission

        @require_permission('tasks.delete')
        def my_view(view_instance, request):
            """My view docstring."""
            return 'ok'

        self.assertEqual(my_view.__name__, 'my_view')
        self.assertEqual(my_view._required_permission, 'tasks.delete')


class RequireFeatureDecoratorTest(TestCase):
    def test_passes_when_no_tenant(self):
        from utils.decorators import require_feature

        @require_feature('mfa')
        def my_view(view_instance, request):
            return 'ok'

        request = MagicMock(spec=['user'])  # sin tenant
        result = my_view(None, request)
        self.assertEqual(result, 'ok')

    def test_raises_feature_not_available_on_free_plan(self):
        from utils.decorators import require_feature

        @require_feature('mfa')
        def my_view(view_instance, request):
            return 'ok'

        tenant = MagicMock()
        tenant.plan = 'free'
        request = MagicMock()
        request.tenant = tenant

        with self.assertRaises(FeatureNotAvailable):
            my_view(None, request)

    def test_passes_for_pro_plan_feature(self):
        from utils.decorators import require_feature

        @require_feature('mfa')
        def my_view(view_instance, request):
            return 'ok'

        tenant = MagicMock()
        tenant.plan = 'professional'
        request = MagicMock()
        request.tenant = tenant

        result = my_view(None, request)
        self.assertEqual(result, 'ok')


@override_settings(PASSWORD_HASHERS=_FAST_HASHERS)
class CheckPlanLimitDecoratorTest(TestCase):
    def test_raises_when_limit_exceeded(self):
        from utils.decorators import check_plan_limit

        tenant = make_tenant('free')
        user = make_user(tenant)

        count_fn = lambda req: 2  # free plan max_projects = 2

        @check_plan_limit('projects', count_fn)
        def create_project(view_instance, request):
            return 'created'

        request = MagicMock()
        request.user = user

        with self.assertRaises(PlanLimitExceeded):
            create_project(None, request)

    def test_passes_when_below_limit(self):
        from utils.decorators import check_plan_limit

        tenant = make_tenant('free')
        user = make_user(tenant)

        count_fn = lambda req: 1  # 1 < 2 (free limit)

        @check_plan_limit('projects', count_fn)
        def create_project(view_instance, request):
            return 'created'

        request = MagicMock()
        request.user = user

        result = create_project(None, request)
        self.assertEqual(result, 'created')
