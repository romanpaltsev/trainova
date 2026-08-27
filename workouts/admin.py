from django.contrib import admin

from workouts.models import CardioDetails, Exercise, Sport, StrengthSet, Workout


class CatalogAdmin(admin.ModelAdmin):
    """Общая настройка справочников: глобальные записи правятся только здесь."""

    list_filter = ("owner",)
    search_fields = ("name",)
    autocomplete_fields = ("owner",)

    @admin.display(description="владелец", ordering="owner")
    def owner_display(self, obj):
        return obj.owner or "— глобальное —"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("owner")


@admin.register(Sport)
class SportAdmin(CatalogAdmin):
    list_display = ("name", "category", "owner_display")
    list_filter = ("category", "owner")


@admin.register(Exercise)
class ExerciseAdmin(CatalogAdmin):
    list_display = ("name", "muscle_group", "owner_display")
    list_filter = ("muscle_group", "owner")


class StrengthSetInline(admin.TabularInline):
    model = StrengthSet
    extra = 3
    autocomplete_fields = ("exercise",)
    fields = ("exercise", "set_number", "weight_kg", "reps")


class CardioDetailsInline(admin.StackedInline):
    model = CardioDetails
    extra = 0
    max_num = 1
    fields = ("distance_km", "avg_heart_rate")


@admin.register(Workout)
class WorkoutAdmin(admin.ModelAdmin):
    list_display = ("started_at", "sport", "user", "duration_min", "summary")
    list_filter = ("sport__category", "sport", "user")
    date_hierarchy = "started_at"
    search_fields = ("user__email", "note")
    autocomplete_fields = ("user", "sport")
    inlines = (StrengthSetInline, CardioDetailsInline)

    @admin.display(description="содержимое")
    def summary(self, obj):
        if obj.sport.is_strength:
            count = obj.sets.count()
            return f"{count} подх." if count else "—"
        cardio = getattr(obj, "cardio", None)
        return f"{cardio.distance_km} км" if cardio else "—"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("sport", "user")
