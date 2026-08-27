from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView


class DashboardView(LoginRequiredMixin, TemplateView):
    """Заглушка дашборда: точка входа после логина, наполнение — следующий этап."""

    template_name = "dashboard.html"
    extra_context = {"nav_active": "dashboard"}
