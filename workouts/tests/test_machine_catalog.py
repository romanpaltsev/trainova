"""«Мои тренажёры»: свой справочник производителей и моделей.

Изоляция: свои записи видит и правит только владелец, общие — администратор.
Удаление держит RESTRICT: используемую запись не удалить, а удаление аккаунта
со своим справочником проходит.
"""

import importlib
import re
from datetime import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.deletion import delete_user_data
from workouts.models import ExerciseMachine, MachineBrand, MachineModel, Sport
from workouts.tests.budgets import SIDEBAR_QUERIES
from workouts.tests.factories import (
    CardioPartFactory,
    ExerciseFactory,
    ExerciseMachineFactory,
    LocationFactory,
    MachineBrandFactory,
    MachineModelFactory,
    SportFactory,
    WorkoutFactory,
)

pytestmark = pytest.mark.django_db

URL = reverse("my_machines")


@pytest.fixture
def admin_user(user):
    user.is_staff = True
    user.save(update_fields=["is_staff"])
    return user


def rename(client, kind, item, name):
    return client.post(reverse("machine_rename", args=[kind, item.pk]), {"name": name})


def delete(client, kind, item):
    return client.post(reverse("machine_delete", args=[kind, item.pk]))


def test_page_lists_own_records_only(client, user, other_user):
    kettler = MachineBrandFactory(name="Kettler", owner=user)
    MachineModelFactory(brand=kettler, name="Axos", owner=user)
    MachineModelFactory(brand=MachineBrandFactory(name="Technogym"), name="Мой Pure", owner=user)
    MachineBrandFactory(name="Чужой бренд", owner=other_user)
    client.force_login(user)

    html = client.get(URL).content.decode()

    assert "Kettler" in html
    assert "Axos" in html
    assert "Мой Pure" in html
    assert "Чужой бренд" not in html
    assert "Общие — видят все" not in html


def test_admin_sees_shared_section(client, admin_user):
    MachineBrandFactory(name="Technogym")
    client.force_login(admin_user)

    html = client.get(URL).content.decode()

    assert "Общие — видят все" in html
    assert "Technogym" in html


def test_rename_own_and_taken_name_is_error(client, user):
    kettler = MachineBrandFactory(name="Kettler", owner=user)
    MachineBrandFactory(name="Matrix", owner=user)
    client.force_login(user)

    response = rename(client, "brand", kettler, "matrix")
    assert "Такое название уже есть." in response.content.decode()

    response = rename(client, "brand", kettler, "  Kettler   Sport ")
    assert response.headers["HX-Refresh"] == "true"
    kettler.refresh_from_db()
    assert kettler.name == "Kettler Sport"


def test_others_and_shared_records_are_404_for_regular_user(client, user, other_user):
    theirs = MachineBrandFactory(name="Чужой", owner=other_user)
    shared = MachineBrandFactory(name="Technogym")
    shared_model = MachineModelFactory(brand=shared, name="Pure")
    client.force_login(user)

    for kind, item in [("brand", theirs), ("brand", shared), ("model", shared_model)]:
        assert rename(client, kind, item, "Взлом").status_code == 404
        assert delete(client, kind, item).status_code == 404
    assert client.get(reverse("machine_delete", args=["nonsense", theirs.pk])).status_code == 404
    assert MachineBrand.objects.count() == 2


def test_admin_renames_shared(client, admin_user):
    shared = MachineBrandFactory(name="Technogym")
    client.force_login(admin_user)

    rename(client, "brand", shared, "TechnoGym")

    shared.refresh_from_db()
    assert shared.name == "TechnoGym"


def test_delete_unused_brand_with_its_models(client, user):
    kettler = MachineBrandFactory(name="Kettler", owner=user)
    MachineModelFactory(brand=kettler, name="Axos", owner=user)
    client.force_login(user)

    response = delete(client, "brand", kettler)

    assert response.status_code == 302
    assert not MachineBrand.objects.exists()
    assert not MachineModel.objects.exists()


def test_used_records_are_not_deleted(client, user):
    machine = ExerciseMachineFactory(
        user=user,
        brand=MachineBrandFactory(name="Kettler", owner=user),
        model=MachineModelFactory(
            brand=MachineBrandFactory(name="Kettler", owner=user), name="Axos", owner=user
        ),
    )
    client.force_login(user)

    html = client.get(reverse("machine_delete", args=["model", machine.model_id])).content
    assert "указан у тренажёров" in html.decode()

    delete(client, "model", machine.model)
    delete(client, "brand", machine.brand)

    assert MachineModel.objects.filter(pk=machine.model_id).exists()
    assert MachineBrand.objects.filter(pk=machine.brand_id).exists()


