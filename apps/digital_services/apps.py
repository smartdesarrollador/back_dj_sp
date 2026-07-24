from django.apps import AppConfig


class DigitalServicesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.digital_services'
    verbose_name = 'DigitalServices'

    def ready(self):
        from apps.digital_services import signals  # noqa: F401
