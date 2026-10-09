"""«Админка»: раздел приложения только для администраторов проекта.

Раздел только показывает — правка данных по-прежнему в Django admin
(/django-admin/), и страницы ведут туда ссылками. Новый сервис — новая вьюха с
маршрутом admin_* и плиткой на главной раздела.
"""

import platform

import django
from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Count, Exists, Max, OuterRef, Q
from django.http import Http404
from django.utils import timezone
from django.views.generic import TemplateView

from accounts import deletion
from accounts.models import User
from workouts import trash
from workouts.models import BodyMeasurement, CardioPart, DeletedWorkout, StrengthSet, Workout


class AdminRequiredMixin(LoginRequiredMixin):
    """Гость — на вход, как везде; вошедший не-админ — 404.

    404, а не 403: обычному пользователю незачем знать, что раздел существует, —
    тот же принцип, что «чужая запись по прямому адресу — 404».
    """

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.is_admin:
            raise Http404
        return super().dispatch(request, *args, **kwargs)


class AdminPageMixin(AdminRequiredMixin):
    # «Профиль» в нижней навигации: на телефоне в раздел входят из профиля.
    extra_context = {"nav_active": "profile"}


class AdminHomeView(AdminPageMixin, TemplateView):
    template_name = "adminpanel/home.html"

    def get_context_data(self, **kwargs):
        return super().get_context_data(**kwargs) | {"users_count": User.objects.count()}


def user_status(user, now):
    """Подпись статуса: удаление важнее отключения, отключение — роли."""
    if user.deletion_requested_at is not None:
        until = timezone.localtime(deletion.deadline(user))
        if until <= now:
            return "Удаление просрочено — уйдёт ночью"
        return f"Ждёт удаления до {until:%d.%m.%Y}"
    if not user.is_active:
        return "Отключён"
    if user.is_admin:
        return "Администратор"
    return "Активен"


class AdminUsersView(AdminPageMixin, TemplateView):
    """Все пользователи одним запросом: подтверждение почты и тренировки — аннотациями."""

    template_name = "adminpanel/users.html"

    def get_context_data(self, **kwargs):
        finished = Q(workouts__duration_min__isnull=False)
        users = list(
            User.objects.annotate(
                email_verified=Exists(
                    EmailAddress.objects.filter(user=OuterRef("pk"), verified=True)
                ),
                workouts_count=Count("workouts", filter=finished),
                last_workout_at=Max("workouts__started_at", filter=finished),
            ).order_by("-date_joined", "-pk")
        )
        now = timezone.now()
        for user in users:
            user.status = user_status(user, now)
        return super().get_context_data(**kwargs) | {"users": users}


def database_size():
    """Размер базы в байтах; подпись («9,9 МБ») делает filesizeformat в шаблоне —
    pg_size_pretty писал бы «10126 kB»."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_database_size(current_database())")
        return cursor.fetchone()[0]


def pending_migrations():
    """Неприменённые миграции: «app.0042_name» — их видно сразу после деплоя."""
    executor = MigrationExecutor(connection)
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    return [f"{migration.app_label}.{migration.name}" for migration, _backwards in plan]


def postgres_version():
    # server_version — число вида 170004 (17.4); запроса не стоит.
    major, minor = divmod(connection.connection.info.server_version, 10000)
    return f"{major}.{minor}"


class AdminSystemView(AdminPageMixin, TemplateView):
    """Состояние системы: версии, база, миграции, объём данных, ночная чистка."""

    template_name = "adminpanel/system.html"

    def get_context_data(self, **kwargs):
        connection.ensure_connection()
        counts = [
            ("Пользователи", User.objects.count()),
            ("Тренировки", Workout.objects.count()),
            ("Подходы", StrengthSet.objects.count()),
            ("Кардио-части", CardioPart.objects.count()),
            ("Замеры", BodyMeasurement.objects.count()),
            ("В корзине", DeletedWorkout.objects.count()),
        ]
        return super().get_context_data(**kwargs) | {
            "versions": [
                ("Приложение", settings.APP_VERSION or "—"),
                ("Django", django.get_version()),
                ("Python", platform.python_version()),
                ("PostgreSQL", postgres_version()),
            ],
            "database_size": database_size(),
            "pending_migrations": pending_migrations(),
            "counts": counts,
            # Те же выборки, что удаляет purge_deleted: здесь — только посчитать.
            "expired_trash": trash.expired().count(),
            "expired_accounts": deletion.expired().count(),
        }
