"""Формы записи тренировок и личных справочников."""

from django import forms
from django.utils import timezone

from workouts import excel, excel_import, services
from workouts.models import (
    LOCATION_NAME_MAX_LENGTH,
    CardioPart,
    Exercise,
    Location,
    Sport,
    Workout,
    chosen_equipment,
    chosen_muscle_group,
    collapse_spaces,
    facets_for,
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
            for name in ("date", "time", "avg_heart_rate"):
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
            "avg_heart_rate": cardio.avg_heart_rate if cardio else None,
            "note": workout.note,
        }

    def clean(self):
        cleaned = super().clean()
        # Ключей нет, когда поле уже дало свою ошибку: пересчитывать по обрывку
        # значило бы повесить вторую, противоречащую первой.
        if "duration_hours" in cleaned and "duration_minutes" in cleaned:
            error = clean_duration(cleaned, required=not self.planned)
            if error:
                self.add_error(*error)
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
        if distance is None:
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
            # Пульс есть только у состоявшейся тренировки: у плана поля нет,
            # а на записи черновика оно придёт из формы как обычно.
            part.avg_heart_rate = self.cleaned_data.get("avg_heart_rate")
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

    class Meta:
        model = CardioPart
        fields = ("sport", "distance_km", "avg_heart_rate")
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
            "distance_km": forms.NumberInput(
                attrs={"class": "form-control", "inputmode": "decimal", "step": "0.01"}
            ),
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
        if self.instance.pk:
            hours, minutes = divmod(self.instance.duration_min or 0, 60)
            self.initial.setdefault("duration_hours", hours or None)
            self.initial.setdefault("duration_minutes", minutes or None)

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
        return cleaned

    def save(self, commit=True):
        part = super().save(commit=False)
        part.workout = self.workout
        # Ноль значит «не указывал»: у части это NULL, и тогда скорость с темпом
        # просто не считаются — врать про них хуже, чем промолчать.
        part.duration_min = self.cleaned_data.get("duration_min") or None
        if commit:
            part.save()
        return part


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
        return chosen_muscle_group(self.data, self.facets().muscle_groups)

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
