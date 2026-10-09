from django.apps import AppConfig


class AdminpanelConfig(AppConfig):
    # Не «admin»: эту метку занимает django.contrib.admin.
    name = "adminpanel"
    verbose_name = "Админка"