def test_shared_brand_with_others_models_is_not_deleted(client, admin_user, other_user):
    shared = MachineBrandFactory(name="Technogym")
    MachineModelFactory(brand=shared, name="Их модель", owner=other_user)
    client.force_login(admin_user)

    delete(client, "brand", shared)

    assert MachineBrand.objects.filter(pk=shared.pk).exists()


def test_admin_deletes_unused_shared(client, admin_user):
    shared = MachineBrandFactory(name="Technogym")
    MachineModelFactory(brand=shared, name="Pure")
    client.force_login(admin_user)

    delete(client, "brand", shared)

    assert not MachineBrand.objects.exists()
    assert not MachineModel.objects.exists()


def test_account_deletion_with_own_catalog_and_machines(user):
    """RESTRICT пропускает строки того же каскада — PROTECT здесь упал бы."""
    kettler = MachineBrandFactory(name="Kettler", owner=user)
    model = MachineModelFactory(brand=kettler, name="Axos", owner=user)
    ExerciseMachineFactory(user=user, brand=kettler, model=model)
    own_model_of_shared = MachineModelFactory(
        brand=MachineBrandFactory(name="Technogym"), name="Своя", owner=user
    )
    ExerciseMachineFactory(
        user=user,
        location=LocationFactory(owner=user, name="Второй зал"),
        brand=own_model_of_shared.brand,
        model=own_model_of_shared,
    )

    delete_user_data(user)

    assert not ExerciseMachine.objects.exists()
    assert list(MachineBrand.objects.values_list("name", flat=True)) == ["Technogym"]
    assert not MachineModel.objects.exists()


def test_page_query_budget_does_not_grow(client, admin_user, django_assert_max_num_queries):
    """Счётчики использования — аннотацией, модели — prefetch: число запросов
    не зависит от числа записей."""
    for number in range(5):
        brand = MachineBrandFactory(name=f"Свой {number}", owner=admin_user)
        MachineModelFactory(brand=brand, name="Модель", owner=admin_user)
        shared = MachineBrandFactory(name=f"Общий {number}")
        MachineModelFactory(brand=shared, name="Модель")
    client.force_login(admin_user)

    # Пара SAVEPOINT/RELEASE транзакции запроса, сессия, пользователь, свои модели
    # общих производителей, свои производители и их модели, общие производители
    # и их модели.
    with django_assert_max_num_queries(9 + SIDEBAR_QUERIES):
        client.get(URL)


def test_sidebar_and_profile_link_to_page(client, user):
    client.force_login(user)

    html = client.get(reverse("profile")).content.decode()

    assert html.count(f'href="{URL}"') == 2  # панель и строка профиля


def test_migration_turns_text_into_catalog(user, other_user):
    """0071: текст становится записями — общий производитель находится по имени,
    незнакомый заводится своим у владельца строки, пустой — «Без производителя»."""
    migration = importlib.import_module("workouts.migrations.0071_machine_text_to_catalog")
    shared = MachineBrandFactory(name="Technogym")

    class Row:
        def __init__(self, user_id, brand, model):
            self.user_id, self.brand, self.model = user_id, brand, model
            self.saved = None

        def save(self, update_fields):
            self.saved = (self.brand_ref, self.model_ref)

    rows = [
        Row(user.pk, "technogym", "Pure"),
        Row(user.pk, "Kettler", ""),
        Row(other_user.pk, "", "Axos"),
    ]

    class Manager:
        def all(self):
            return rows

    class OldMachine:
        objects = Manager()

    class Apps:
        def get_model(self, app_label, name):
            return {
                "ExerciseMachine": OldMachine,
                "MachineBrand": MachineBrand,
                "MachineModel": MachineModel,
            }[name]

    migration.text_to_catalog(Apps(), None)

    assert rows[0].saved[0] == shared
    assert rows[0].saved[1].name == "Pure"
    assert rows[0].saved[1].owner == user
    assert (rows[1].saved[0].name, rows[1].saved[0].owner, rows[1].saved[1]) == (
        "Kettler",
        user,
        None,
    )
    assert rows[2].saved[0].name == migration.NO_BRAND
    assert rows[2].saved[0].owner == other_user
    assert rows[2].saved[1].name == "Axos"


