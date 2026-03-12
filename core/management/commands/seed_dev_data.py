"""
Management command: seed_dev_data
Crea 3 tenants de desarrollo con usuarios y roles asignados.
Idempotente: usa get_or_create. Requiere seed_permissions ejecutado antes.
Ejecutar: python manage.py seed_dev_data
Alias make: make seed-data
"""
import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.rbac.models import Role, UserRole
from apps.tenants.models import Tenant

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

        # ─── Datos Hub ─────────────────────────────────────────────────────────
        self._seed_hub_data()

    def _seed_hub_data(self) -> None:
        from apps.auth_app.models import SSOToken
        from apps.referrals.models import Referral, ReferralCode
        from apps.services.models import Service, TenantService

        tenants = list(Tenant.objects.filter(slug__in=['acme-corp', 'startup-xyz', 'freetier-inc']))
        tenant_map = {t.slug: t for t in tenants}

        # ReferralCode — uno por tenant
        rc_created = 0
        for tenant in tenants:
            _, created = ReferralCode.objects.get_or_create(
                tenant=tenant,
                defaults={'code': ReferralCode.generate_code(tenant)},
            )
            if created:
                rc_created += 1

        # TenantService — workspace + vista para cada tenant
        ts_created = 0
        for svc_slug in ['workspace', 'vista']:
            try:
                service = Service.objects.get(slug=svc_slug)
            except Service.DoesNotExist:
                continue
            for tenant in tenants:
                _, created = TenantService.objects.get_or_create(
                    tenant=tenant,
                    service=service,
                    defaults={'status': 'active'},
                )
                if created:
                    ts_created += 1

        # Referrals — acme→startup (active) y startup→freetier (pending)
        ref_created = 0
        for referrer_slug, referred_slug, ref_status in [
            ('acme-corp', 'startup-xyz', 'active'),
            ('startup-xyz', 'freetier-inc', 'pending'),
        ]:
            referrer = tenant_map.get(referrer_slug)
            referred = tenant_map.get(referred_slug)
            if referrer and referred:
                _, created = Referral.objects.get_or_create(
                    referrer=referrer,
                    referred=referred,
                    defaults={'status': ref_status},
                )
                if created:
                    ref_created += 1

        # SSOToken expirado de prueba (acme owner, workspace)
        sso_created = 0
        acme_owner = User.objects.filter(email='carlos@acme.com').first()
        acme_tenant = tenant_map.get('acme-corp')
        if acme_owner and acme_tenant:
            existing = SSOToken.objects.filter(user=acme_owner, service='workspace').first()
            if not existing:
                SSOToken.objects.create(
                    user=acme_owner,
                    tenant=acme_tenant,
                    service='workspace',
                    token=secrets.token_hex(32),
                    expires_at=timezone.now() - timedelta(hours=2),
                )
                sso_created += 1

        self.stdout.write(self.style.SUCCESS(
            f'Hub data seeded: {rc_created} referral codes, '
            f'{ts_created} tenant services, {ref_created} referrals, '
            f'{sso_created} SSO tokens.'
        ))
