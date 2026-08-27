#!/usr/bin/env bash
# Обёртка над docker compose для прод-стека.
#
# Зачем: env_file в compose передаёт переменные только внутрь контейнеров, а для
# подстановки ${DOMAIN}, ${POSTGRES_*} в самом compose-файле нужен --env-file.
# Без него DOMAIN оказался бы пустым, и nginx поднялся бы с пустым server_name.
#
# Примеры:
#   ./scripts/prod.sh up -d
#   ./scripts/prod.sh logs -f web
#   ./scripts/prod.sh exec web python manage.py createsuperuser
set -euo pipefail

cd "$(dirname "$0")/.."
[[ -f .env.prod ]] || { echo "Нет .env.prod — скопируйте .env.prod.example и заполните." >&2; exit 1; }

exec docker compose --env-file .env.prod -f docker-compose.prod.yml "$@"