def create(client, kind, **data):
    return client.post(
        reverse("machine_create", args=[kind]), {f"new-{key}": value for key, value in data.items()}
    )


def test_create_own_brand_and_model(client, user):
    client.force_login(user)

    response = create(client, "brand", name="  Kettler   Sport ")
    assert response.headers["HX-Refresh"] == "true"
    brand = MachineBrand.objects.get()
    assert (brand.name, brand.owner) == ("Kettler Sport", user)

    create(client, "model", brand=brand.pk, name="Axos")
    model = MachineModel.objects.get()
    assert (model.brand, model.name, model.owner) == (brand, "Axos", user)


def test_create_own_model_of_shared_brand(client, user):
    shared = MachineBrandFactory(name="Technogym")
    client.force_login(user)

    create(client, "model", brand=shared.pk, name="Selection 900", scope="global")

    model = MachineModel.objects.get()
    assert (model.brand, model.owner) == (shared, user)  # scope у не-админа нет


def test_create_taken_name_is_error(client, user):
    shared = MachineBrandFactory(name="Technogym")
    MachineModelFactory(brand=shared, name="Pure")
    client.force_login(user)

    for kind, data in [
        ("brand", {"name": "technogym"}),
        ("model", {"brand": shared.pk, "name": "PURE"}),
    ]:
        response = create(client, kind, **data)
        assert "Такое название уже есть." in response.content.decode()
    assert MachineBrand.objects.count() == 1
    assert MachineModel.objects.count() == 1


def test_create_model_needs_visible_brand(client, user, other_user):
    theirs = MachineBrandFactory(name="Чужой", owner=other_user)
    MachineBrandFactory(name="Kettler", owner=user)
    client.force_login(user)

    html = create(client, "model", brand=theirs.pk, name="Взлом").content.decode()
    assert "Выберите производителя из списка." in html
    assert "Чужой" not in client.get(reverse("machine_create", args=["model"])).content.decode()
    assert not MachineModel.objects.exists()
    assert client.get(reverse("machine_create", args=["nonsense"])).status_code == 404


def test_admin_creates_shared_and_shared_model_needs_shared_brand(client, admin_user):
    client.force_login(admin_user)

    create(client, "brand", name="Technogym", scope="global")
    shared = MachineBrand.objects.get(name="Technogym")
    assert shared.is_global
    create(client, "model", brand=shared.pk, name="Pure", scope="global")
    assert MachineModel.objects.get(name="Pure").is_global

    own = MachineBrandFactory(name="Свой", owner=admin_user)
    html = create(client, "model", brand=own.pk, name="Общая", scope="global").content.decode()
    assert "Общая модель бывает только у общего производителя." in html
    assert not MachineModel.objects.filter(name="Общая").exists()


def test_page_has_create_buttons(client, user):
    brand = MachineBrandFactory(name="Kettler", owner=user)
    client.force_login(user)

    html = client.get(URL).content.decode()

    assert reverse("machine_create", args=["brand"]) in html
    assert f"{reverse('machine_create', args=['model'])}?brand={brand.pk}" in html


# ---------- Окно записи: где стоит тренажёр ----------


def detail(client, kind, item):
    return client.get(reverse("machine_detail", args=[kind, item.pk]))


def cardio_part(user, sport, brand, model, *, started_at):
    """Часть кардио с тренажёром; started_at=None — часть плана (черновика)."""
    workout = WorkoutFactory(
        user=user,
        sport=sport,
        started_at=started_at,
        duration_min=None if started_at is None else 30,
    )
    return CardioPartFactory(workout=workout, brand=brand, model=model)


@pytest.fixture
def hammer(user):
    """Свой производитель с моделью: жим ногами в двух залах, разгибания без модели."""
    brand = MachineBrandFactory(name="Hammer Strength", owner=user)
    model = MachineModelFactory(brand=brand, name="Plate-Loaded Leg Press", owner=user)
    press = ExerciseFactory(name="Жим ногами")
    extension = ExerciseFactory(name="Разгибание ног")
    gym = LocationFactory(owner=user, name="СпортЛайф")
    home = LocationFactory(owner=user, name="Дом")
    ExerciseMachineFactory(user=user, exercise=press, location=gym, brand=brand, model=model)
    ExerciseMachineFactory(user=user, exercise=press, location=home, brand=brand, model=model)
    ExerciseMachineFactory(user=user, exercise=extension, location=gym, brand=brand, model=None)
    return brand, model, press, extension


