#!/usr/bin/env bash
# Чтение значений из .env.prod БЕЗ выполнения файла.
#
# source .env.prod ломается на нормальных для env-файла значениях: docker compose
# спокойно читает DEFAULT_FROM_EMAIL=Дневник тренировок <a@b.ru>, а shell видит
# в "<" перенаправление ввода. Поэтому берём значения построчно.

ENV_FILE="${ENV_FILE:-.env.prod}"

env_get() {
  local key="$1" default="${2:-}" value
  value="$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)"
  # Снимаем обрамляющие кавычки, если они есть.
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  # Переменные окружения имеют приоритет над файлом — удобно для разовых запусков.
  local from_env="${!key:-}"
  if [[ -n "$from_env" ]]; then
    printf '%s' "$from_env"
  elif [[ -n "$value" ]]; then
    printf '%s' "$value"
  else
    printf '%s' "$default"
  fi
}

env_require() {
  local key="$1" value
  value="$(env_get "$key")"
  if [[ -z "$value" ]]; then
    echo "ОШИБКА: ${key} не задан в ${ENV_FILE}" >&2
    exit 1
  fi
  printf '%s' "$value"
}
