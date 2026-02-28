"""
Management command: seed_dev_data
Crea 3 tenants de desarrollo con usuarios y roles asignados.
Idempotente: usa get_or_create. Requiere seed_permissions ejecutado antes.
Ejecutar: python manage.py seed_dev_data
Alias make: make seed-data
"""
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.contrib.auth import get_user_model

from apps.tenants.models import Tenant
from apps.rbac.models import Role, UserRole

User = get_user_model()

DEV_PASSWORD = 'Password123!'

TENANTS_DATA: list[dict] = [
    {
        'name': 'Acme Corp',
        'slug': 'acme-corp',
        'subdomain': 'acme',
        'plan': 'professional',
        'users': [
            {'email': 'carlos@acme.com',  'name': 'Carlos Owner',    'role': 'Owner'},
            {'email': 'ana@acme.com',     'name': 'Ana García',      'role': 'Service Manager'},
            {'email': 'pedro@acme.com',   'name': 'Pedro Martínez',  'role': 'Member'},
            {'email': 'lucia@acme.com',   'name': 'Lucía Viewer',    'role': 'Viewer'},
            {'email': 'jorge@acme.com',   'name': 'Jorge López',     'role': 'Member'},
        ],
    },
    {
        'name': 'StartupXYZ',
        'slug': 'startup-xyz',
        'subdomain': 'startup',
        'plan': 'starter',
        'users': [
            {'email': 'owner@startup.com',   'name': 'Startup Owner',   'role': 'Owner'},
            {'email': 'manager@startup.com', 'name': 'Startup Manager', 'role': 'Service Manager'},
            {'email': 'member@startup.com',  'name': 'Startup Member',  'role': 'Member'},
            {'email': 'viewer@startup.com',  'name': 'Startup Viewer',  'role': 'Viewer'},
        ],
    },
    {
        'name': 'FreeTier Inc',
        'slug': 'freetier-inc',
        'subdomain': 'freetier',
        'plan': 'free',
        'users': [
            {'email': 'owner@freetier.com',  'name': 'Free Owner',  'role': 'Owner'},
            {'email': 'member@freetier.com', 'name': 'Free Member', 'role': 'Member'},
            {'email': 'viewer@freetier.com', 'name': 'Free Viewer', 'role': 'Viewer'},
        ],
    },
]


class Command(BaseCommand):
    help = 'Seeds development data: 3 tenants with users and roles (idempotent)'

    def handle(self, *args: object, **kwargs: object) -> None:
        # Garantizar que permissions y system roles existen
        call_command('seed_permissions', verbosity=0)

        tenants_created = users_created = roles_assigned = 0

        for tenant_data in TENANTS_DATA:
            tenant, t_created = Tenant.objects.get_or_create(
                slug=tenant_data['slug'],
                defaults={
                    'name': tenant_data['name'],
                    'subdomain': tenant_data['subdomain'],
                    'plan': tenant_data['plan'],
                },
            )
            if t_created:
                tenants_created += 1

            for user_data in tenant_data['users']:
                user, u_created = User.objects.get_or_create(
                    email=user_data['email'],
                    defaults={
                        'name': user_data['name'],
                        'tenant': tenant,
                        'email_verified': True,
                        'is_active': True,
                    },
                )
                if u_created:
                    user.set_password(DEV_PASSWORD)
                    user.save(update_fields=['password'])
                    users_created += 1

                # Asignar rol del sistema (tenant=None para system roles)
                role = Role.objects.filter(
                    name=user_data['role'], is_system_role=True
                ).first()
                if role:
                    _, r_created = UserRole.objects.get_or_create(
                        user=user, role=role
                    )
                    if r_created:
                        roles_assigned += 1

        self.stdout.write(self.style.SUCCESS(
            f'Dev data seeded: {tenants_created} tenants, '
            f'{users_created} users, {roles_assigned} roles assigned.'
        ))
