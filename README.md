# Дневник тренировок

Веб-приложение для записи силовых и кардио-тренировок (зал, велосипед, бег, лыжи).
Рабочее кодовое имя репозитория — trainova; в интерфейсе продукт называется
«Дневник тренировок».

Стек: Python + Django, шаблоны Django + HTMX + Alpine.js, Bootstrap 5, PostgreSQL,
django-allauth. Зависимости — uv, окружение разработки — Docker Compose.

## Запуск

```bash
cp .env.example .env      # при необходимости поправьте значения
docker compose up         # применит миграции и поднимет сервер
```

- приложение — http://localhost:8000
- письма (mailpit) — http://localhost:8025

Создать администратора:

```bash
docker compose exec web python manage.py createsuperuser
```

Подтверждение email обязательно, поэтому свежесозданный суперюзер не сможет войти
в приложение (в `/admin/` — сможет). Пометить его адрес подтверждённым:

```bash
docker compose exec web python manage.py shell -c "
from allauth.account.models import EmailAddress
from accounts.models import User
u = User.objects.get(email='admin@example.com')
EmailAddress.objects.update_or_create(
    user=u, email=u.email, defaults={'verified': True, 'primary': True})
"
```

## Разработка

```bash
docker compose exec web pytest        # тесты
uv run ruff check . && uv run ruff format .   # линт и формат
uv run python manage.py makemigrations        # миграции
```

Наполнить глобальные справочники (виды спорта, базовые упражнения зала) и стартовые
записи «Что нового». Команда идемпотентна — можно запускать повторно:

```bash
docker compose exec web python manage.py seed
```

Разобрать запросы страницы — панель django-debug-toolbar, она включается отдельным
флагом (по `DJANGO_DEBUG` не включается специально: иначе панель искажала бы счётчики
запросов в тестах):

```bash
DJANGO_DEBUG_TOOLBAR=True  # в .env
```

Локально (без Docker) — `uv sync`, свой PostgreSQL и `DATABASE_URL` в `.env`.

## Прод

Push в `main` с зелёными проверками деплоится на сервер автоматически
([.github/workflows/ci.yml](.github/workflows/ci.yml)); на pull request'ах гоняются
только проверки. Деплой на VDS с HTTPS — [docs/deploy.md](docs/deploy.md),
бэкапы и восстановление базы —
[docs/backup.md](docs/backup.md). Прод-стек (gunicorn + postgres) поднимается через
`./scripts/prod.sh up -d` и слушает только localhost; наружу его отдаёт системный nginx
с конфигом из [deploy/nginx/trainova.hotbar.pro.conf](deploy/nginx/trainova.hotbar.pro.conf).

Требования к продукту, дизайн-система и рабочие правила — в [CLAUDE.md](CLAUDE.md),
макеты экранов — в `docs/design/`.
