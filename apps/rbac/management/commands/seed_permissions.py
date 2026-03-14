"""
Management command: seed_permissions
Carga los 62 permisos base y los 4 roles del sistema.
Idempotente: usa get_or_create, se puede ejecutar múltiples veces sin duplicar datos.

Ejecutar: python manage.py seed_permissions
Alias make: make seed-permissions
"""
from django.core.management.base import BaseCommand

from apps.rbac.models import Permission, Role, RolePermission


# (codename, name, description, resource, action)
PERMISSIONS: list[tuple[str, str, str, str, str]] = [
    # users
    ('users.create',   'Crear Usuarios',           'Crear nuevos usuarios en el tenant',           'users',   'create'),
    ('users.read',     'Ver Usuarios',              'Ver lista y detalle de usuarios',               'users',   'read'),
    ('users.update',   'Editar Usuarios',           'Editar datos de usuarios existentes',           'users',   'update'),
    ('users.delete',   'Eliminar Usuarios',         'Eliminar usuarios del tenant',                  'users',   'delete'),
    ('users.invite',   'Invitar Usuarios',          'Enviar invitaciones a nuevos usuarios',         'users',   'invite'),
    # roles
    ('roles.create',   'Crear Roles',               'Crear nuevos roles personalizados',             'roles',   'create'),
    ('roles.read',     'Ver Roles',                 'Ver roles disponibles y sus permisos',          'roles',   'read'),
    ('roles.update',   'Editar Roles',              'Modificar roles existentes',                    'roles',   'update'),
    ('roles.delete',   'Eliminar Roles',            'Eliminar roles personalizados',                 'roles',   'delete'),
    ('roles.assign',   'Asignar Roles',             'Asignar o revocar roles a usuarios',            'roles',   'assign'),
    # tasks
    ('tasks.create',   'Crear Tareas',              'Crear nuevas tareas',                           'tasks',   'create'),
    ('tasks.read',     'Ver Tareas',                'Ver tareas propias y del equipo',               'tasks',   'read'),
    ('tasks.update',   'Editar Tareas',             'Editar tareas existentes',                      'tasks',   'update'),
    ('tasks.delete',   'Eliminar Tareas',           'Eliminar tareas',                               'tasks',   'delete'),
    ('tasks.assign',   'Asignar Tareas',            'Asignar tareas a otros usuarios',               'tasks',   'assign'),
    # boards
    ('boards.admin',   'Administrar Tableros',      'Crear, editar y eliminar tableros Kanban',      'boards',  'admin'),
    ('boards.reorder', 'Reordenar Tableros',        'Reordenar columnas y tarjetas',                 'boards',  'reorder'),
    # calendar
    ('calendar.create', 'Crear Eventos',            'Crear nuevos eventos de calendario',            'calendar', 'create'),
    ('calendar.read',   'Ver Calendario',           'Ver eventos del calendario',                    'calendar', 'read'),
    ('calendar.update', 'Editar Eventos',           'Editar eventos existentes',                     'calendar', 'update'),
    ('calendar.delete', 'Eliminar Eventos',         'Eliminar eventos del calendario',               'calendar', 'delete'),
    ('calendar.share',  'Compartir Calendario',     'Compartir calendario con otros',                'calendar', 'share'),
    ('calendar.sync',   'Sincronizar Calendario',   'Sincronizar con Google Calendar / iCal',        'calendar', 'sync'),
    # landing
    ('landing.create',  'Crear Landing Pages',      'Crear nuevas landing pages',                   'landing', 'create'),
    ('landing.read',    'Ver Landing Pages',        'Ver landing pages del tenant',                  'landing', 'read'),
    ('landing.edit',    'Editar Landing Pages',     'Editar contenido de landing pages',             'landing', 'edit'),
    ('landing.publish', 'Publicar Landing Pages',   'Publicar o despublicar landing pages',          'landing', 'publish'),
    # branding
    ('branding.update', 'Editar Branding',          'Modificar logo, colores y branding del tenant', 'branding', 'update'),
    # forms
    ('forms.manage',    'Gestionar Formularios',    'Crear, editar y eliminar formularios',          'forms',   'manage'),
    # projects
    ('projects.create',   'Crear Proyectos',        'Crear nuevos proyectos',                        'projects', 'create'),
    ('projects.read',     'Ver Proyectos',          'Ver proyectos y sus elementos',                 'projects', 'read'),
    ('projects.update',   'Editar Proyectos',       'Editar proyectos existentes',                   'projects', 'update'),
    ('projects.delete',   'Eliminar Proyectos',     'Eliminar proyectos',                            'projects', 'delete'),
    ('projects.sections', 'Gestionar Secciones',    'Crear y eliminar secciones en proyectos',       'projects', 'sections'),
    # credentials
    ('credentials.manage', 'Gestionar Credenciales', 'Crear y editar credenciales almacenadas',     'credentials', 'manage'),
    ('credentials.reveal', 'Revelar Contraseñas',   'Ver contraseñas y secretos en texto plano',    'credentials', 'reveal'),
    # portfolio
    ('portfolio.publish', 'Publicar Portfolio',     'Publicar y gestionar portfolio público',        'portfolio', 'publish'),
    # digital_services
    ('digital_services.tarjeta',    'Tarjeta Digital',  'Gestionar tarjeta digital',                'digital_services', 'tarjeta'),
    ('digital_services.landing',    'Landing Personal', 'Gestionar landing page personal',          'digital_services', 'landing'),
    ('digital_services.cv',         'CV Digital',       'Gestionar currículum digital',             'digital_services', 'cv'),
    ('digital_services.portfolio',  'Portfolio Digital','Gestionar portfolio digital',              'digital_services', 'portfolio'),
    # public_profiles
    ('public_profiles.analytics', 'Analíticas Perfil', 'Ver analíticas de perfiles públicos',      'public_profiles', 'analytics'),
    # billing
    ('billing.read',    'Ver Facturación',          'Ver facturas y estado de suscripción',          'billing', 'read'),
    ('billing.manage',  'Gestionar Facturación',    'Cambiar método de pago y datos de facturación', 'billing', 'manage'),
    ('billing.upgrade', 'Cambiar Plan',             'Actualizar o degradar plan de suscripción',     'billing', 'upgrade'),
    # promotions
    ('promotions.manage', 'Gestionar Promociones',  'Crear y aplicar códigos promocionales',         'promotions', 'manage'),
    # customers
    ('customers.read',      'Ver Clientes',         'Ver lista y detalle de clientes',               'customers', 'read'),
    ('customers.create',    'Crear Clientes',       'Registrar nuevos clientes',                     'customers', 'create'),
    ('customers.update',    'Editar Clientes',      'Editar datos de clientes',                      'customers', 'update'),
    ('customers.delete',    'Eliminar Clientes',    'Eliminar clientes del sistema',                 'customers', 'delete'),
    ('customers.suspend',   'Suspender Clientes',   'Suspender o reactivar clientes',                'customers', 'suspend'),
    ('customers.analytics', 'Analíticas Clientes',  'Ver analíticas y métricas de clientes',         'customers', 'analytics'),
    ('customers.export',    'Exportar Clientes',    'Exportar datos de clientes a CSV/Excel',         'customers', 'export'),
    # subscriptions
    ('subscriptions.manage', 'Gestionar Suscripciones', 'Administrar suscripciones de clientes',    'subscriptions', 'manage'),
    ('subscriptions.cancel', 'Cancelar Suscripciones',  'Cancelar suscripciones activas',           'subscriptions', 'cancel'),
    # analytics
    ('analytics.read',   'Ver Analíticas',          'Ver dashboards y reportes de analíticas',       'analytics', 'read'),
    ('analytics.export', 'Exportar Analíticas',     'Exportar datos de analíticas',                  'analytics', 'export'),
    # settings
    ('settings.read',   'Ver Configuración',        'Ver configuración del tenant',                  'settings', 'read'),
    ('settings.update', 'Editar Configuración',     'Modificar configuración del tenant',            'settings', 'update'),
    # audit
    ('audit.read',   'Ver Auditoría',               'Ver logs de auditoría del tenant',              'audit', 'read'),
    ('audit.export', 'Exportar Auditoría',          'Exportar logs de auditoría',                    'audit', 'export'),
    # snippets
    ('snippets.create', 'Crear Snippets',            'Crear nuevos snippets de código',               'snippets', 'create'),
    ('snippets.read',   'Ver Snippets',              'Ver snippets propios',                          'snippets', 'read'),
    ('snippets.update', 'Editar Snippets',           'Editar snippets existentes',                    'snippets', 'update'),
    ('snippets.delete', 'Eliminar Snippets',         'Eliminar snippets',                             'snippets', 'delete'),
    # dashboard
    ('dashboard.read', 'Ver Dashboard',             'Acceder al dashboard principal',                'dashboard', 'read'),
]

