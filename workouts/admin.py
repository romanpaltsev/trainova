from django.contrib import admin

from workouts.models import (
    CardioDetails,
    ChangelogEntry,
    Exercise,
    ExerciseNote,
    ExerciseSettings,
    Location,
    Sport,
    StrengthSet,
    Workout,
)


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
    list_display = ("name", "muscle_group", "measurement", "owner_display")
    list_filter = ("measurement", "muscle_group", "owner")


@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    """Места пользователей — на случай разбора «почему тренировка не там».

    Не CatalogAdmin: глобальных мест не бывает, и «— глобальное —» в колонке
    владельца было бы неправдой. search_fields обязателен: без него не работает
    autocomplete_fields = ("location",) в WorkoutAdmin.
    """

    list_display = ("name", "owner", "is_default")
    list_filter = ("owner",)
    search_fields = ("name", "owner__email")
    autocomplete_fields = ("owner",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("owner")


@admin.register(ExerciseSettings)
class ExerciseSettingsAdmin(admin.ModelAdmin):
    """Личные настройки упражнений — на случай разбора «почему шаг такой»."""

    list_display = ("user", "exercise", "weight_step")
    list_filter = ("user",)
    search_fields = ("user__email", "exercise__name")
    autocomplete_fields = ("user", "exercise")


class StrengthSetInline(admin.TabularInline):
    model = StrengthSet
    extra = 3
    autocomplete_fields = ("exercise",)
    # Явный список: без duration_sec и measurement подход временного упражнения
    # из админки создать нельзя — он упрётся в set_fields_match_measurement.
    fields = ("exercise", "set_number", "measurement", "weight_kg", "reps", "duration_sec", "done")


class ExerciseNoteInline(admin.TabularInline):
    model = ExerciseNote
    extra = 0
    autocomplete_fields = ("exercise",)
    fields = ("exercise", "text")


class CardioDetailsInline(admin.StackedInline):
    model = CardioDetails
    extra = 0
    max_num = 1
    fields = ("distance_km", "avg_heart_rate")


@admin.register(Workout)
class WorkoutAdmin(admin.ModelAdmin):
    list_display = ("started_at", "state", "sport", "user", "duration_min", "summary")
    list_filter = ("sport__category", "sport", "user")
    # Черновики (started_at пуст) в срезы по датам не попадают — их там и нет.
    date_hierarchy = "started_at"
    search_fields = ("user__email", "note")
    # location в list_filter не идёт: у каждого пользователя свои места,
    # и фильтр разбух бы объединением всех справочников.
    autocomplete_fields = ("user", "sport", "location")
    inlines = (StrengthSetInline, ExerciseNoteInline, CardioDetailsInline)

    @admin.display(description="состояние")
    def state(self, obj):
        """Иначе черновик и идущая в списке отличались бы только пустой датой."""
        if obj.is_planned:
            return "черновик"
        return "записана" if obj.is_finished else "идёт"

    @admin.display(description="содержимое")
    def summary(self, obj):
        if obj.sport.is_strength:
            count = obj.sets.count()
            return f"{count} подх." if count else "—"
        cardio = getattr(obj, "cardio", None)
        return f"{cardio.distance_km} км" if cardio else "—"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("sport", "user")


@admin.register(ChangelogEntry)
class ChangelogEntryAdmin(admin.ModelAdmin):
    """Новости проекта: единственное место, где их создают и правят."""

    list_display = ("published_at", "kind", "title", "is_published")
    list_filter = ("kind", "is_published")
    date_hierarchy = "published_at"
    search_fields = ("title", "body")
