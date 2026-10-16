"""Формы записи тренировок и личных справочников."""

from decimal import Decimal

from django import forms
from django.core.validators import MaxLengthValidator
from django.utils import formats, timezone

from workouts import excel, excel_import, services
from workouts.models import (
    BODY_METRIC_NAME_MAX_LENGTH,
    BODY_METRIC_UNIT_MAX_LENGTH,
    CARDIO_ELEVATION_MAX,
    CARDIO_INCLINE_MAX,
    CARDIO_INCLINE_MIN,
    CARDIO_LEVEL_MAX,
    CARDIO_POWER_MAX,
    CATALOG_REQUEST_TEXT_MAX_LENGTH,
    GEAR_NAME_MAX_LENGTH,
    LOCATION_NAME_MAX_LENGTH,
    MACHINE_BRAND_MAX_LENGTH,
    MACHINE_MODEL_MAX_LENGTH,
    BodyMeasurement,
    BodyMetric,
    CardioPart,
    Exercise,
    Gear,
    Location,
    MachineBrand,
    MachineModel,
    Sport,
    Workout,
    chosen_equipment,
    chosen_muscle_group,
    collapse_spaces,
    facets_for,
    parse_measurement_value,
)

MAX_DURATION_HOURS = 24


def duration_fields():
    """Пара полей «ч / мин»: одна длительность, набранная с телефона в два поля.

    Возвращает кортеж, чтобы в теле формы читалось как объявление:
    `duration_hours, duration_minutes = duration_fields()`. Функция, а не общий
    базовый класс: формам нужны одинаковые поля, но не одинаковое поведение — у
    кардио-плана та же пара значит цель по времени.

    Имена полей одни и те же у всех потребителей: на них завязан партиал
    _duration_fields.html.
    """
    return (
        forms.IntegerField(
            label="ч",
            min_value=0,
            max_value=MAX_DURATION_HOURS,
            required=False,
            widget=forms.NumberInput(
                attrs={"class": "form-control", "inputmode": "numeric", "placeholder": "0"}
            ),
        ),
        forms.IntegerField(
            label="мин",
            min_value=0,
            max_value=59,
            required=False,
            widget=forms.NumberInput(
                attrs={"class": "form-control", "inputmode": "numeric", "placeholder": "00"}
            ),
        ),
    )


def clean_duration(cleaned, *, required):
    """Длительность из пары «ч / мин» — общая проверка обеих форм.

    `required=False` — это цель по времени у плана: пустая значит «не загадывал»,
    а не ошибку. Верхняя граница общая: цель в 30 часов тоже опечатка. Ошибка
    возвращается парой «поле, текст», потому что вешать её умеет только форма.
    """
    duration = (cleaned.get("duration_hours") or 0) * 60 + (cleaned.get("duration_minutes") or 0)
    cleaned["duration_min"] = duration
    if duration <= 0:
        return ("duration_minutes", "Укажите длительность тренировки.") if required else None
    if duration > MAX_DURATION_HOURS * 60:
        return "duration_hours", "Слишком долгая тренировка."
    return None


def date_field():
    """Поле даты тренировки.

    input_formats и format — обязательны: <input type="date"> понимает только
    ISO-формат, а с локалью ru-ru Django по умолчанию рендерит 27.08.2026.
    """
    return forms.DateField(
        label="Дата",
        error_messages={"required": "Укажите дату.", "invalid": "Не похоже на дату."},
        input_formats=["%Y-%m-%d"],
        widget=forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"),
    )


def time_field():
    """Время начала — необязательное: из тетрадки его обычно не вспомнить.

    Форматы заданы явно по той же причине, что у даты: <input type="time">
    принимает и отдаёт только ЧЧ:ММ.
    """
    return forms.TimeField(
        label="Время",
        required=False,
        error_messages={"invalid": "Не похоже на время."},
        input_formats=["%H:%M", "%H:%M:%S"],
        widget=forms.TimeInput(attrs={"type": "time", "class": "form-control"}, format="%H:%M"),
    )


