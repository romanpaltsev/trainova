from django.contrib import admin

from feedback.models import Feedback


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    """Обращения; отвечают на них в «Админке» приложения — там ответ уходит письмом."""

    list_display = ("created_at", "kind", "status", "user", "text")
    list_filter = ("kind", "status")
    date_hierarchy = "created_at"
    search_fields = ("text", "reply", "user__email")
    list_select_related = ("user",)
