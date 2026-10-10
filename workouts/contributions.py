"""Перевод своей записи справочника в общие: упражнения, производители, модели.

Одна точка на оба пути: «Сделать общим» администратора и принятие заявки
пользователя. Запись не копируется, а меняет владельца на NULL — это та же
строка, поэтому история, настройки и тренажёры автора остаются на ней, а дубля
в его справочнике не появляется. Общая запись с тем же названием мешает
переводу: две одинаковые общие запрещены констрейнтом, а сливать историю в
существующую никто не просил — такую заявку отклоняют.
"""

from django.db import IntegrityError, transaction

from workouts.models import Exercise, MachineBrand, MachineModel, facets_from_pairs, normalize_facet


class DuplicateError(ValueError):
    """Общая запись с таким названием уже есть; existing — она (для ссылки)."""

    def __init__(self, message, existing=None):
        super().__init__(message)
        self.existing = existing


def kind_of(item):
    """exercise | brand | model — тот же ключ, что в адресах и заявках."""
    return {Exercise: "exercise", MachineBrand: "brand", MachineModel: "model"}[type(item)]


def global_twin(item):
    """Общая запись с тем же названием (без учёта регистра) или None."""
    same = type(item).objects.global_only().filter(name__iexact=item.name).exclude(pk=item.pk)
    if isinstance(item, MachineModel):
        same = same.filter(brand_id=item.brand_id)
    return same.first()


def blocker(item):
    """Что мешает сделать запись общей — DuplicateError или None.

    Модель своего производителя уходит в общие вместе с ним (общая модель
    бывает только у общего производителя), поэтому проверяется и он.
    """
    if isinstance(item, MachineModel) and not item.brand.is_global:
        twin = global_twin(item.brand)
        if twin is not None:
            return DuplicateError(f"Общий производитель «{twin.name}» уже есть.", twin)
    twin = global_twin(item)
    if twin is None:
        return None
    if isinstance(item, Exercise):
        return DuplicateError(f"Общее упражнение «{twin.name}» уже есть.", twin)
    if isinstance(item, MachineBrand):
        return DuplicateError(f"Общий производитель «{twin.name}» уже есть.", twin)
    return DuplicateError(f"Общая модель «{twin.name}» у «{item.brand.name}» уже есть.", twin)


def _publish(item, contributor):
    item.owner = None
    item.contributed_by = contributor
    item.save(update_fields=["owner", "contributed_by"])


def make_global(item, *, contributor=None):
    """Сделать свою запись общей; contributor — автор принятой заявки.

    Группа и снаряд упражнения приводятся к написанию, уже принятому у общих:
    иначе в общем справочнике появились бы «Грудь» и «грудь». Возвращает
    список записей, ставших общими (у модели своего производителя — двух).
    """
    problem = blocker(item)
    if problem is not None:
        raise problem
    published = []
    try:
        with transaction.atomic():
            if isinstance(item, MachineModel) and not item.brand.is_global:
                _publish(item.brand, contributor)
                published.append(item.brand)
            if isinstance(item, Exercise):
                pairs = (
                    Exercise.objects.global_only()
                    .order_by("muscle_group", "equipment")
                    .values_list("muscle_group", "equipment")
                    .distinct()
                )
                facets = facets_from_pairs(pairs)
                item.muscle_group = normalize_facet(item.muscle_group, facets.muscle_groups)
                item.equipment = normalize_facet(item.equipment, facets.equipment)
                item.save(update_fields=["muscle_group", "equipment"])
            _publish(item, contributor)
            published.append(item)
    except IntegrityError as error:
        # Гонка: общую с тем же именем завели между проверкой и записью.
        raise DuplicateError("Такое название в общем справочнике уже есть.") from error
    return published
