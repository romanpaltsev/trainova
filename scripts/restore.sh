#!/usr/bin/env bash
# Восстановление базы из дампа. Перезаписывает текущие данные — спрашивает подтверждение.
#
# Использование:
#   scripts/restore.sh /var/backups/trainova/dnevnik-2026-08-27-0417.dump
#   scripts/restore.sh <файл> --force     # без вопросов (для скриптов)
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

COMPOSE="$PROJECT_DIR/scripts/prod.sh"
DUMP_PATH="${1:-}"
FORCE="${2:-}"

log() { echo "$(date '+%H:%M:%S') $*"; }
fail() { echo "ОШИБКА: $*" >&2; exit 1; }

[[ -n "$DUMP_PATH" ]] || fail "укажите файл дампа: scripts/restore.sh <файл> [--force]"
[[ -f "$DUMP_PATH" ]] || fail "файл не найден: $DUMP_PATH"

[[ -f .env.prod ]] || fail "нет .env.prod"
# shellcheck disable=SC1091
source "$PROJECT_DIR/scripts/env.sh"
POSTGRES_DB="$(env_require POSTGRES_DB)"
POSTGRES_USER="$(env_require POSTGRES_USER)"

count() {
  $COMPOSE exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1" 2>/dev/null || echo "?"
}

echo "Файл дампа:  $DUMP_PATH ($(du -h "$DUMP_PATH" | cut -f1))"
echo "База:        $POSTGRES_DB на контейнере db"
echo "Сейчас в ней: пользователей $(count 'select count(*) from accounts_user'),"\
     "тренировок $(count 'select count(*) from workouts_workout')"
echo
echo "Восстановление ПЕРЕЗАПИШЕТ текущие данные этой базы."

if [[ "$FORCE" != "--force" ]]; then
  read -r -p "Продолжить? Напишите 'да': " answer
  [[ "$answer" == "да" ]] || fail "отменено"
fi

log "останавливаю приложение (чтобы никто не писал в базу)"
$COMPOSE stop web >/dev/null

log "восстанавливаю дамп"
# --clean --if-exists: сносим существующие объекты перед созданием.
# --no-owner: владелец в дампе может не совпадать с пользователем на новом сервере.
if ! $COMPOSE exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
       --clean --if-exists --no-owner --no-privileges < "$DUMP_PATH"; then
  log "pg_restore завершился с предупреждениями — проверяю результат ниже"
fi

log "проверяю, что схема соответствует коду"
$COMPOSE run --rm --entrypoint python web manage.py migrate --check >/dev/null \
  || fail "схема из дампа не совпадает с миграциями кода: сначала обновите код, затем migrate"

log "поднимаю приложение"
$COMPOSE up -d web >/dev/null

echo
echo "Восстановлено. Теперь в базе:"
echo "  пользователей: $(count 'select count(*) from accounts_user')"
echo "  тренировок:    $(count 'select count(*) from workouts_workout')"
echo "  подходов:      $(count 'select count(*) from workouts_strengthset')"
echo "  видов спорта:  $(count 'select count(*) from workouts_sport')"