def cardio_machine_fields():
    """Тренажёр кардио-части и его настройки: четыре необязательных поля.

    Кортеж, как у duration_fields: формам нужны одинаковые поля, а базовый класс
    не подошёл бы — одна из них ModelForm. Имена полей одни у всех потребителей:
    на них завязаны партиал _cardio_machine_fields.html и CardioMachineView.

    Производитель и модель — текстом с подсказками (datalist), а не выбором из
    списка: окна выбора поверх формы не откроешь, а новое название — обычное
    дело. Запись справочника заводится только при сохранении части (контракт
    machine_*_for_name: совпало имя — это он), иначе форма, упавшая на другой
    ошибке, засорила бы справочник. Уклон — localize: «2,5» с запятой.
    """
    return (
        forms.CharField(
            label="Производитель",
            required=False,
            max_length=MACHINE_BRAND_MAX_LENGTH,
            widget=forms.TextInput(
                attrs={
                    "class": "form-control",
                    "list": "cardio-brands",
                    "autocomplete": "off",
                    "placeholder": "напр. Technogym",
                }
            ),
        ),
        forms.CharField(
            label="Модель",
            required=False,
            max_length=MACHINE_MODEL_MAX_LENGTH,
            widget=forms.TextInput(
                attrs={"class": "form-control", "list": "cardio-models", "autocomplete": "off"}
            ),
        ),
        forms.DecimalField(
            label="Уклон, %",
            required=False,
            max_digits=3,
            decimal_places=1,
            min_value=CARDIO_INCLINE_MIN,
            max_value=CARDIO_INCLINE_MAX,
            localize=True,
            error_messages={
                "invalid": "Уклон — это число, например 2,5.",
                "min_value": "Уклон — от −10 до 40 %%.",
                "max_value": "Уклон — от −10 до 40 %%.",
                "max_decimal_places": "Уклон — с одним знаком после запятой.",
                "max_digits": "Уклон — от −10 до 40 %%.",
            },
            widget=forms.TextInput(
                attrs={"class": "form-control", "inputmode": "decimal", "placeholder": "напр. 3"}
            ),
        ),
        forms.IntegerField(
            label="Уровень нагрузки",
            required=False,
            min_value=1,
            max_value=CARDIO_LEVEL_MAX,
            error_messages={
                "invalid": "Уровень — целое число.",
                "min_value": f"Уровень — от 1 до {CARDIO_LEVEL_MAX}.",
                "max_value": f"Уровень — от 1 до {CARDIO_LEVEL_MAX}.",
            },
            widget=forms.NumberInput(
                attrs={"class": "form-control", "inputmode": "numeric", "placeholder": "напр. 8"}
            ),
        ),
    )


CARDIO_MACHINE_FIELDS = ("machine_brand", "machine_model", "incline_pct", "level")


def cardio_machine_initial(part):
    """Значения полей тренажёра из части — для правки и для подстановки прошлого."""
    if part is None:
        return {}
    return {
        "machine_brand": part.brand.name if part.brand_id else "",
        "machine_model": part.model.name if part.model_id else "",
        # «4», а не «4,0»: поле с одним знаком после запятой рисовало бы ноль.
        "incline_pct": (
            part.incline_pct.to_integral()
            if part.incline_pct is not None and part.incline_pct == part.incline_pct.to_integral()
            else part.incline_pct
        ),
        "level": part.level,
    }


def clean_cardio_machine(form, cleaned):
    """Модель без производителя не сохранить — сказать об этом у поля модели."""
    brand = collapse_spaces(cleaned.get("machine_brand") or "")
    model = collapse_spaces(cleaned.get("machine_model") or "")
    if model and not brand:
        form.add_error("machine_brand", "Укажите производителя модели.")


def has_cardio_machine(cleaned):
    return any(cleaned.get(name) not in (None, "") for name in CARDIO_MACHINE_FIELDS)


def apply_cardio_machine(user, part, cleaned):
    """Перенести тренажёр и настройки из формы в часть (без save)."""
    brand_name = collapse_spaces(cleaned.get("machine_brand") or "")
    model_name = collapse_spaces(cleaned.get("machine_model") or "")
    brand = services.machine_brand_for_name(user, brand_name) if brand_name else None
    part.brand = brand
    part.model = (
        services.machine_model_for_name(user, brand, model_name)
        if brand is not None and model_name
        else None
    )
    part.incline_pct = cleaned.get("incline_pct")
    part.level = cleaned.get("level")


class CardioMachineForm(forms.Form):
    """Только поля тренажёра — для перерисовки блока (CardioMachineView)."""

    machine_brand, machine_model, incline_pct, level = cardio_machine_fields()


def distance_widget():
    """Поле дистанции одно на три формы: блок маршрута перерисовывается своей."""
    return forms.NumberInput(
        attrs={"class": "form-control", "inputmode": "decimal", "step": "0.01"}
    )


class ZeroIsEmptyField(forms.IntegerField):
    """Целое, где ноль — «не указано», как у времени части.

    Набор высоты и мощность: ровный круг законен, но «0 м» и «0 Вт» в строке
    части — шум, а в базе ноль и NULL значили бы одно и то же двумя способами.
    """

    def clean(self, value):
        return super().clean(value) or None


