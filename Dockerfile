FROM python:3.13-slim

# uv ставим бинарником из официального образа — без pip в рантайме.
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # venv лежит вне /app: код монтируется bind-mount'ом и затёр бы .venv внутри проекта.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Слой зависимостей кешируется отдельно от кода проекта.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

COPY . .

# Статика собирается при сборке, а не при старте контейнера: сборка идёт, пока
# старый контейнер ещё обслуживает сайт, и простой на деплое сжимается до пары
# секунд. Битый ассет теперь валит сборку — до остановки работающей версии.
# collectstatic не ходит ни в базу, ни в почту, поэтому переменные — заглушки.
RUN DJANGO_SECRET_KEY=collectstatic-only \
    DATABASE_URL=postgres://build@localhost/build \
    EMAIL_URL=consolemail:// \
    DJANGO_STATIC_MANIFEST=True \
    python manage.py collectstatic --noinput

EXPOSE 8000

# По умолчанию — прод-режим (миграции, gunicorn).
# Dev-compose переопределяет command на runserver, поэтому образ один на оба режима.
CMD ["/app/deploy/entrypoint.sh"]
