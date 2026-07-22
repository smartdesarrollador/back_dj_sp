"""
Configuración de planes y feature gates.

Define los límites de recursos (max_*) y los flags de funcionalidades (bool)
para cada plan: free, starter, professional, enterprise.
"""
from typing import Any

PLAN_FEATURES: dict[str, dict[str, Any]] = {
    'free': {
        # Límites de recursos (None = ilimitado)
        'max_users': 5,
        'max_projects': 2,
        'max_sections_per_project': 3,
        'max_items_per_project': 50,
        'max_notes': 10,
        'max_contacts': 25,
        'max_bookmarks': 20,
        'max_custom_roles': 0,
        'max_ssh_keys': 0,
        'max_ssl_certs': 0,
        'max_snippets': 10,
        'max_env_vars': 0,
        'max_vault_items': 10,
        'audit_log_days': 7,
        'storage_gb': 1,
        'max_image_upload_mb': 2,
        'max_file_upload_mb': 5,
        'api_calls_per_month': 1000,
        'max_shares_per_project': 0,
        # Feature flags
        'custom_roles': False,
        'mfa': False,
        'mfa_enforce': False,
        'sso': False,
        'batch_operations': False,
        'webhooks': False,
        'custom_branding': False,
        'temporal_delegation': False,
        'vault': True,
        'sharing': False,
        'audit_logs': False,
        'pdf_export': False,
        'full_text_search': False,
        'contact_groups': False,
        'contact_export': False,
        'bookmark_collections': False,
        'bookmark_export': False,
        'notes_export': False,
        'tasks_export': False,
        'snippets_export': False,
        'calendar_export': False,
        'project_export': False,
        'full_backup': False,
        'contact_import': False,
        'bookmark_import': False,
        'notes_import': False,
        'tasks_import': False,
        'calendar_import': False,
        'max_forms': 1,
        'max_form_questions': 5,
        'max_form_responses': 50,
        'form_export_csv': False,
        'analytics': False,
        'analytics_trends': False,
        'max_task_boards': 1,
        'max_tasks': 50,
        'max_calendar_events': 50,
        'task_assign': False,
        'calendar_attendees': False,
        # Digital Services
        'digital_card': True,
        'landing_page': False,
        'portfolio': False,
        'cv_pdf_export': False,
        'custom_domain': False,
        'digital_analytics': False,
        'digital_analytics_days': 7,
        'qr_vcard_export': False,
        'max_portfolio_items': 0,
        # Support
        'support_export': False,
        'support_sla': False,
    },
    'starter': {
        'max_users': 10,
        'max_projects': 10,
        'max_sections_per_project': 10,
        'max_items_per_project': 200,
        'max_notes': 100,
        'max_contacts': 100,
        'max_bookmarks': 100,
        'max_custom_roles': 3,
        'max_ssh_keys': 5,
        'max_ssl_certs': 10,
        'max_snippets': 50,
        'max_env_vars': 50,
        'max_vault_items': 50,
        'max_shares_per_project': 5,
        'audit_log_days': 30,
        'storage_gb': 5,
        'max_image_upload_mb': 5,
        'max_file_upload_mb': 10,
        'api_calls_per_month': 10000,
        'custom_roles': True,
        'mfa': True,
        'mfa_enforce': False,
        'sso': False,
        'batch_operations': False,
        'webhooks': False,
        'custom_branding': False,
        'temporal_delegation': False,
        'vault': True,
        'sharing': True,
        'audit_logs': False,
        'pdf_export': False,
        'full_text_search': False,
        'contact_groups': True,
        'contact_export': True,
        'bookmark_collections': True,
        'bookmark_export': False,
        'notes_export': True,
        'tasks_export': True,
        'snippets_export': True,
        'calendar_export': True,
        'project_export': True,
        'full_backup': False,
        'contact_import': True,
        'bookmark_import': True,
        'notes_import': True,
        'tasks_import': True,
        'calendar_import': True,
        'max_forms': 5,
        'max_form_questions': 20,
        'max_form_responses': None,
        'form_export_csv': False,
        'analytics': True,
        'analytics_trends': False,
        'max_task_boards': 5,
        'max_tasks': 500,
        'max_calendar_events': 200,
        'task_assign': True,
        'calendar_attendees': True,
        # Digital Services
        'digital_card': True,
        'landing_page': True,
        'portfolio': False,
        'cv_pdf_export': True,
        'custom_domain': False,
        'digital_analytics': True,
        'digital_analytics_days': 7,
        'qr_vcard_export': True,
        'max_portfolio_items': 0,
        # Support
        'support_export': False,
        'support_sla': False,
    },
    'professional': {
        'max_users': 25,
        'max_projects': None,
        'max_sections_per_project': None,
        'max_items_per_project': None,
        'max_notes': 1000,
        'max_contacts': None,
        'max_bookmarks': None,
        'max_custom_roles': 10,
        'max_ssh_keys': None,
        'max_ssl_certs': None,
        'max_snippets': None,
        'max_env_vars': None,
        'max_vault_items': None,
        'max_shares_per_project': 50,
        'audit_log_days': 365,
        'storage_gb': 20,
        'max_image_upload_mb': 10,
        'max_file_upload_mb': 25,
        'api_calls_per_month': 100000,
        'custom_roles': True,
        'mfa': True,
        'mfa_enforce': False,
        'sso': False,
        'batch_operations': True,
        'webhooks': True,
        'custom_branding': True,
        'temporal_delegation': True,
        'vault': True,
        'sharing': True,
        'audit_logs': True,
        'pdf_export': True,
        'full_text_search': True,
        'contact_groups': True,
        'contact_export': True,
        'bookmark_collections': True,
        'bookmark_export': True,
        'notes_export': True,
        'tasks_export': True,
        'snippets_export': True,
        'calendar_export': True,
        'project_export': True,
        'full_backup': True,
        'contact_import': True,
        'bookmark_import': True,
        'notes_import': True,
        'tasks_import': True,
        'calendar_import': True,
        'max_forms': 25,
        'max_form_questions': None,
        'max_form_responses': None,
        'form_export_csv': True,
        'analytics': True,
        'analytics_trends': True,
        'max_task_boards': None,
        'max_tasks': None,
        'max_calendar_events': None,
        'task_assign': True,
        'calendar_attendees': True,
        # Digital Services
        'digital_card': True,
        'landing_page': True,
        'portfolio': True,
        'cv_pdf_export': True,
        'custom_domain': False,
        'digital_analytics': True,
        'digital_analytics_days': 30,
        'qr_vcard_export': True,
        'max_portfolio_items': None,
        # Support
        'support_export': True,
        'support_sla': False,
    },
    'enterprise': {
        'max_users': None,
        'max_projects': None,
        'max_sections_per_project': None,
        'max_items_per_project': None,
        'max_notes': None,
        'max_contacts': None,
        'max_bookmarks': None,
        'max_custom_roles': None,
        'max_ssh_keys': None,
        'max_ssl_certs': None,
        'max_snippets': None,
        'max_env_vars': None,
        'max_vault_items': None,
        'max_shares_per_project': None,
        'audit_log_days': 2555,
        'storage_gb': None,
        'max_image_upload_mb': 25,
        'max_file_upload_mb': 100,
        'api_calls_per_month': None,
        'custom_roles': True,
        'mfa': True,
        'mfa_enforce': True,
        'sso': True,
        'batch_operations': True,
        'webhooks': True,
        'custom_branding': True,
        'temporal_delegation': True,
        'vault': True,
        'sharing': True,
        'audit_logs': True,
        'pdf_export': True,
        'full_text_search': True,
        'contact_groups': True,
        'contact_export': True,
        'bookmark_collections': True,
        'bookmark_export': True,
        'notes_export': True,
        'tasks_export': True,
        'snippets_export': True,
        'calendar_export': True,
        'project_export': True,
        'full_backup': True,
        'contact_import': True,
        'bookmark_import': True,
        'notes_import': True,
        'tasks_import': True,
        'calendar_import': True,
        'max_forms': None,
        'max_form_questions': None,
        'max_form_responses': None,
        'form_export_csv': True,
        'analytics': True,
        'analytics_trends': True,
        'max_task_boards': None,
        'max_tasks': None,
        'max_calendar_events': None,
        'task_assign': True,
        'calendar_attendees': True,
        # Digital Services
        'digital_card': True,
        'landing_page': True,
        'portfolio': True,
        'cv_pdf_export': True,
        'custom_domain': True,
        'digital_analytics': True,
        'digital_analytics_days': 365,
        'qr_vcard_export': True,
        'max_portfolio_items': None,
        # Support
        'support_export': True,
        'support_sla': True,
    },
}


