from django.apps import AppConfig

class SyncupConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'syncup'

    def ready(self):
        from django.conf import settings
        from .utils import init_fcm
        init_fcm(settings.SERVICE_ACCOUNT_FILE)