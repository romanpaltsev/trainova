from django.contrib import admin

from workouts.models import (
    BodyMeasurement,
    BodyMetric,
    CardioPart,
    CatalogRequest,
    ChangelogEntry,
    DeletedWorkout,
    Exercise,
    ExerciseMachine,
    ExerciseNote,
    ExerciseSettings,
    Location,
    MachineBrand,
    MachineModel,
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


@admin.register(BodyMetric)
class BodyMetricAdmin(CatalogAdmin):
    """Параметры тела: общие правятся здесь, свои — в разделе «Мои замеры»."""

    list_display = ("name", "unit", "owner_display")


@admin.register(BodyMeasurement)
class BodyMeasurementAdmin(admin.ModelAdmin):
    list_display = ("measured_on", "metric", "value", "user")
    list_filter = ("metric",)
    date_hierarchy = "measured_on"
    autocomplete_fields = ("user", "metric")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("user", "metric")


@admin.register(Exercise)
class ExerciseAdmin(CatalogAdmin):
    list_display = ("name", "muscle_group", "equipment", "measurement", "owner_display")
    list_filter = ("measurement", "muscle_group", "equipment", "owner")


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


@admin.register(MachineBrand)
class MachineBrandAdmin(CatalogAdmin):
    list_display = ("name", "owner_display")


@admin.register(MachineModel)
class MachineModelAdmin(CatalogAdmin):
    list_display = ("name", "brand", "owner_display")
    search_fields = ("name", "brand__name")
    autocomplete_fields = ("owner", "brand")

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("brand")


@admin.register(ExerciseMachine)
class ExerciseMachineAdmin(admin.ModelAdmin):
    """Тренажёры пользователей: упражнение × место — производитель и модель."""

    list_display = ("user", "exercise", "location", "brand", "model")
    list_filter = ("user",)
    search_fields = (
        "user__email",
        "exercise__name",
        "location__name",
        "brand__name",
        "model__name",
    )
    autocomplete_fields = ("user", "exercise", "location", "brand", "model")
    list_select_related = ("user", "exercise", "location", "brand", "model")


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


class CardioPartInline(admin.TabularInline):
    """Табличный, а не стопкой: частей у тренировки может быть несколько."""

    model = CardioPart
    extra = 0
    autocomplete_fields = ("sport",)
    fields = ("sport", "distance_km", "duration_min", "avg_heart_rate")


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
    inlines = (StrengthSetInline, ExerciseNoteInline, CardioPartInline)

    @admin.display(description="состояние")
    def state(self, obj):
        """Иначе черновик и идущая в списке отличались бы только пустой датой."""
        if obj.is_planned:
            return "черновик"
        return "записана" if obj.is_finished else "идёт"

    @admin.display(description="содержимое")
    def summary(self, obj):
        """Подходы и кардио-части разом: у смешанной тренировки есть и то, и другое."""
        parts = []
        count = obj.sets.count()
        if count:
            parts.append(f"{count} подх.")
        parts += [str(part) for part in obj.cardio_parts.all()]
        return " · ".join(parts) or "—"

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("sport", "user")


@admin.register(DeletedWorkout)
class DeletedWorkoutAdmin(admin.ModelAdmin):
    """Корзина только на просмотр: восстанавливает сам пользователь, а правка
    снимка руками сломала бы восстановление."""

    list_display = ("title", "subtitle", "user", "deleted_at")
    list_filter = ("deleted_at",)
    search_fields = ("title", "user__email")
    readonly_fields = ("user", "deleted_at", "title", "subtitle", "color_key", "payload")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ChangelogEntry)
class ChangelogEntryAdmin(admin.ModelAdmin):
    """Новости проекта: единственное место, где их создают и правят."""

    list_display = ("published_at", "kind", "title", "is_published")
    list_filter = ("kind", "is_published")
    date_hierarchy = "published_at"
    search_fields = ("title", "body")


@admin.register(CatalogRequest)
class CatalogRequestAdmin(admin.ModelAdmin):
    """Заявки в общий справочник — только посмотреть: решают их в «Админке»
    (принятие делает запись общей и пишет автору, это не правка строки)."""

    list_display = ("__str__", "user", "status", "created_at", "decided_at")
    list_filter = ("status", "kind")
    search_fields = ("user__email", "exercise__name", "brand__name", "model__name")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
