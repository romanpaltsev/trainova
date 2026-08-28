"""Формы записи тренировок и личных справочников."""

from datetime import datetime, time

from django import forms
from django.db import IntegrityError, transaction
from django.utils import timezone

from workouts.models import CardioDetails, Exercise, Sport, Workout

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
    note = forms.CharField(
        label="Заметка",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )

    def __init__(self, *args, user, instance=None, **kwargs):
        self.user = user
        self.instance = instance
        super().__init__(*args, **kwargs)
        # Священное правило: только глобальные виды спорта и свои, только кардио.
        self.fields["sport"].queryset = Sport.objects.visible_to(user).filter(
            category=Sport.Category.CARDIO
        )
        if instance is not None:
            self.initial = {**self._initial_from_instance(instance), **self.initial}
        else:
            self.initial.setdefault("date", timezone.localdate())

    @staticmethod
    def _initial_from_instance(workout):
        started_at = timezone.localtime(workout.started_at)
        hours, minutes = divmod(workout.duration_min, 60)
        cardio = getattr(workout, "cardio", None)
        return {
            "sport": workout.sport_id,
            "date": started_at.date(),
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
            if duration <= 0:
                self.add_error("duration_minutes", "Укажите длительность тренировки.")
            elif duration > MAX_DURATION_HOURS * 60:
                self.add_error("duration_hours", "Слишком долгая тренировка.")
        cleaned["duration_min"] = duration
        return cleaned

    def started_at(self):
        """Дата + время: для сегодняшней тренировки — текущее, иначе полдень."""
        date = self.cleaned_data["date"]
        now = timezone.localtime()
        moment = now.time() if date == now.date() else DEFAULT_TIME
        return timezone.make_aware(datetime.combine(date, moment))

    def save(self):
        workout = self.instance or Workout(user=self.user)
        workout.sport = self.cleaned_data["sport"]
        workout.started_at = self.started_at()
        workout.duration_min = self.cleaned_data["duration_min"]
        workout.note = self.cleaned_data["note"]
        workout.save()

        CardioDetails.objects.update_or_create(
            workout=workout,
            defaults={
                "distance_km": self.cleaned_data["distance_km"],
                "avg_heart_rate": self.cleaned_data["avg_heart_rate"],
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
    """Быстрое создание упражнения из живого режима: одно поле «название»."""

    class Meta:
        model = Exercise
        fields = ("name",)
        error_messages = {"name": {"required": "Введите название."}}

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def save_for_user(self):
        """Вернуть видимое упражнение с таким именем или создать личное.

        Посреди тренировки ввод существующего названия означает «добавь его»,
        а не ошибку дубля — намеренное отличие от SportForm.
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
