from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from accounts import deletion
from accounts.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("email", "is_active", "is_staff", "date_joined", "deletion_requested_at")
    list_filter = ("is_active", "is_staff", "is_superuser", "deletion_requested_at")
    actions = ["delete_with_data"]
    search_fields = ("email",)
    ordering = ("email",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Тренировки", {"fields": ("rest_seconds_default", "weekly_goal_minutes")}),
        (
            "Права",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        # changelog_seen_at редактируемое: очистить поле — самый простой способ
        # снова увидеть точку-бейдж «Что нового».
        ("Даты", {"fields": ("last_login", "date_joined", "changelog_seen_at")}),
        ("Удаление", {"fields": ("deletion_requested_at",)}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)

    @admin.action(description="Удалить безвозвратно вместе с данными")
    def delete_with_data(self, request, queryset):
        """Стандартное удаление админки упирается в PROTECT: своё упражнение,
        использованное в своих тренировках, держит пользователя. Помощник
        удаляет в два шага — сначала тренировки, потом пользователя."""
        users = list(queryset.exclude(is_superuser=True))
        for user in users:
            deletion.delete_user_data(user)
        self.message_user(request, f"Удалено пользователей: {len(users)}.", messages.SUCCESS)