# Todos los codenames disponibles
_ALL = [p[0] for p in PERMISSIONS]

# (name, description, permission_codenames)
SYSTEM_ROLES: list[tuple[str, str, list[str]]] = [
    (
        'Owner',
        'Control total del tenant. Acceso a todos los recursos y configuraciones.',
        _ALL,
    ),
    (
        'Service Manager',
        'Gestión operativa del tenant. Sin acceso a facturación crítica ni configuración global.',
        [c for c in _ALL if c not in {
            'billing.manage', 'billing.upgrade',
            'settings.update',
            'promotions.manage',
            'customers.create', 'customers.update', 'customers.delete', 'customers.suspend',
            'subscriptions.cancel',
            'audit.export',
        }],
    ),
    (
        'Member',
        'Usuario operativo estándar. Acceso a tareas, calendario, proyectos y servicios digitales.',
        [
            'dashboard.read',
            'tasks.create', 'tasks.read', 'tasks.update', 'tasks.delete', 'tasks.assign',
            'boards.reorder',
            'calendar.create', 'calendar.read', 'calendar.update', 'calendar.delete',
            'projects.create', 'projects.read', 'projects.update',
            'credentials.manage', 'credentials.reveal',
            'digital_services.tarjeta', 'digital_services.landing',
            'digital_services.cv', 'digital_services.portfolio',
            'public_profiles.analytics',
            'billing.read',
            'settings.read',
        ],
    ),
    (
        'Viewer',
        'Usuario de solo lectura. Puede ver recursos pero no modificarlos.',
        [
            'dashboard.read',
            'tasks.read',
            'calendar.read',
            'projects.read',
            'landing.read',
            'digital_services.tarjeta', 'digital_services.landing',
            'digital_services.cv', 'digital_services.portfolio',
            'public_profiles.analytics',
        ],
    ),
]