def elevation_field():
    return ZeroIsEmptyField(
        label="Набор высоты, м",
        required=False,
        min_value=0,
        max_value=CARDIO_ELEVATION_MAX,
        error_messages={
            "invalid": "Набор высоты — целое число метров.",
            "min_value": "Набор высоты не бывает отрицательным.",
            "max_value": f"Набор высоты — до {CARDIO_ELEVATION_MAX} м.",
        },
        widget=forms.NumberInput(
            attrs={"class": "form-control", "inputmode": "numeric", "placeholder": "напр. 120"}
        ),
    )


def style_field():
    """Стиль хода: чипы «Конёк | Классика» — только у видов спорта со стилями.

    Пусто — не указан. Сохраняют его формы только у такого вида спорта
    (cardio_style_of): сменили «Лыжи» на «Бег» — выбранный конёк не должен
    приехать в пробежку.
    """
    return forms.ChoiceField(
        label="Стиль",
        required=False,
        choices=[("", "не указан"), *CardioPart.Style.choices],
        widget=forms.RadioSelect,
        error_messages={"invalid_choice": "Такого стиля нет."},
    )


def cardio_style_of(sport, cleaned):
    return cleaned.get("style", "") if sport is not None and sport.has_styles else ""


def power_field():
    """Средняя мощность за занятие — с табло тренажёра или с измерителя."""
    return ZeroIsEmptyField(
        label="Мощность, Вт",
        required=False,
        min_value=0,
        max_value=CARDIO_POWER_MAX,
        error_messages={
            "invalid": "Мощность — целое число ватт.",
            "min_value": "Мощность не бывает отрицательной.",
            "max_value": f"Мощность — до {CARDIO_POWER_MAX} Вт.",
        },
        widget=forms.NumberInput(
            attrs={"class": "form-control", "inputmode": "numeric", "placeholder": "напр. 180"}
        ),
    )


def gear_fields():
    """Снаряжение части: чип своей вещи или новое название.

    Пара, как у места (location / location_own): ввод названия и есть создание
    (services.gear_for_name), и запись появляется только при сохранении части —
    форма, упавшая на другой ошибке, не засоряет «Моё снаряжение».
    """
    return (
        forms.ModelChoiceField(
            label="Снаряжение",
            queryset=Gear.objects.none(),
            required=False,
            widget=forms.RadioSelect,
            error_messages={"invalid_choice": "Такого снаряжения у вас нет."},
        ),
        forms.CharField(label="Новое снаряжение", required=False, max_length=GEAR_NAME_MAX_LENGTH),
    )


def chosen_gear(form):
    """Снаряжение из формы: новое название перебивает чип, как у места."""
    name = collapse_spaces(form.cleaned_data.get("gear_own") or "")
    if name:
        return services.gear_for_name(form.user, name)
    return form.cleaned_data.get("gear")


class GearForm(forms.Form):
    """Окно правки снаряжения: название и пробег до дневника."""

    name = forms.CharField(
        label="Название",
        max_length=GEAR_NAME_MAX_LENGTH,
        error_messages={"required": "Введите название."},
        widget=forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
    )
    initial_km = forms.DecimalField(
        label="Пробег до дневника, км",
        required=False,
        min_value=0,
        max_digits=7,
        decimal_places=1,
        localize=True,
        error_messages={
            "invalid": "Пробег — число, например 350 или 12,5.",
            "min_value": "Пробег не бывает отрицательным.",
            "max_digits": "Столько не пробегают.",
            "max_decimal_places": "Пробег — с одним знаком после запятой.",
        },
        widget=forms.TextInput(attrs={"class": "form-control", "inputmode": "decimal"}),
    )

    def __init__(self, *args, user, instance, **kwargs):
        self.user = user
        self.instance = instance
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = collapse_spaces(self.cleaned_data["name"])
        if not name:
            raise forms.ValidationError("Введите название.")
        taken = (
            Gear.objects.filter(owner=self.user, name__iexact=name)
            .exclude(pk=self.instance.pk)
            .exists()
        )
        if taken:
            raise forms.ValidationError("Снаряжение с таким названием у вас уже есть.")
        return name

    def save(self):
        self.instance.name = self.cleaned_data["name"]
        self.instance.initial_km = self.cleaned_data["initial_km"] or 0
        self.instance.save(update_fields=["name", "initial_km"])
        return self.instance


CARDIO_ROUTE_FIELDS = ("distance_km", "elevation_gain_m")


class CardioRouteForm(forms.Form):
    """Только поля маршрута — для перерисовки блока (CardioRouteView).

    Виджеты те же, что у форм кардио: блок приходит на место своих же полей, и
    отличаться от них ему нельзя.
    """

    distance_km = forms.DecimalField(required=False, widget=distance_widget())
    elevation_gain_m = elevation_field()