PLAN_LIMITS_CACHE_TTL = 300  # 5 min — mismo TTL que TENANT_CACHE_TTL (apps/tenants/middleware.py)


def get_effective_plan_limits(plan: str) -> dict[str, Any]:
    """
    Límites técnicos reales de un plan: los overrides editables desde el Admin
    (Plan.limits en BD, subset comercial: max_users, storage_gb, max_projects,
    max_custom_roles, api_calls_per_month) tienen prioridad sobre los defaults
    hardcodeados de PLAN_FEATURES. Cacheado — se invalida en Plan.save().
    """
    from django.core.cache import cache

    cache_key = f'plan:limits:{plan}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    from apps.subscriptions.models import Plan  # import diferido: orden de carga de apps

    defaults = PLAN_FEATURES.get(plan, PLAN_FEATURES['free'])
    try:
        overrides = Plan.objects.get(id=plan).limits or {}
    except Plan.DoesNotExist:
        overrides = {}

    merged = {**defaults, **overrides}
    cache.set(cache_key, merged, timeout=PLAN_LIMITS_CACHE_TTL)
    return merged


def get_plan_limit(plan: str, resource: str) -> int | None:
    """
    Retorna el límite numérico para un recurso en un plan.
    None significa ilimitado.

    Args:
        plan: 'free' | 'starter' | 'professional' | 'enterprise'
        resource: nombre del recurso sin prefijo 'max_' (ej. 'projects', 'users')
    """
    return get_effective_plan_limits(plan).get(f'max_{resource}')


