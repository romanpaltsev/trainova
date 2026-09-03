"""Формы записи тренировок и личных справочников."""

from datetime import datetime, time

from django import forms
from django.db import IntegrityError, transaction
from django.utils import timezone

from workouts import services
from workouts.models import (
    LOCATION_NAME_MAX_LENGTH,
    CardioDetails,
    Exercise,
    Location,
    Sport,
    Workout,
    chosen_muscle_group,
    collapse_spaces,
    muscle_groups_for,
)

MAX_DURATION_HOURS = 24
# Время, которое ставим тренировке, записанной за прошедший день: точное время
# постфактум не вспомнить, а модели нужен datetime.
DEFAULT_TIME = time(12, 0)


class CardioWorkoutForm(forms.Form):
    """Кардио-тренировка целиком: и Workout, и CardioDetails.

    Дата и длительность вводятся так, как удобно с телефона: дата — одним полем,
    длительность — часы и минуты по отдельности.
    """

    sport = forms.ModelChoiceField(
        label="Вид спорта",
        queryset=Sport.objects.none(),
        empty_label=None,
        widget=forms.RadioSelect,
        error_messages={"required": "Выберите вид спорта."},
    )
    # Место необязательно: NULL в модели значит «не указано», и так записана вся
    # история до появления справочника.
    location = forms.ModelChoiceField(
        label="Место",
        queryset=Location.objects.none(),
        required=False,
        widget=forms.RadioSelect,
        error_messages={"invalid_choice": "Такого места у вас нет."},
    )
    # Своё поле перебивает чип — то же правило, что у muscle_group_own. Ввод
    # нового названия и есть создание места.
    location_own = forms.CharField(
        label="Новое место",
        required=False,
        max_length=LOCATION_NAME_MAX_LENGTH,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    date = forms.DateField(
        label="Дата",
        error_messages={"required": "Укажите дату.", "invalid": "Не похоже на дату."},
        # input_formats и format — обязательны: <input type="date"> понимает только
        # ISO-формат, а с локалью ru-ru Django по умолчанию рендерит 27.08.2026.
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"),
    )
    duration_hours = forms.IntegerField(
        label="ч",
        min_value=0,
        max_value=MAX_DURATION_HOURS,
        required=False,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "inputmode": "numeric", "placeholder": "0"}
        ),
    )
    duration_minutes = forms.IntegerField(
        label="мин",
        min_value=0,
        max_value=59,
        required=False,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "inputmode": "numeric", "placeholder": "00"}
        ),
    )
    distance_km = forms.DecimalField(
        label="Дистанция, км",
        max_digits=6,
        decimal_places=2,
        min_value=0.01,
        error_messages={
            "required": "Укажите дистанцию.",
            "invalid": "Дистанция — это число, например 7,2.",
            "min_value": "Дистанция должна быть больше нуля.",
        },
        widget=forms.NumberInput(
            attrs={"class": "form-control", "inputmode": "decimal", "step": "0.01"}
        ),
    )
    avg_heart_rate = forms.IntegerField(
        label="Средний пульс",
        help_text="необязательно",
        error_messages={"invalid": "Пульс — это целое число."},
        min_value=30,
        max_value=250,
        required=False,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "inputmode": "numeric", "placeholder": "напр. 142"}
        ),
    )
    # День, на который тренировка подготовлена. В отличие от `date`, может быть
    # в будущем — это и есть смысл плана, поэтому clean_date его не касается.
    planned_for = forms.DateField(
        label="На какой день",
        required=False,
        input_formats=["%Y-%m-%d"],
        error_messages={"invalid": "Не похоже на дату."},
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"),
    )
    note = forms.CharField(
        label="Заметка",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )

    def __init__(self, *args, user, instance=None, planned=False, **kwargs):
        self.user = user
        self.instance = instance
        self.planned = planned
        super().__init__(*args, **kwargs)
        if planned:
            # Даты записи и пульса у плана не бывает: первая появится, когда
            # тренировка состоится, второй — только после неё. Поля удаляются, а
            # не гасятся флагом required: без `date` форма физически не может
            # вычислить started_at, и «этой формой нельзя записать тренировку»
            # держится структурой, а не договорённостью.
            #
            # А вот поля длительности остаются: у плана они значат цель по
            # времени — тем же приёмом, каким distance_km служит и цели, и факту.
            # Плата за это честная: clean() и save() теперь смотрят на self.planned.
            for name in ("date", "avg_heart_rate"):
                del self.fields[name]
            # Обе цели необязательны: планов на неделю наготавливают пачкой, и
            # заставлять заполнять значения было бы издевательством.
            self.fields["distance_km"].required = False
        else:
            # Плановый день живёт только у черновика — у записанной тренировки
            # день известен из даты, а хранить оба значило бы «план vs факт».
            del self.fields["planned_for"]
        # Священное правило: только глобальные виды спорта и свои, только кардио.
        self.fields["sport"].queryset = Sport.objects.visible_to(user).filter(
            category=Sport.Category.CARDIO
        )
        # Глобальных мест не бывает, поэтому только свои — и чужое по id даст
        # ошибку валидации, а не тихую запись.
        self.fields["location"].queryset = Location.objects.filter(owner=user)
        if instance is not None:
            self.initial = {**self._initial_from_instance(instance), **self.initial}
        else:
            self.initial.setdefault("date", timezone.localdate())
            # Дефолт подставляем только новой и только несвязанной форме: у
            # связанной значение уже пришло, и запрос был бы лишним. На правке
            # подстановка запрещена — она подменила бы место записанной тренировки.
            if not self.is_bound:
                default = Location.objects.default_for(user)
                if default is not None:
                    self.initial.setdefault("location", default.pk)

    @staticmethod
    def _initial_from_instance(workout):
        # У черновика нет ни начала, ни длительности — это и есть его признак,
        # поэтому оба поля разбираются с оглядкой. Дата подставляется сегодняшняя:
        # план записывается тем днём, когда тренировка наконец состоялась.
        started_at = timezone.localtime(workout.started_at) if workout.started_at else None
        # Факт первым, иначе цель: открытый на запись план подставляет в поля
        # длительности то, что было загадано, — как цель по дистанции подставляется
        # в дистанцию. Правится поверх, если вышло иначе.
        planned_minutes = workout.duration_min or workout.target_duration_min
        hours, minutes = divmod(planned_minutes, 60) if planned_minutes else (None, None)
        cardio = getattr(workout, "cardio", None)
        return {
            "sport": workout.sport_id,
            # location_id, а не объект: get_instance тянет только sport, и
            # обращение к workout.location стоило бы отдельного запроса.
            "location": workout.location_id,
            "date": started_at.date() if started_at else timezone.localdate(),
            "duration_hours": hours or None,
            "duration_minutes": minutes or None,
            "distance_km": cardio.distance_km if cardio else None,
            "avg_heart_rate": cardio.avg_heart_rate if cardio else None,
            "note": workout.note,
        }

    def clean_date(self):
        date = self.cleaned_data["date"]
        if date > timezone.localdate():
            raise forms.ValidationError("Дата не может быть в будущем.")
        return date

    def clean(self):
        cleaned = super().clean()
        hours = cleaned.get("duration_hours") or 0
        minutes = cleaned.get("duration_minutes") or 0
        duration = hours * 60 + minutes
        if "duration_hours" in cleaned and "duration_minutes" in cleaned:
            # У плана пустая длительность значит «не загадывал», а не ошибку;
            # верхняя граница остаётся общей — цель в 30 часов тоже опечатка.
            if duration <= 0 and not self.planned:
                self.add_error("duration_minutes", "Укажите длительность тренировки.")
            elif duration > MAX_DURATION_HOURS * 60:
                self.add_error("duration_hours", "Слишком долгая тренировка.")
        cleaned["duration_min"] = duration
        return cleaned

    def chosen_location(self):
        """Место тренировки: новое название перебивает выбранный чип.

        Ввод названия и есть создание места, поэтому запись появляется только
        здесь — на сохранении тренировки, а не при открытии формы.
        """
        name = collapse_spaces(self.cleaned_data["location_own"])
        if name:
            return services.location_for_name(self.user, name)
        return self.cleaned_data["location"]

    def started_at(self):
        """Дата + время: для сегодняшней тренировки — текущее, иначе полдень."""
        date = self.cleaned_data["date"]
        now = timezone.localtime()
        moment = now.time() if date == now.date() else DEFAULT_TIME
        return timezone.make_aware(datetime.combine(date, moment))

    def save(self):
        workout = self.instance or Workout(user=self.user)
        workout.sport = self.cleaned_data["sport"]
        workout.location = self.chosen_location()
        if self.planned:
            # Черновик: время не идёт и длительности нет — ровно те же две
            # колонки, которыми состояние тренировки задаётся у силовой.
            # Длительность из формы уезжает в цель; ноль значит «не загадывал».
            workout.started_at = None
            workout.duration_min = None
            workout.target_duration_min = self.cleaned_data["duration_min"] or None
            workout.planned_for = self.cleaned_data.get("planned_for")
        else:
            workout.started_at = self.started_at()
            workout.duration_min = self.cleaned_data["duration_min"]
            # Цель заменяется фактом, плановый день — настоящей датой. То же
            # самое происходит с целью по дистанции строкой ниже, просто ей для
            # этого не нужно отдельное поле. planned_for обнулять обязательно:
            # его держит констрейнт planned_for_only_when_planned.
            workout.target_duration_min = None
            workout.planned_for = None
        workout.note = self.cleaned_data["note"]
        workout.save()

        distance = self.cleaned_data.get("distance_km")
        if distance is None:
            # «Цели по дистанции нет» выражается отсутствием строки — тот же
            # приём, что у ExerciseNote. Ветка delete сегодня всегда попадает в
            # пустоту (planned бывает только у новой тренировки), но делает
            # ветвление полным и переживёт появление правки плана.
            CardioDetails.objects.filter(workout=workout).delete()
        else:
            CardioDetails.objects.update_or_create(
                workout=workout,
                defaults={
                    "distance_km": distance,
                    # Пульс есть только у состоявшейся тренировки: у плана поля
                    # нет, а на записи черновика оно придёт из формы как обычно.
                    "avg_heart_rate": self.cleaned_data.get("avg_heart_rate"),
                },
            )
        return workout