class Command(BaseCommand):
    help = 'Seeds the 62 base permissions and 4 system roles (idempotent)'

    def handle(self, *args: object, **kwargs: object) -> None:
        self._seed_permissions()
        self._seed_system_roles()
        self.stdout.write(self.style.SUCCESS(
            'Permissions and system roles seeded successfully.'
        ))

    def _seed_permissions(self) -> None:
        created_count = 0
        for codename, name, description, resource, action in PERMISSIONS:
            _, created = Permission.objects.get_or_create(
                codename=codename,
                defaults={
                    'name': name,
                    'description': description,
                    'resource': resource,
                    'action': action,
                },
            )
            if created:
                created_count += 1

        total = Permission.objects.count()
        self.stdout.write(f'  Permissions: {total} total ({created_count} new)')

    def _seed_system_roles(self) -> None:
        for role_name, role_desc, perm_codenames in SYSTEM_ROLES:
            role, role_created = Role.objects.get_or_create(
                name=role_name,
                tenant=None,
                defaults={
                    'description': role_desc,
                    'is_system_role': True,
                },
            )
            new_rp = 0
            for codename in perm_codenames:
                try:
                    perm = Permission.objects.get(codename=codename)
                except Permission.DoesNotExist:
                    self.stderr.write(f'  WARNING: permission "{codename}" not found, skipping')
                    continue
                _, created = RolePermission.objects.get_or_create(
                    role=role,
                    permission=perm,
                    defaults={'scope': 'all'},
                )
                if created:
                    new_rp += 1

            total_rp = role.role_permissions.count()
            status = 'created' if role_created else 'existing'
            self.stdout.write(
                f'  Role "{role_name}" ({status}): {total_rp} permissions ({new_rp} new)'
            )
