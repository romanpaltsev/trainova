from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from accounts.models import User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("email", "is_active", "is_staff", "date_joined")
    list_filter = ("is_active", "is_staff", "is_superuser")
    search_fields = ("email",)
    ordering = ("email",)
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Тренировки", {"fields": ("rest_seconds_default",)}),
        (
            "Права",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Даты", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)
