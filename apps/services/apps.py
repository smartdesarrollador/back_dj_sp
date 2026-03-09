from django.apps import AppConfig


class ServicesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.services'
    verbose_name = 'Services'

    def ready(self) -> None:
        import apps.services.signals  # noqa: F401
