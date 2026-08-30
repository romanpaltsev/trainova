# Бэкапы и восстановление

## Что бэкапится

Только база PostgreSQL — в ней все данные приложения: пользователи, тренировки, подходы,
справочники. Загружаемых файлов в v1 нет (аватар — первая буква email), поэтому `MEDIA`
бэкапить нечего. Код и конфиги живут в git, кроме `.env.prod` — **его сохраните отдельно
в менеджер паролей**: без него дамп бесполезен (не поднимется база).

Формат дампа — `pg_dump -Fc`: сжатый, из него можно восстановить и отдельную таблицу.

## Как это устроено

`scripts/backup.sh` каждый день:

1. снимает `pg_dump` в `BACKUP_DIR` (по умолчанию `/var/backups/trainova`);
2. проверяет, что файл не пустой и **читается `pg_restore`** — иначе можно годами копить
   битые дампы и узнать об этом в день, когда они нужны;
3. выгружает файл в облако через `rclone` и убеждается, что он там появился;
4. удаляет локальные дампы старше `BACKUP_KEEP_LOCAL_DAYS` (7) и облачные старше
   `BACKUP_KEEP_REMOTE_DAYS` (90).

Любая осечка — ненулевой код возврата и запись в `/var/log/trainova-backup.log`; крон
пришлёт вывод письмом, если на сервере настроена локальная почта.

Расписание — `deploy/cron/trainova-backup`: бэкап в 04:17, проверка продления
сертификата в 03:41.

## Настройка выгрузки в облако (один раз)

Подойдёт любое S3-совместимое хранилище: Yandex Object Storage, Selectel, Backblaze B2.
Заведите бакет и ключ доступа **только на запись**, чтобы утечка ключа с сервера не дала
удалить историю бэкапов.

```bash
rclone config
# n (new remote) → имя: backup → тип: s3 → провайдер (Yandex / Other)
# access_key_id и secret_access_key — из консоли провайдера
# endpoint, например для Яндекса: storage.yandexcloud.net
```

Проверка и запись в `.env.prod`:

```bash
rclone lsd backup:
echo 'BACKUP_RCLONE_REMOTE=backup:trainova-backups' >> .env.prod   # или поправьте строку
./scripts/backup.sh          # первый прогон руками
ls -la /var/backups/trainova
rclone ls backup:trainova-backups
```

Если `BACKUP_RCLONE_REMOTE` пустой, скрипт всё равно сделает дамп, но честно предупредит,
что он остался только на этом сервере.

**Про шифрование.** В дампе есть email'ы и хэши паролей. У хранилища есть шифрование на
стороне провайдера, но если хочется, чтобы провайдер не мог прочитать данные — заведите
поверх remote типа `crypt` (`rclone config`, тип `crypt`, внутри указывается
`backup:trainova-backups`) и подставьте его имя в `BACKUP_RCLONE_REMOTE`. Пароль от crypt
храните там же, где `.env.prod`: без него дампы не расшифровать.

## Восстановление

```bash
cd /opt/trainova
ls -la /var/backups/trainova                  # выбрать дамп
./scripts/restore.sh /var/backups/trainova/dnevnik-2026-08-27-0417.dump
```

Скрипт покажет, что сейчас в базе, и спросит подтверждение (нужно ввести `да`). Дальше он:
останавливает web, чтобы никто не писал в базу → `pg_restore --clean --if-exists` →
проверяет `migrate --check`, что схема из дампа совпадает с кодом → поднимает web →
печатает контрольные счётчики строк.

Сверьте счётчики с ожидаемыми. Затем откройте сайт и загляните в историю тренировок.

Если дамп лежит только в облаке:

```bash
rclone copy backup:trainova-backups/dnevnik-2026-08-27-0417.dump /var/backups/trainova/
```

`migrate --check` упал — значит дамп старее кода: сначала
`git checkout <версия того времени>`, восстановление, затем `git pull` и обычный деплой,
который применит миграции по порядку.

## Разовая проверка, что бэкапы рабочие

Раз в несколько месяцев полезно убедиться, что дампы восстанавливаются. Безопасный способ —
поднять копию базы рядом и восстановить в неё, не трогая рабочую:

```bash
./scripts/prod.sh exec db createdb -U trainova restore_test
./scripts/prod.sh exec -T db pg_restore -U trainova -d restore_test --no-owner \
  < /var/backups/trainova/dnevnik-2026-08-27-0417.dump
./scripts/prod.sh exec db psql -U trainova -d restore_test \
  -c 'select count(*) from workouts_workout'
./scripts/prod.sh exec db dropdb -U trainova restore_test
```

Считается проверенным, только если счётчики похожи на реальные.

## Если потерян весь VDS

1. Новый сервер, шаги 1–4 из [deploy.md](deploy.md) (DNS на новый IP, docker и nginx, клон,
   `.env.prod` из менеджера паролей, `up -d`, конфиг nginx, `certbot --nginx`).
2. Скачать дамп: `rclone copy backup:trainova-backups/<файл> /var/backups/trainova/`.
3. `./scripts/restore.sh /var/backups/trainova/<файл>`.
4. Проверить вход и историю тренировок.

Потеря данных ограничена сутками: между последним бэкапом и аварией.