def cardio_route_initial(part):
    """Дистанция и набор высоты из прошлой части — подстановка маршрута.

    Дистанция — как её набрал бы человек: «7.4», а не «7.40» из колонки с двумя
    знаками после запятой.
    """
    if part is None:
        return {}
    distance = part.distance_km
    if distance is not None:
        distance = (
            distance.quantize(Decimal(1))
            if distance == distance.to_integral()
            else distance.normalize()
        )
    return {"distance_km": distance, "elevation_gain_m": part.elevation_gain_m}


class CardioWorkoutForm(forms.Form):
    """Кардио-тренировка целиком: и Workout, и CardioPart.

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
    style = style_field()
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
    date = date_field()
    # Время необязательно и живёт рядом с датой: пустое значит «как обычно»
    # (см. combine_started_at). Нужно оно правке — без него тренировка,
    # записанная вечером за прошлый день, при каждом сохранении уезжала бы
    # на полдень.
    time = time_field()
    duration_hours, duration_minutes = duration_fields()
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
        widget=distance_widget(),
    )
    elevation_gain_m = elevation_field()
    avg_power_w = power_field()
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
    machine_brand, machine_model, incline_pct, level = cardio_machine_fields()
    gear, gear_own = gear_fields()

    def __init__(self, *args, user, instance=None, planned=False, **kwargs):
        self.user = user
        self.instance = instance
        self.planned = planned
        # День, на который замахнулись датой в будущем: по нему шаблон предлагает
        # подготовить тренировку вместо записи. Атрибут заводим всегда — шаблон
        # спрашивает его и на GET, а «тихо False из-за отсутствия» — случайность,
        # а не контракт.
        self.future_date = None
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
            #
            # Набора высоты и мощности у плана нет по той же причине, что и
            # пульса: цель плана — километры и время, остальное — то, что вышло.
            #
            # Стиля и снаряжения у плана тоже нет: план — это цели, километры и
            # время.
            for name in (
                "date",
                "time",
                "avg_heart_rate",
                "avg_power_w",
                "elevation_gain_m",
                "style",
                "gear",
                "gear_own",
            ):
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
        if "gear" in self.fields:
            # Своё — и на пенсии тоже: правка части с такой вещью должна сохраняться.
            self.fields["gear"].queryset = Gear.objects.filter(owner=user)
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
        # У чистого кардио часть ровно одна — эта форма только такие и правит
        # (тренировку с подходами она не откроет вовсе).
        cardio = workout.cardio_parts.first()
        return {
            "sport": workout.sport_id,
            # location_id, а не объект: get_instance тянет только sport, и
            # обращение к workout.location стоило бы отдельного запроса.
            "location": workout.location_id,
            "date": started_at.date() if started_at else timezone.localdate(),
            # У черновика времени нет — пустое поле и значит «как обычно».
            "time": started_at.time() if started_at else None,
            "duration_hours": hours or None,
            "duration_minutes": minutes or None,
            "distance_km": cardio.distance_km if cardio else None,
            "elevation_gain_m": cardio.elevation_gain_m if cardio else None,
            "style": cardio.style if cardio else "",
            "avg_heart_rate": cardio.avg_heart_rate if cardio else None,
            "avg_power_w": cardio.avg_power_w if cardio else None,
            "gear": cardio.gear_id if cardio else None,
            "note": workout.note,
            **cardio_machine_initial(cardio),
        }

    def clean(self):
        cleaned = super().clean()
        # Ключей нет, когда поле уже дало свою ошибку: пересчитывать по обрывку
        # значило бы повесить вторую, противоречащую первой.
        if "duration_hours" in cleaned and "duration_minutes" in cleaned:
            error = clean_duration(cleaned, required=not self.planned)
            if error:
                self.add_error(*error)
        clean_cardio_machine(self, cleaned)
        if "date" in cleaned:
            # Проверяем собранный момент, а не дату: сегодняшнее число с
            # временем 23:00 в десять утра — это будущее, и одна дата такое
            # пропустила бы. У плана поля даты нет вовсе — ветка не про него.
            started_at = services.combine_started_at(cleaned["date"], cleaned.get("time"))
            if started_at > timezone.now():
                # Запомнить день нужно ДО add_error: тот удаляет ключ из
                # cleaned_data, и обратный порядок дал бы KeyError.
                self.future_date = cleaned["date"]
                self.add_error("date", "Дата не может быть в будущем.")
            else:
                cleaned["started_at"] = started_at
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
            workout.started_at = self.cleaned_data["started_at"]
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
        # Тренажёр держит строку части так же, как цель по дистанции: у плана
        # «на дорожке, уклон 5» без километров — законная заготовка.
        if distance is None and not has_cardio_machine(self.cleaned_data):
            # «Цели по дистанции нет» выражается отсутствием строки — тот же
            # приём, что у ExerciseNote. Ветка delete сегодня всегда попадает в
            # пустоту (planned бывает только у новой тренировки), но делает
            # ветвление полным и переживёт появление правки плана.
            workout.cardio_parts.all().delete()
        else:
            # Часть здесь ровно одна и совпадает с тренировкой целиком, поэтому
            # вид спорта и длительность у неё те же: это тот инвариант, на
            # который опираются скорость и темп, считая по полям части.
            part = workout.cardio_parts.first() or CardioPart(workout=workout)
            part.sport = workout.sport
            part.duration_min = workout.duration_min
            part.distance_km = distance
            # Пульс и набор высоты есть только у состоявшейся тренировки: у плана
            # полей нет, а на записи черновика они придут из формы как обычно.
            part.avg_heart_rate = self.cleaned_data.get("avg_heart_rate")
            part.elevation_gain_m = self.cleaned_data.get("elevation_gain_m")
            part.avg_power_w = self.cleaned_data.get("avg_power_w")
            part.style = cardio_style_of(part.sport, self.cleaned_data)
            if not self.planned:
                part.gear = chosen_gear(self)
            apply_cardio_machine(self.user, part, self.cleaned_data)
            part.save()
        return workout


class CardioPartForm(forms.ModelForm):
    """Кардио-часть тренировки: вид спорта, дистанция, время, пульс.

    Отдельная форма, а не режим CardioWorkoutForm: та собирает тренировку
    целиком (дату, место, длительность занятия), а часть — это кусок внутри уже
    существующей тренировки, и спрашивать у неё дату второй раз было бы враньём.

    Всё, кроме вида спорта, необязательно: «двадцать минут на дорожке, не
    мерил» — законная часть, и заставлять придумывать числа незачем.
    """

    duration_hours, duration_minutes = duration_fields()
    machine_brand, machine_model, incline_pct, level = cardio_machine_fields()
    # Своими полями, а не полями модели: ноль здесь значит «не указан» (ZeroIsEmptyField).
    elevation_gain_m = elevation_field()
    avg_power_w = power_field()
    # Тоже своим: пишется только у вида спорта со стилями (cardio_style_of).
    style = style_field()
    gear, gear_own = gear_fields()

    class Meta:
        model = CardioPart
        fields = ("sport", "distance_km", "elevation_gain_m", "avg_heart_rate", "avg_power_w")
        error_messages = {
            "sport": {
                "required": "Выберите вид спорта.",
                "invalid_choice": "Такого вида спорта у вас нет.",
            },
            "distance_km": {"invalid": "Дистанция — это число, например 7,2."},
            "avg_heart_rate": {"invalid": "Пульс — это целое число."},
        }
        widgets = {
            "sport": forms.RadioSelect,
            "distance_km": distance_widget(),
            "avg_heart_rate": forms.NumberInput(
                attrs={"class": "form-control", "inputmode": "numeric", "placeholder": "напр. 142"}
            ),
        }

    def __init__(self, *args, user, workout, **kwargs):
        self.user = user
        self.workout = workout
        super().__init__(*args, **kwargs)
        # Священное правило: только глобальные виды спорта и свои, только кардио.
        self.fields["sport"].queryset = Sport.objects.visible_to(user).filter(
            category=Sport.Category.CARDIO
        )
        self.fields["sport"].empty_label = None
        self.fields["distance_km"].required = False
        self.fields["gear"].queryset = Gear.objects.filter(owner=user)
        if self.instance.pk:
            hours, minutes = divmod(self.instance.duration_min or 0, 60)
            self.initial.setdefault("duration_hours", hours or None)
            self.initial.setdefault("duration_minutes", minutes or None)
            for name, value in cardio_machine_initial(self.instance).items():
                self.initial.setdefault(name, value)
            self.initial.setdefault("style", self.instance.style)
            self.initial.setdefault("gear", self.instance.gear_id)

    def clean_distance_km(self):
        distance = self.cleaned_data.get("distance_km")
        if distance is not None and distance <= 0:
            raise forms.ValidationError("Дистанция должна быть больше нуля.")
        return distance

    def clean(self):
        cleaned = super().clean()
        if "duration_hours" in cleaned and "duration_minutes" in cleaned:
            # Длительность части необязательна: у пробежки без часов законно
            # знать только километры. Верхняя граница остаётся общей.
            error = clean_duration(cleaned, required=False)
            if error:
                self.add_error(*error)
        clean_cardio_machine(self, cleaned)
        return cleaned

    def save(self, commit=True):
        part = super().save(commit=False)
        part.workout = self.workout
        part.style = cardio_style_of(part.sport, self.cleaned_data)
        part.gear = chosen_gear(self)
        apply_cardio_machine(self.user, part, self.cleaned_data)
        # Ноль значит «не указывал»: у части это NULL, и тогда скорость с темпом
        # просто не считаются — врать про них хуже, чем промолчать.
        part.duration_min = self.cleaned_data.get("duration_min") or None
        if commit:
            part.save()
        return part


class FinishDurationForm(forms.Form):
    """Сколько на самом деле длилась забытая тренировка.

    Обычное завершение считает длительность от начала, и для тренировки,
    которую забыли завершить вечером, это были бы сутки. Поэтому у забытой
    (`Workout.is_stale`) завершение спрашивает время; дольше, чем прошло с
    начала, она длиться не могла.
    """

    duration_hours, duration_minutes = duration_fields()

    def __init__(self, *args, elapsed_min, **kwargs):
        super().__init__(*args, **kwargs)
        self.elapsed_min = elapsed_min

    def clean(self):
        cleaned = super().clean()
        if "duration_hours" in cleaned and "duration_minutes" in cleaned:
            error = clean_duration(cleaned, required=True)
            if error:
                self.add_error(*error)
            elif cleaned["duration_min"] > self.elapsed_min:
                self.add_error("duration_hours", "Тренировка идёт меньше — проверьте время.")
        return cleaned


class StrengthTimeForm(forms.Form):
    """Когда была силовая тренировка и сколько длилась.

    Два применения на одну форму: записать подготовленный черновик за прошедший
    день (тренировка из бумажной тетрадки) и поправить эти же значения у уже
    записанной. Разводит их вьюха, а не форма: набор полей и проверки совпадают
    ровно, и вторая копия разошлась бы с первой на первой же правке.

    Длительность обязательна: у такой тренировки время не шло, и вычислить её,
    как это делает WorkoutFinishView, попросту неоткуда.
    """

    date = date_field()
    time = time_field()
    duration_hours, duration_minutes = duration_fields()

    def __init__(self, *args, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        if instance is not None and instance.started_at is not None:
            # Правка: поля показывают то, что записано сейчас.
            started_at = timezone.localtime(instance.started_at)
            hours, minutes = divmod(instance.duration_min or 0, 60)
            self.initial = {
                "date": started_at.date(),
                "time": started_at.time(),
                "duration_hours": hours or None,
                "duration_minutes": minutes or None,
                **self.initial,
            }
        else:
            self.initial.setdefault("date", timezone.localdate())

    def clean(self):
        cleaned = super().clean()
        if "duration_hours" in cleaned and "duration_minutes" in cleaned:
            error = clean_duration(cleaned, required=True)
            if error:
                self.add_error(*error)
        if "date" in cleaned:
            started_at = services.combine_started_at(cleaned["date"], cleaned.get("time"))
            if started_at > timezone.now():
                self.add_error("date", "Дата не может быть в будущем.")
            else:
                cleaned["started_at"] = started_at
        return cleaned


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
        fields = ("name", "measurement", "muscle_group", "equipment")
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
        return chosen_muscle_group(self.data, self.facets().muscle_group_choices)

    def clean_equipment(self):
        """Снаряд — вторая ось справочника, правило у него то же."""
        return chosen_equipment(self.data, self.facets().equipment)

    def facets(self):
        """Обе оси одним запросом: два clean-метода спрашивают один и тот же список."""
        if not hasattr(self, "_facets"):
            self._facets = facets_for(self.user)
        return self._facets

    def clean_name(self):
        return self.cleaned_data["name"].strip()

    def save_for_user(self):
        """Вернуть видимое упражнение с таким именем или создать личное.

        Посреди тренировки ввод существующего названия означает «добавь его»,
        а не ошибку дубля — намеренное отличие от SportForm. Правило одно с
        импортом из таблицы, поэтому живёт в services: две реализации разошлись
        бы в том, что делать с совпавшим именем.
        """
        return services.exercise_for_name(
            self.user,
            self.cleaned_data["name"],
            measurement=self.cleaned_data["measurement"],
            muscle_group=self.cleaned_data["muscle_group"],
            equipment=self.cleaned_data["equipment"],
        )


class ExerciseCreateForm(ExerciseQuickForm):
    """«Создать» в справочнике: то же, что быстрое создание, но совпавшее имя — ошибка.

    В живом режиме ввод существующего названия значит «добавь его», а здесь
    человек заводит упражнение осознанно, и молча открыть чужое по смыслу —
    «Жим лёжа» вместо своего — было бы обманом. Администратор выбирает ещё и
    «Своё / Общее»: общее упражнение (owner=NULL) увидят все.
    """

    SCOPE_OWN = "own"
    SCOPE_GLOBAL = "global"

    scope = forms.ChoiceField(
        choices=[(SCOPE_OWN, "Своё"), (SCOPE_GLOBAL, "Общее")], required=False
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        # Поля нет вовсе — подменой запроса общее упражнение не создать.
        if not user.is_admin:
            del self.fields["scope"]

    @property
    def is_global(self):
        return self.cleaned_data.get("scope") == self.SCOPE_GLOBAL

    def clean(self):
        cleaned = super().clean()
        name = cleaned.get("name")
        # Владелец ставится до проверок модели (_post_clean после clean): иначе
        # уникальность имени сверялась бы так, будто упражнение общее.
        self.instance.owner = None if self.is_global else self.user
        if name:
            # Общее сверяется со всеми общими (их уникальность держит база), своё —
            # со всем, что человек видит: общий «Жим лёжа» дублировать незачем.
            if self.is_global:
                same = Exercise.objects.global_only()
            else:
                same = Exercise.objects.visible_to(self.user)
            if same.filter(name__iexact=name).exists():
                self.add_error("name", "Такое упражнение уже есть в справочнике.")
        return cleaned


class XlsxUploadForm(forms.Form):
    """Книга Excel — с историей или со справочником. Здесь — только размер и расширение.

    Содержимое разбирают excel_import и exercise_excel: форма не должна знать про
    листы и колонки, её дело — не пустить в разбор то, что заведомо не книга.
    На странице обмена две такие формы, поэтому у второй свой prefix — иначе у
    обоих полей был бы один id, и подпись одной открывала бы выбор файла другой.
    """

    file = forms.FileField(
        label="Файл .xlsx",
        error_messages={"required": "Выберите файл."},
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": ".xlsx," + excel.CONTENT_TYPE}
        ),
    )

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if upload.size > excel_import.MAX_UPLOAD_BYTES:
            limit = excel_import.MAX_UPLOAD_BYTES // (1024 * 1024)
            raise forms.ValidationError(
                f"Файл больше {limit} МБ. Разделите таблицу на части и загрузите по очереди."
            )
        if not upload.name.lower().endswith(".xlsx"):
            raise forms.ValidationError(
                "Нужен файл .xlsx — сохраните таблицу из Excel как «Книга Excel (.xlsx)»."
            )
        return upload


class BodyMeasurementForm(forms.Form):
    """Замер: параметр, значение, день.

    Параметр — из видимых пользователю (общие и свои): чужой id приходит
    ошибкой поля, а не утечкой. При правке параметр зафиксирован — его нет в
    полях вовсе, дослать его руками нельзя. Значение разбирает сервер: «82,5» и
    «82.5» одинаково.
    """

    metric = forms.ModelChoiceField(
        queryset=BodyMetric.objects.none(),
        empty_label=None,
        widget=forms.RadioSelect,
        error_messages={
            "required": "Выберите параметр.",
            "invalid_choice": "Такого параметра у вас нет.",
        },
    )
    value = forms.CharField(
        label="Значение",
        max_length=12,
        error_messages={"required": "Введите значение, например 82,5."},
        widget=forms.TextInput(
            attrs={"class": "form-control", "inputmode": "decimal", "autocomplete": "off"}
        ),
    )
    measured_on = date_field()

    def __init__(self, *args, user, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.instance = instance
        self.fields["measured_on"].label = "День"
        # Будущий день браузер не даст выбрать, а сервер всё равно проверит.
        self.fields["measured_on"].widget.attrs["max"] = timezone.localdate().isoformat()
        self.fields["value"].widget.attrs["autofocus"] = True
        if instance is None:
            self.fields["metric"].queryset = BodyMetric.objects.visible_to(user)
        else:
            del self.fields["metric"]

    def clean_value(self):
        try:
            return parse_measurement_value(self.cleaned_data["value"])
        except ValueError as error:
            raise forms.ValidationError(str(error)) from None

    def clean_measured_on(self):
        day = self.cleaned_data["measured_on"]
        if day > timezone.localdate():
            raise forms.ValidationError("Дата не может быть в будущем.")
        return day

    def clean(self):
        cleaned = super().clean()
        day = cleaned.get("measured_on")
        # Перенос замера на день, где у параметра уже есть замер, — ошибка поля:
        # при добавлении такой ввод — исправление дня, а при правке он молча
        # затёр бы другой замер.
        if self.instance is not None and day:
            taken = BodyMeasurement.objects.filter(
                user=self.user, metric=self.instance.metric, measured_on=day
            ).exclude(pk=self.instance.pk)
            if taken.exists():
                label = formats.date_format(day, "j E")
                self.add_error("measured_on", f"За {label} замер уже есть — поправьте его.")
        return cleaned


class BodyMetricForm(forms.Form):
    """Свой параметр тела: название и единица («Шея», «см»).

    Единица — только подпись: при смене прежние замеры не пересчитываются, о чём
    модалка и говорит. Дубль общего названия в другом регистре отклоняется —
    ограничение в базе его не поймает, владельцы разные (тот же довод, что у
    SportForm).
    """

    name = forms.CharField(
        label="Название",
        max_length=BODY_METRIC_NAME_MAX_LENGTH,
        error_messages={"required": "Введите название."},
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "напр. Шея", "autocomplete": "off"}
        ),
    )
    unit = forms.CharField(
        label="Единица",
        max_length=BODY_METRIC_UNIT_MAX_LENGTH,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "напр. см", "autocomplete": "off"}
        ),
    )

    def __init__(self, *args, user, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.instance = instance

    def clean_name(self):
        name = collapse_spaces(self.cleaned_data["name"])
        if not name:
            raise forms.ValidationError("Введите название.")
        duplicate = BodyMetric.objects.visible_to(self.user).filter(name__iexact=name)
        if self.instance is not None:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError("Такой параметр у вас уже есть.")
        return name

    def clean_unit(self):
        return collapse_spaces(self.cleaned_data["unit"])

    def save(self):
        metric = self.instance or BodyMetric(owner=self.user)
        metric.name = self.cleaned_data["name"]
        metric.unit = self.cleaned_data["unit"]
        metric.save()
        return metric


class MachineNameForm(forms.Form):
    """Новый производитель или модель в окне «Тренажёр» и переименование на
    «Моих тренажёрах»: одно поле названия, у администратора — ещё «Своё / Общее».

    Поля scope у остальных нет вовсе (приём ExerciseCreateForm): подменой
    запроса общую запись не завести.
    """

    SCOPE_OWN = "own"
    SCOPE_GLOBAL = "global"

    name = forms.CharField(error_messages={"required": "Введите название."})
    scope = forms.ChoiceField(
        choices=[(SCOPE_OWN, "Своё"), (SCOPE_GLOBAL, "Общее")], required=False
    )

    def __init__(self, *args, user, max_length, with_scope=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].max_length = max_length
        self.fields["name"].validators.append(MaxLengthValidator(max_length))
        if not (with_scope and user.is_admin):
            del self.fields["scope"]

    @property
    def is_global(self):
        return self.cleaned_data.get("scope") == self.SCOPE_GLOBAL

    def clean_name(self):
        return collapse_spaces(self.cleaned_data["name"])


class MachineCreateForm(MachineNameForm):
    """«Создать» на «Моих тренажёрах»: производитель или модель (kind).

    В отличие от окна «Тренажёр», совпавшее имя здесь ошибка (приём
    ExerciseCreateForm): человек пришёл завести запись, а не выбрать её. У
    модели ещё и производитель — из видимых, поэтому чужой своей записи не
    подставить. Общая модель — только у общего производителя.
    """

    brand = forms.ModelChoiceField(
        queryset=MachineBrand.objects.none(),
        error_messages={
            "required": "Выберите производителя.",
            "invalid_choice": "Выберите производителя из списка.",
        },
    )

    def __init__(self, *args, user, kind, **kwargs):
        self.user = user
        self.model = MachineBrand if kind == "brand" else MachineModel
        max_length = self.model._meta.get_field("name").max_length
        super().__init__(*args, user=user, max_length=max_length, **kwargs)
        if self.model is MachineBrand:
            del self.fields["brand"]
        else:
            self.fields["brand"].queryset = MachineBrand.objects.visible_to(user)

    def clean(self):
        cleaned = super().clean()
        name = cleaned.get("name")
        manager = self.model.objects
        same = manager.global_only() if self.is_global else manager.visible_to(self.user)
        if self.model is MachineModel:
            brand = cleaned.get("brand")
            if brand is None:
                return cleaned
            if self.is_global and not brand.is_global:
                self.add_error("name", "Общая модель бывает только у общего производителя.")
                return cleaned
            same = same.filter(brand=brand)
        if name and same.filter(name__iexact=name).exists():
            self.add_error("name", "Такое название уже есть.")
        return cleaned

    def save(self):
        fields = {
            "owner": None if self.is_global else self.user,
            "name": self.cleaned_data["name"],
        }
        if self.model is MachineModel:
            fields["brand"] = self.cleaned_data["brand"]
        return self.model.objects.create(**fields)


class CatalogRequestForm(forms.Form):
    """Заявка в общий справочник: комментарий автора необязателен."""

    comment = forms.CharField(
        required=False,
        max_length=CATALOG_REQUEST_TEXT_MAX_LENGTH,
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )

    def clean_comment(self):
        return self.cleaned_data["comment"].strip()


class CatalogRejectForm(forms.Form):
    """Отказ по заявке — только с причиной: её получит автор."""

    reason = forms.CharField(
        max_length=CATALOG_REQUEST_TEXT_MAX_LENGTH,
        error_messages={"required": "Укажите причину отказа."},
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
    )
