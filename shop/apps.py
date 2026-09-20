from django.apps import AppConfig


class ShopConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'shop'

    def ready(self):
        # Connects the user_logged_in receiver that hands a guest's session
        # cart over to their account cart. Imported here (not at module top)
        # because the signal module needs the app registry to be populated.
        from . import signals  # noqa: F401