def test_rows_open_window_and_rename_lives_in_it(client, user, hammer):
    brand, model, _, _ = hammer
    client.force_login(user)

    page = client.get(URL).content.decode()
    for kind, item in [("brand", brand), ("model", model)]:
        assert reverse("machine_detail", args=[kind, item.pk]) in page
        assert reverse("machine_rename", args=[kind, item.pk]) not in page

    window = detail(client, "brand", brand).content.decode()
    assert reverse("machine_rename", args=["brand", brand.pk]) in window

    # «Отмена» переименования возвращает в окно записи, а не закрывает всё.
    rename_modal = client.get(reverse("machine_rename", args=["brand", brand.pk]))
    assert reverse("machine_detail", args=["brand", brand.pk]) in rename_modal.content.decode()


def test_brand_window_lists_exercises_with_place_and_model(client, user, hammer):
    brand, _, press, extension = hammer
    client.force_login(user)

    html = detail(client, "brand", brand).content.decode()

    assert "Производитель" in html
    # Строка — упражнение × место: жим в двух залах — две строки, как в подписи.
    assert html.count(f'href="{reverse("exercise_detail", args=[press.pk])}"') == 2
    assert f'href="{reverse("exercise_detail", args=[extension.pk])}"' in html
    assert "Дом · Plate-Loaded Leg Press" in html
    assert "СпортЛайф · Plate-Loaded Leg Press" in html
    assert "СпортЛайф · модель не указана" in html
    assert html.index("Жим ногами") < html.index("Разгибание ног")
    assert "у 3 упражнений" in client.get(URL).content.decode()


def test_model_window_lists_only_its_rows(client, user, hammer):
    _, model, press, extension = hammer
    other_model = MachineModelFactory(brand=model.brand, name="ISO Row", owner=user)
    row = ExerciseFactory(name="Тяга в тренажёре")
    ExerciseMachineFactory(user=user, exercise=row, brand=model.brand, model=other_model)
    client.force_login(user)

    html = detail(client, "model", model).content.decode()

    assert "Модель · Hammer Strength" in html
    assert reverse("exercise_detail", args=[press.pk]) in html
    assert "Тяга в тренажёре" not in html
    assert "Разгибание ног" not in html
    assert "модель не указана" not in html
    assert "СпортЛайф" in html


def test_window_shows_cardio_by_sport_including_plans(client, user):
    brand = MachineBrandFactory(name="Мой Technogym", owner=user)
    model = MachineModelFactory(brand=brand, name="Run 1000", owner=user)
    run = SportFactory(name="Бег", category=Sport.Category.CARDIO)
    walk = SportFactory(name="Ходьба", category=Sport.Category.CARDIO)
    moscow = timezone.get_current_timezone()
    cardio_part(user, run, brand, model, started_at=datetime(2026, 9, 1, 10, 0, tzinfo=moscow))
    cardio_part(user, run, brand, model, started_at=datetime(2026, 10, 12, 19, 0, tzinfo=moscow))
    cardio_part(user, run, brand, model, started_at=None)
    cardio_part(user, walk, brand, model, started_at=None)
    client.force_login(user)

    html = detail(client, "model", model).content.decode()

    assert "Ни у одного упражнения" in html
    assert "3 раза · последний 12 окт" in html
    assert "1 раз · в плане" in html
    assert html.index("Бег") < html.index("Ходьба")  # свежие первыми, только планы — в конце
    # Планы с тренажёром держат запись так же, как записанные: сумма сходится с подписью.
    assert "в 4 кардио" in client.get(URL).content.decode()


def test_unused_record_window_says_where_to_pick(client, user):
    brand = MachineBrandFactory(name="Kettler", owner=user)
    model = MachineModelFactory(brand=brand, name="Axos", owner=user)
    client.force_login(user)

    brand_html = detail(client, "brand", brand).content.decode()
    model_html = detail(client, "model", model).content.decode()

    assert "Пока нигде не указан." in brand_html
    assert "Пока нигде не указана." in model_html
    assert "на странице упражнения" in brand_html
    assert "Кардио" not in brand_html


