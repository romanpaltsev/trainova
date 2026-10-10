"""Заявки в общий справочник: пользователь предлагает своё, администратор решает.

Изоляция: заявка — только на своё, «Мои заявки» — только свои, отозвать — только
свою ожидающую. Решение уходит автору письмом, новая заявка — админам.
"""

import pytest
from django.core import mail
from django.urls import reverse

from accounts.deletion import delete_user_data
from workouts import contributions
from workouts.models import CatalogRequest, Exercise
from workouts.tests.budgets import SIDEBAR_QUERIES
from workouts.tests.factories import (
    ExerciseFactory,
    MachineBrandFactory,
    MachineModelFactory,
    StrengthSetFactory,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(db):
    from accounts.tests.factories import UserFactory

    return UserFactory(is_staff=True, email="boss@example.com")


def propose(client, kind, item, comment=""):
    return client.post(reverse("catalog_propose", args=[kind, item.pk]), {"comment": comment})


def decide(client, req, action, reason=""):
    return client.post(
        reverse("admin_request_detail", args=[req.pk]), {"action": action, "reason": reason}
    )


def test_user_proposes_exercise_and_admins_get_mail(client, user, admin):
    exercise = ExerciseFactory(name="Жим Смита", owner=user)
    client.force_login(user)

    page = client.get(reverse("exercise_detail", args=[exercise.pk])).content.decode()
    assert "Предложить в общий справочник" in page
    response = propose(client, "exercise", exercise, "  Есть в каждом зале ")

    assert response.headers["HX-Refresh"] == "true"
    req = CatalogRequest.objects.get()
    assert (req.user, req.exercise, req.comment, req.status) == (
        user,
        exercise,
        "Есть в каждом зале",
        "pending",
    )
    assert mail.outbox[-1].to == [admin.email]
    assert "Жим Смита" in mail.outbox[-1].subject
    page = client.get(reverse("exercise_detail", args=[exercise.pk])).content.decode()
    assert "на рассмотрении" in page


def test_second_pending_request_is_error(client, user):
    exercise = ExerciseFactory(name="Жим Смита", owner=user)
    client.force_login(user)
    propose(client, "exercise", exercise)

    html = propose(client, "exercise", exercise).content.decode()

    assert "Заявка уже на рассмотрении." in html
    assert CatalogRequest.objects.count() == 1


def test_propose_isolation(client, user, other_user):
    theirs = ExerciseFactory(name="Чужое", owner=other_user)
    shared = ExerciseFactory(name="Общее")
    their_brand = MachineBrandFactory(name="Чужой", owner=other_user)
    client.force_login(user)

    for kind, item in [("exercise", theirs), ("exercise", shared), ("brand", their_brand)]:
        assert propose(client, kind, item).status_code == 404
    assert client.get(reverse("catalog_propose", args=["nonsense", theirs.pk])).status_code == 404
    assert not CatalogRequest.objects.exists()


def test_admin_has_no_propose(client, admin):
    exercise = ExerciseFactory(name="Своё админа", owner=admin)
    client.force_login(admin)

    assert propose(client, "exercise", exercise).status_code == 404


def test_withdraw_only_own_pending(client, user, other_user):
    mine = contributions.propose(user, ExerciseFactory(name="Моё", owner=user))
    theirs = contributions.propose(other_user, ExerciseFactory(name="Их", owner=other_user))
    client.force_login(user)

    assert client.post(reverse("catalog_withdraw", args=[theirs.pk])).status_code == 404
    client.post(reverse("catalog_withdraw", args=[mine.pk]))

    assert list(CatalogRequest.objects.all()) == [theirs]


def test_my_requests_lists_only_own(client, user, other_user):
    contributions.propose(user, ExerciseFactory(name="Моё упражнение", owner=user))
    contributions.propose(other_user, ExerciseFactory(name="Их упражнение", owner=other_user))
    client.force_login(user)

    html = client.get(reverse("my_requests")).content.decode()

    assert "Моё упражнение" in html
    assert "Их упражнение" not in html


def test_admin_accepts(client, user, admin):
    exercise = ExerciseFactory(name="Жим Смита", owner=user)
    StrengthSetFactory(exercise=exercise, workout__user=user)
    req = contributions.propose(user, exercise)
    client.force_login(admin)

    assert (
        "Принять в общий справочник"
        in client.get(reverse("admin_request_detail", args=[req.pk])).content.decode()
    )
    decide(client, req, "accept")

    exercise.refresh_from_db()
    req.refresh_from_db()
    assert exercise.is_global and exercise.contributed_by == user
    assert req.status == "accepted" and req.decided_at
    assert mail.outbox[-1].to == [user.email]
    assert "принята" in mail.outbox[-1].subject


def test_admin_rejects_only_with_reason(client, user, admin):
    req = contributions.propose(user, ExerciseFactory(name="Жим Смита", owner=user))
    client.force_login(admin)

    html = decide(client, req, "reject").content.decode()
    assert "Укажите причину отказа." in html
    req.refresh_from_db()
    assert req.is_pending

    decide(client, req, "reject", "Это «Жим в Смите», он уже есть")
    req.refresh_from_db()
    assert req.status == "rejected"
    assert "уже есть" in mail.outbox[-1].body
    assert not Exercise.objects.get(pk=req.exercise_id).is_global

    client.force_login(user)
    page = client.get(reverse("exercise_detail", args=[req.exercise_id])).content.decode()
    assert "Заявку отклонили: Это «Жим в Смите», он уже есть" in page
    assert "Отправить снова" in page


def test_duplicate_blocks_accept(client, user, admin):
    req = contributions.propose(user, ExerciseFactory(name="жим лёжа", owner=user))
    ExerciseFactory(name="Жим лёжа")
    client.force_login(admin)

    html = client.get(reverse("admin_request_detail", args=[req.pk])).content.decode()
    assert "Общее упражнение «Жим лёжа» уже есть." in html
    assert "Принять в общий справочник" not in html

    decide(client, req, "accept")
    req.refresh_from_db()
    assert req.is_pending


def test_admin_pages_are_404_for_regular_user(client, user):
    req = contributions.propose(user, ExerciseFactory(name="Моё", owner=user))
    client.force_login(user)

    assert client.get(reverse("admin_requests")).status_code == 404
    assert decide(client, req, "accept").status_code == 404


def test_machine_model_request_takes_brand_and_closes_its_request(client, user, admin):
    brand = MachineBrandFactory(name="Kettler", owner=user)
    model = MachineModelFactory(brand=brand, name="Axos", owner=user)
    brand_req = contributions.propose(user, brand)
    model_req = contributions.propose(user, model)
    client.force_login(admin)

    decide(client, model_req, "accept")

    brand.refresh_from_db()
    brand_req.refresh_from_db()
    assert brand.is_global and brand.contributed_by == user
    assert brand_req.status == "accepted"


def test_my_machines_shows_propose_and_status(client, user):
    brand = MachineBrandFactory(name="Kettler", owner=user)
    model = MachineModelFactory(brand=brand, name="Axos", owner=user)
    contributions.propose(user, model)
    client.force_login(user)

    html = client.get(reverse("my_machines")).content.decode()

    assert reverse("catalog_propose", args=["brand", brand.pk]) in html
    assert reverse("catalog_propose", args=["model", model.pk]) not in html
    assert "заявка на рассмотрении" in html


def test_admin_counter_counts_requests(client, user, admin):
    contributions.propose(user, ExerciseFactory(name="Моё", owner=user))
    client.force_login(admin)

    html = client.get(reverse("dashboard")).content.decode()

    assert "ждут решения: 1" in html


def test_requests_list_budget_does_not_grow(client, user, admin, django_assert_max_num_queries):
    for number in range(5):
        contributions.propose(user, ExerciseFactory(name=f"Моё {number}", owner=user))
        model = MachineModelFactory(
            brand=MachineBrandFactory(name=f"Бренд {number}", owner=user), name="М", owner=user
        )
        contributions.propose(user, model)
    client.force_login(admin)

    # Пара SAVEPOINT/RELEASE, сессия, пользователь, заявки.
    with django_assert_max_num_queries(5 + SIDEBAR_QUERIES):
        client.get(reverse("admin_requests"), {"status": "all"})


def test_account_deletion_drops_requests_keeps_accepted(user):
    accepted = ExerciseFactory(name="Принятое", owner=user)
    contributions.accept(contributions.propose(user, accepted))
    contributions.propose(user, ExerciseFactory(name="Ждёт", owner=user))

    delete_user_data(user)

    assert not CatalogRequest.objects.exists()
    accepted.refresh_from_db()
    assert accepted.is_global and accepted.contributed_by is None