class SportForm(forms.ModelForm):
    """Личный вид спорта: создаётся из формы тренировки, не уходя со страницы."""

    class Meta:
        model = Sport
        fields = ("name", "category")
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "напр. Гребля", "autofocus": True}
            ),
            "category": forms.RadioSelect,
        }
        error_messages = {
            "name": {"required": "Введите название."},
            "category": {"required": "Выберите категорию."},
        }

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        # У CharField с choices форма добавляет пустой вариант — в радиогруппе он лишний.
        self.fields["category"].choices = Sport.Category.choices

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        # Ограничение в БД не ловит личный дубль глобального имени: owner разный.
        if Sport.objects.visible_to(self.user).filter(name__iexact=name).exists():
            raise forms.ValidationError("Такой вид спорта у вас уже есть.")
        return name

    def save(self, commit=True):
        sport = super().save(commit=False)
        sport.owner = self.user
        if commit:
            sport.save()
        return sport


class ExerciseQuickForm(forms.ModelForm):
    """Быстрое создание упражнения из живого режима: название и единица.

    Единица по умолчанию — «вес × повторы», поэтому сценарий в зале остаётся
    «ввёл название → создать»: чипы трогают только для планки и подобных.
    """

    class Meta:
        model = Exercise
        fields = ("name", "measurement", "muscle_group")
        error_messages = {"name": {"required": "Введите название."}}

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        # Пустое значение считаем «как обычно», а не ошибкой: чипы могут не приехать
        # из устаревшей вкладки, а прерывать создание упражнения из-за этого глупо.
        self.fields["measurement"].required = False

    def clean_measurement(self):
        return self.cleaned_data.get("measurement") or Exercise.Measurement.WEIGHT_REPS

    def clean_muscle_group(self):
        """Группа мышц необязательна; выбор чипа и своё поле сводит одно правило."""
        return chosen_muscle_group(self.data, muscle_groups_for(self.user))

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def save_for_user(self):
        """Вернуть видимое упражнение с таким именем или создать личное.

        Посреди тренировки ввод существующего названия означает «добавь его»,
        а не ошибку дубля — намеренное отличие от SportForm. Единица при этом
        не применяется: переопределить измерение чужого (в том числе глобального)
        упражнения вводом его названия нельзя.
        """
        name = self.cleaned_data["name"]
        existing = Exercise.objects.visible_to(self.user).filter(name__iexact=name).first()
        if existing is not None:
            return existing
        exercise = self.save(commit=False)
        exercise.owner = self.user
        try:
            # Savepoint: гонка двух вкладок упрётся в уникальный индекс, и тогда
            # правильный ответ — взять только что созданную запись, а не 500.
            with transaction.atomic():
                exercise.save()
        except IntegrityError:
            return Exercise.objects.visible_to(self.user).get(name__iexact=name)
        return exercise
