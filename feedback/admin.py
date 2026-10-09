from django.contrib import admin

from feedback.models import Feedback, FeedbackMessage


class FeedbackMessageInline(admin.TabularInline):
    model = FeedbackMessage
    extra = 0


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    """Обращения; отвечают на них в «Админке» приложения — там сообщение уходит письмом."""

    list_display = ("created_at", "kind", "status", "user", "text")
    list_filter = ("kind", "status")
    date_hierarchy = "created_at"
    search_fields = ("text", "messages__text", "user__email")
    list_select_related = ("user",)
    inlines = [FeedbackMessageInline]