def test_window_isolation(client, user, other_user):
    theirs = MachineBrandFactory(name="Чужой", owner=other_user)
    shared = MachineBrandFactory(name="Technogym")
    shared_model = MachineModelFactory(brand=shared, name="Pure")
    mine = MachineModelFactory(brand=shared, name="Мой Pure", owner=user)
    client.force_login(user)

    assert detail(client, "brand", theirs).status_code == 404
    assert detail(client, "brand", shared).status_code == 404
    assert detail(client, "model", shared_model).status_code == 404
    assert detail(client, "model", mine).status_code == 200
    assert client.get(reverse("machine_detail", args=["nonsense", mine.pk])).status_code == 404

    client.logout()
    response = detail(client, "model", mine)
    assert response.status_code == 302
    assert response.url.startswith(reverse("account_login"))


def test_admin_sees_others_of_shared_record_only_as_number(client, admin_user, other_user):
    shared = MachineBrandFactory(name="Technogym")
    model = MachineModelFactory(brand=shared, name="Pure")
    ExerciseMachineFactory(
        user=admin_user,
        exercise=ExerciseFactory(name="Жим ногами"),
        location=LocationFactory(owner=admin_user, name="Мой зал"),
        brand=shared,
        model=model,
    )
    ExerciseMachineFactory(
        user=other_user,
        exercise=ExerciseFactory(name="Чужое упражнение", owner=other_user),
        location=LocationFactory(owner=other_user, name="Чужой зал"),
        brand=shared,
        model=model,
    )
    CardioPartFactory(workout__user=other_user, brand=shared, model=model)
    own = MachineBrandFactory(name="Свой", owner=admin_user)
    ExerciseMachineFactory(user=admin_user, brand=own, model=None)
    client.force_login(admin_user)

    html = detail(client, "brand", shared).content.decode()

    assert "Жим ногами" in html
    assert "Мой зал" in html
    assert "Чужое упражнение" not in html
    assert "Чужой зал" not in html
    assert "У других участников: у 1 упражнения · в 1 кардио" in html
    assert "У других участников" not in detail(client, "brand", own).content.decode()


@pytest.mark.parametrize("count", [1, 5])
def test_window_query_budget_does_not_grow(client, user, count, django_assert_max_num_queries):
    brand = MachineBrandFactory(name="Kettler", owner=user)
    model = MachineModelFactory(brand=brand, name="Axos", owner=user)
    for number in range(count):
        ExerciseMachineFactory(
            user=user,
            exercise=ExerciseFactory(name=f"Упражнение {number}"),
            location=LocationFactory(owner=user, name=f"Зал {number}"),
            brand=brand,
            model=model,
        )
        sport = SportFactory(name=f"Кардио {number}", category=Sport.Category.CARDIO)
        cardio_part(user, sport, brand, model, started_at=timezone.now())
    client.force_login(user)

    # Пара SAVEPOINT/RELEASE транзакции запроса, сессия, пользователь, запись со
    # счётчиками, свои тренажёры упражнений, кардио по видам спорта.
    with django_assert_max_num_queries(7):
        html = detail(client, "brand", brand).content.decode()

    assert html.count("Упражнение ") == count
    assert html.count("Кардио ") == count


def test_window_title_keeps_pencil_with_last_word(client, user):
    """Карандаш заголовка склеен с последним словом, а длинное слово без
    пробелов — нет: ему нужно переноситься."""
    brand = MachineBrandFactory(name="Hammer Strength", owner=user)
    model = MachineModelFactory(brand=brand, name="Супермегатренажёрдляжиманогами", owner=user)
    client.force_login(user)

    brand_html = detail(client, "brand", brand).content.decode()
    model_html = detail(client, "model", model).content.decode()

    # Перевод строки перед <svg> — от инклуда карандаша; внутри text-nowrap
    # он не даёт переноса.
    assert re.search(r'Hammer <span class="text-nowrap">Strength\s*<svg', brand_html)
    assert re.search(r'Супермегатренажёрдляжиманогами<span class="text-nowrap">\s*<svg', model_html)


def test_shared_record_used_only_by_others_is_not_called_unused(client, admin_user, other_user):
    shared = MachineBrandFactory(name="Technogym")
    ExerciseMachineFactory(user=other_user, brand=shared, model=None)
    client.force_login(admin_user)

    html = detail(client, "brand", shared).content.decode()

    assert "Ни у одного упражнения." in html
    assert "Пока нигде не" not in html
    assert "У других участников: у 1 упражнения" in html