def plan_has_feature(plan: str, feature: str) -> bool:
    """
    Retorna True si el plan incluye la feature dada.

    Args:
        plan: 'free' | 'starter' | 'professional' | 'enterprise'
        feature: nombre del feature flag (ej. 'custom_roles', 'mfa', 'sso')
    """
    plan_config = PLAN_FEATURES.get(plan, PLAN_FEATURES['free'])
    return bool(plan_config.get(feature, False))


PLAN_CATALOG: list[dict] = [
    {
        'id': 'free',
        'display_name': 'Free',
        'description': 'Para explorar la plataforma',
        'price_monthly': 0,
        'price_annual': 0,
        'popular': False,
        'highlights': [
            {'label': 'Hasta 5 usuarios', 'included': True},
            {'label': '1 GB almacenamiento', 'included': True},
            {'label': '1,000 llamadas API/mes', 'included': True},
            {'label': 'Roles personalizados', 'included': False},
            {'label': 'MFA', 'included': False},
            {'label': 'SSO/SAML', 'included': False},
            {'label': 'Soporte prioritario', 'included': False},
        ],
    },
    {
        'id': 'starter',
        'display_name': 'Starter',
        'description': 'Para pequeños equipos en crecimiento',
        'price_monthly': 29,
        'price_annual': 313,
        'popular': False,
        'highlights': [
            {'label': 'Hasta 10 usuarios', 'included': True},
            {'label': '5 GB almacenamiento', 'included': True},
            {'label': '10,000 llamadas API/mes', 'included': True},
            {'label': 'Roles personalizados', 'included': True},
            {'label': 'MFA', 'included': True},
            {'label': 'SSO/SAML', 'included': False},
            {'label': 'Soporte por email', 'included': True},
        ],
    },
    {
        'id': 'professional',
        'display_name': 'Professional',
        'description': 'Para empresas que necesitan escala y control',
        'price_monthly': 79,
        'price_annual': 854,
        'popular': True,
        'highlights': [
            {'label': 'Hasta 25 usuarios', 'included': True},
            {'label': '20 GB almacenamiento', 'included': True},
            {'label': '100,000 llamadas API/mes', 'included': True},
            {'label': 'Roles personalizados ilimitados', 'included': True},
            {'label': 'MFA', 'included': True},
            {'label': 'SSO/SAML', 'included': False},
            {'label': 'Soporte prioritario', 'included': True},
        ],
    },
    {
        'id': 'enterprise',
        'display_name': 'Enterprise',
        'description': 'Para grandes organizaciones',
        'price_monthly': 199,
        'price_annual': 2149,
        'popular': False,
        'highlights': [
            {'label': 'Usuarios ilimitados', 'included': True},
            {'label': 'Almacenamiento ilimitado', 'included': True},
            {'label': 'Llamadas API ilimitadas', 'included': True},
            {'label': 'Roles personalizados ilimitados', 'included': True},
            {'label': 'MFA (forzado para todos)', 'included': True},
            {'label': 'SSO/SAML', 'included': True},
            {'label': 'Soporte dedicado 24/7', 'included': True},
        ],
    },
]
