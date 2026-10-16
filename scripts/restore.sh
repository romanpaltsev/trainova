#!/usr/bin/env bash
# Восстановление базы из дампа и фото из облака. Перезаписывает текущие данные —
# спрашивает подтверждение.
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
REMOTE="$(env_get BACKUP_RCLONE_REMOTE)"
MEDIA_DIR="$(env_get MEDIA_DIR /var/lib/trainova/media)"

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

# Фото: дамп ссылается на файлы по именам, а сами файлы лежат в облаке — backup.sh
# только добавляет их туда и ничего не стирает. copy докладывает недостающие и не
# удаляет лишние: их уберёт ночная уборка (purge_deleted). Уже после подъёма web и
# без fail: база восстановлена, и осечка с фото не должна оставлять сайт лежать.
# Нужен root: каталог фото создал докер, а конфиг rclone лежит у root.
MEDIA_COMMAND="sudo rclone copy ${REMOTE}/media ${MEDIA_DIR}"
if [[ -z "$REMOTE" ]]; then
  log "фото: BACKUP_RCLONE_REMOTE не задан — каталог ${MEDIA_DIR} остаётся как есть"
elif [[ "$EUID" -ne 0 ]]; then
  log "фото: верните их из облака от root — ${MEDIA_COMMAND}"
elif ! command -v rclone >/dev/null; then
  log "предупреждение: rclone не установлен — фото не вернулись; поставьте и выполните ${MEDIA_COMMAND}"
elif ! rclone lsf --max-depth 1 "${REMOTE}/media" >/dev/null 2>&1; then
  log "фото: в ${REMOTE}/media ничего нет"
else
  log "возвращаю фото из ${REMOTE}/media в ${MEDIA_DIR}"
  mkdir -p "$MEDIA_DIR"
  rclone copy "${REMOTE}/media" "$MEDIA_DIR" \
    || log "предупреждение: фото не вернулись — повторите ${MEDIA_COMMAND}"
fi

echo
echo "Восстановлено. Теперь в базе:"
echo "  пользователей: $(count 'select count(*) from accounts_user')"
echo "  тренировок:    $(count 'select count(*) from workouts_workout')"
echo "  подходов:      $(count 'select count(*) from workouts_strengthset')"
echo "  видов спорта:  $(count 'select count(*) from workouts_sport')"
