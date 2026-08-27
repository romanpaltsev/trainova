import factory

from accounts.models import User

PASSWORD = "correct-horse-battery"


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        django_get_or_create = ("email",)

    email = factory.Sequence(lambda n: f"user{n}@example.com")
    # Password хеширует пароль сразу при создании — set_password в postgeneration
    # не сохранился бы в БД и сессия «разлогинивалась» бы на следующем запросе.
    password = factory.django.Password(PASSWORD)
