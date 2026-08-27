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

EXPOSE 8000

# По умолчанию — прод-режим (миграции, статика, gunicorn).
# Dev-compose переопределяет command на runserver, поэтому образ один на оба режима.
CMD ["/app/deploy/entrypoint.sh"]
