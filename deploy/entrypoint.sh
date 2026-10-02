#!/bin/sh
# Старт прод-контейнера: миграции, затем gunicorn. Статика уже собрана при сборке
# образа (Dockerfile) — поэтому старт занимает секунды, а не десяток.
# Контейнер один, поэтому миграции здесь безопасны и деплой — это просто перезапуск.
set -e

python manage.py migrate --noinput

exec gunicorn config.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers "${GUNICORN_WORKERS:-3}" \
  --timeout "${GUNICORN_TIMEOUT:-60}" \
  --access-logfile - \
  --error-logfile - \
  --forwarded-allow-ips '*'
