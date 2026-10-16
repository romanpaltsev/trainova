#!/usr/bin/env bash
# Ежедневный бэкап: pg_dump → проверка дампа → выгрузка в облако дампа и фото →
# чистка просроченного в приложении (purge_deleted) → чистка старых дампов.
#
# Любая осечка возвращает ненулевой код: крон не должен молча «делать бэкапы»,
# которые на самом деле не делаются.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

COMPOSE="$PROJECT_DIR/scripts/prod.sh"
LOG_FILE="${BACKUP_LOG:-/var/log/trainova-backup.log}"
LOCK_FILE="/tmp/trainova-backup.lock"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }
fail() { log "ОШИБКА: $*"; exit 1; }

# Не даём двум бэкапам идти одновременно (крон + запуск руками).
exec 9>"$LOCK_FILE"
flock -n 9 || fail "бэкап уже выполняется"

# Лог и на экран, и в файл — крон присылает вывод, а файл остаётся историей.
if [[ -w "$(dirname "$LOG_FILE")" ]]; then
  exec > >(tee -a "$LOG_FILE") 2>&1
fi

[[ -f .env.prod ]] || fail "нет .env.prod"
# shellcheck disable=SC1091
source "$PROJECT_DIR/scripts/env.sh"

POSTGRES_DB="$(env_require POSTGRES_DB)"
POSTGRES_USER="$(env_require POSTGRES_USER)"

BACKUP_DIR="$(env_get BACKUP_DIR /var/backups/trainova)"
KEEP_LOCAL_DAYS="$(env_get BACKUP_KEEP_LOCAL_DAYS 7)"
KEEP_REMOTE_DAYS="$(env_get BACKUP_KEEP_REMOTE_DAYS 90)"
REMOTE="$(env_get BACKUP_RCLONE_REMOTE)"
MEDIA_DIR="$(env_get MEDIA_DIR /var/lib/trainova/media)"

STAMP="$(date '+%Y-%m-%d-%H%M')"
DUMP_NAME="dnevnik-${STAMP}.dump"
DUMP_PATH="${BACKUP_DIR}/${DUMP_NAME}"

mkdir -p "$BACKUP_DIR"

log "=== бэкап ${DUMP_NAME}"

# -Fc: сжатый формат pg_dump, из него можно восстановить и отдельную таблицу.
$COMPOSE exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$DUMP_PATH" \
  || fail "pg_dump не отработал"

SIZE="$(stat -c %s "$DUMP_PATH")"
[[ "$SIZE" -gt 1024 ]] || fail "дамп подозрительно мал: ${SIZE} байт"

# Проверяем, что дамп читается. Без этого можно годами копить битые файлы
# и узнать об этом в день, когда они понадобятся.
# Файл подаём в stdin без имени: /dev/stdin внутри контейнера pg_restore
# позиционировать не может и падает на «did not find magic string».
$COMPOSE exec -T db pg_restore --list < "$DUMP_PATH" > /dev/null \
  || fail "дамп не читается pg_restore"

log "дамп готов: $(numfmt --to=iec "$SIZE" 2>/dev/null || echo "${SIZE} байт")"

if [[ -n "$REMOTE" ]]; then
  command -v rclone >/dev/null || fail "rclone не установлен, а BACKUP_RCLONE_REMOTE задан"
  rclone copyto "$DUMP_PATH" "${REMOTE}/${DUMP_NAME}" || fail "выгрузка в ${REMOTE} не удалась"
  # Убеждаемся, что файл действительно доехал.
  rclone lsf "${REMOTE}/${DUMP_NAME}" >/dev/null || fail "файла нет в ${REMOTE} после выгрузки"
  log "выгружено в ${REMOTE}/${DUMP_NAME}"

  rclone delete "$REMOTE" --min-age "${KEEP_REMOTE_DAYS}d" --include "dnevnik-*.dump" || \
    log "предупреждение: не удалось почистить старые дампы в облаке"

  # Фото: имя файла уникально на каждую загрузку, содержимое не меняется, поэтому
  # copy только добавляет новое и ничего не удаляет в облаке — любой дамп за срок
  # хранения восстанавливается со всеми фото, на которые ссылается. До
  # purge_deleted: файл, который уборка сотрёт этой ночью, уже лежит в облаке.
  if [[ -d "$MEDIA_DIR" ]]; then
    rclone copy "$MEDIA_DIR" "${REMOTE}/media" || fail "фото не выгрузились в ${REMOTE}/media"
    log "фото выгружены в ${REMOTE}/media: файлов $(find "$MEDIA_DIR" -type f | wc -l)"
  fi
else
  log "предупреждение: BACKUP_RCLONE_REMOTE не задан — дамп и фото остались только на этом сервере"
fi

# Чистка просроченного (корзина тренировок старше 30 дней, аккаунты после
# отсрочки, файлы фото без записи) — только после удачного дампа и выгрузки фото:
# всё, что удаляется насовсем, остаётся в свежем бэкапе. Крон уже запускает этот
# скрипт, поэтому отдельной строки в crontab не нужно.
$COMPOSE exec -T web python manage.py purge_deleted || fail "очистка удалённого не удалась"

find "$BACKUP_DIR" -name 'dnevnik-*.dump' -mtime "+${KEEP_LOCAL_DAYS}" -delete
log "локально дампов: $(find "$BACKUP_DIR" -name 'dnevnik-*.dump' | wc -l)"
log "=== готово"
