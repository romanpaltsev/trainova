# Переезд на другой VDS

Инструкция для случая «сервер меняется, домен остаётся». Если сервер потерян целиком и
данные есть только в облаке — смотрите [docs/backup.md](backup.md), раздел «Если потерян
весь VDS»: там короткий путь без этапов сверки со старым сервером.

Схема не меняется: системный nginx + системный certbot, gunicorn и postgres в докере.
Установка нового сервера — это [docs/deploy.md](deploy.md) §2–4, поэтому здесь описано
только то, что специфично для переезда: что перенести, в каком порядке и как проверить.

## Что физически переносится

Ровно три вещи. Всё остальное приезжает из git и пересоздаётся само.

| Что | Почему нельзя пересоздать |
|---|---|
| Дамп базы `pg_dump -Fc` | все данные приложения |
| `.env.prod` | не в git; `DJANGO_SECRET_KEY` держит сессии, `POSTGRES_*` должны совпасть с дампом |
| `/root/.config/rclone/rclone.conf` | не в git; пароли обфусцированы, а при crypt-remote без него не расшифровать старые дампы |

Опционально, но рекомендуется — `/etc/letsencrypt` (см. §4).

**Переносить не нужно:** загруженных файлов в проекте нет вовсе (ни одного `FileField`),
`staticfiles` пересобирается `collectstatic` при каждом старте контейнера, кэш живёт в
памяти процесса (`LocMemCache`), а том postgres переносится дампом, а не копированием
каталога — версии и инициализация тома между машинами не обязаны совпадать.

**Не запускать при переезде:** `seed` и `createsuperuser`. Все справочники, новости и
учётные записи приедут из дампа. `seed` идемпотентен по имени, но если что-то
переименовывали в админке — он создаст дубль со старым названием.

## 0. Подготовка на старом сервере

Ничего не ломает, делается заранее.

```bash
cd /opt/trainova
./scripts/prod.sh ps                 # контейнер db должен быть Up
git rev-parse HEAD                   # на новом сервере нужен тот же коммит или новее
timedatectl                          # таймзона: крон бэкапа в 04:17 локального времени
sudo certbot certificates            # срок сертификата — понадобится в §4
```

Снять эталонные счётчики, с ними будем сверять результат восстановления:

```bash
./scripts/prod.sh exec -T db psql -U trainova -d trainova -tAc \
  "select 'users', count(*) from accounts_user
   union all select 'workouts', count(*) from workouts_workout
   union all select 'sets', count(*) from workouts_strengthset
   union all select 'sports', count(*) from workouts_sport
   union all select 'sessions', count(*) from django_session"
```

`.env.prod` поедет на новый сервер **дословно**. `DJANGO_SECRET_KEY` менять нельзя: на нём
держатся сессии из `django_session` и ссылки подтверждения email — смените, и разлогинятся
все. `POSTGRES_USER` и `POSTGRES_DB` тоже: они должны совпасть с тем, что внутри дампа.
`DJANGO_ALLOWED_HOSTS` и `CSRF_TRUSTED_ORIGINS` не трогаем, домен тот же.

**На время переезда не пушить в `main`.** Job `deploy` ходит по `DEPLOY_HOST` — а это домен,
и он смотрит туда же, куда DNS. Пуш в неудачный момент сделает `git reset --hard` и
пересборку на сервере, который вы как раз собрались замораживать.

## 1. Проверка сети нового сервера — до всего остального

Главный этап, если переезжаете из-за проблем с сетью. Делается на голом сервере.

```bash
sudo apt update && sudo apt -y install mtr-tiny tcpdump
IFACE=$(ip route get 8.8.8.8 | grep -oP 'dev \K\S+')
MYIP=$(hostname -I | awk '{print $1}')
```

Чужой трафик на интерфейсе — сколько из 500 пакетов адресовано не нам:

```bash
sudo timeout 60 tcpdump -pni "$IFACE" ip -c 500 2>/dev/null | grep -vc "$MYIP"
```

Флаг `-p` обязателен: без него карта уходит в promiscuous-режим и чужой трафик виден даже
на здоровом сервере, где ядро его отбрасывает.

Потери, переотправки и путь наружу — готовым скриптом, не копируя репозиторий на сервер:

```bash
# со своей машины, из корня проекта
ssh новый-сервер 'WINDOW=300 PINGS=100 bash -s' < scripts/check_network.sh
```

Секции про localhost ожидаемо отругаются — приложения там ещё нет. Счётчик переотправок без
трафика бессмыслен (скрипт сам предупредит «мало трафика»), поэтому в соседней сессии на те
же пять минут дайте нагрузку:

```bash
while true; do curl -s -o /dev/null https://speed.hetzner.de/100MB.bin --max-time 60; done
```

| Метрика | Хорошо | Стоп-сигнал |
|---|---|---|
| Чужих пакетов из 500 | 0–5 | десятки и больше |
| Потери ping до 8.8.8.8 / 1.1.1.1 / 77.88.8.8 | 0% | ≥1%, тем более сразу до всех трёх |
| Переотправки TCP за окно | <0,3% | >1% |
| `rtt` / `minrtt` живых соединений | ≈1–2× | ≥5×, канал в очереди |
| mtr, 2-й хоп (шлюз хостера) | потери 0, среднее ≈ минимуму | потери, наследуемые дальше по пути |

**Мерить дважды, второй раз вечером.** В разобранном случае (см. «Диагностика медленных
ответов» в [docs/deploy.md](deploy.md)) днём было терпимо, а к вечеру потери доходили до
60–80%. Один дневной замер ничего не доказывает.

Заодно проверить, не режет ли хостер исходящий SMTP — иначе это выяснится, когда пользователи
перестанут получать письма подтверждения:

```bash
timeout 10 openssl s_client -connect smtp.yandex.ru:465 -brief </dev/null
```

Пока эти проверки не зелёные в двух прогонах — дальше не идти. Плохой сервер меняют, а не
переезжают на него.

## 2. Сборка нового сервера

Полностью по [docs/deploy.md](deploy.md) §2–4, но **без** `certbot --nginx`, **без** `seed`
и **без** `createsuperuser` — сертификат приедет со старого сервера, данные из дампа.

Два места, где порядок и путь важны:

- каталог именно `/opt/trainova` — он захардкожен в `deploy/cron/trainova-backup`;
- `.env.prod` должен лежать **до первого `up -d`**: `POSTGRES_PASSWORD` применяется только
  при первой инициализации тома, и потом файл уже ничего не изменит (пришлось бы удалять том).

```bash
# со своей машины
ssh старый-сервер 'sudo cat /opt/trainova/.env.prod' > /tmp/envprod
scp /tmp/envprod новый-сервер:/tmp/envprod && shred -u /tmp/envprod
# на новом сервере
sudo install -o deploy -g deploy -m 600 /tmp/envprod /opt/trainova/.env.prod
shred -u /tmp/envprod
```

Поднять стек и убедиться, что приложение отвечает (база пока пустая — это нормально,
`entrypoint.sh` накатит миграции, а восстановление их перезапишет):

```bash
sudo -u deploy /opt/trainova/scripts/prod.sh build
sudo -u deploy /opt/trainova/scripts/prod.sh up -d
curl -sI -H "Host: trainova.hotbar.pro" -H "X-Forwarded-Proto: https" \
  http://127.0.0.1:8000/accounts/login/     # ожидаем 200
```

Конфиг nginx кладётся как обычно, **только HTTP-блок** — TLS дописывает certbot в §4.

Конфиг rclone переносим файлом, а не пересоздаём через `rclone config`: при crypt-remote без
него старые дампы не расшифровать.

```bash
ssh старый-сервер 'sudo cat /root/.config/rclone/rclone.conf' > /tmp/rclone.conf
scp /tmp/rclone.conf новый-сервер:/tmp/rclone.conf && shred -u /tmp/rclone.conf
# на новом сервере
sudo install -d -m 700 /root/.config/rclone
sudo install -o root -g root -m 600 /tmp/rclone.conf /root/.config/rclone/rclone.conf
shred -u /tmp/rclone.conf
sudo rclone ls backup:trainova-backups | tail    # видит старые дампы — конфиг доехал
```

## 3. Перенос данных

Точка невозврата — остановка web на старом сервере, а не переключение DNS. С этой минуты
дамп фиксирует состояние, и всё написанное позже потеряется.

Сначала погасить крон бэкапа и таймер certbot на старом сервере. Именно сейчас, а не потом:
иначе старый крон продолжит каждую ночь класть в **тот же** rclone-remote дампы замороженной
базы, и через неделю в списке «свежих» окажется устаревший файл. Это самая тихая ошибка при
переезде.

```bash
# на старом сервере
sudo rm -f /etc/cron.d/trainova-backup
sudo systemctl disable --now certbot.timer
cd /opt/trainova && sudo -u deploy ./scripts/prod.sh stop web
sudo systemctl stop nginx
```

Останавливаем web, а не весь стек: база нужна живой для `pg_dump`. nginx гасим, чтобы клиент
с закэшированным DNS не попал на старый сервер и не записал туда то, чего не будет нигде.
Пусть лучше получит ошибку соединения — громкий отказ честнее тихой потери данных.

```bash
# на старом сервере
sudo -u deploy ./scripts/prod.sh exec -T db pg_dump -U trainova -d trainova -Fc > /tmp/final.dump
sudo -u deploy ./scripts/prod.sh exec -T db pg_restore --list < /tmp/final.dump | head
sha256sum /tmp/final.dump
# со своей машины
ssh старый-сервер 'sudo cat /tmp/final.dump' > /tmp/final.dump
scp /tmp/final.dump новый-сервер:/tmp/final.dump
sha256sum /tmp/final.dump                        # сверить с суммой на старом
# на новом сервере
sudo mkdir -p /var/backups/trainova
sudo mv /tmp/final.dump /var/backups/trainova/dnevnik-$(date +%F-%H%M).dump
sha256sum /var/backups/trainova/dnevnik-*.dump   # сверить ещё раз
```

Штатный `./scripts/backup.sh` тут тоже сгодится, но он потянет ещё выгрузку в облако с
ротацией — на точке невозврата лишние движения не нужны.

```bash
# на новом сервере
cd /opt/trainova
sudo -u deploy ./scripts/restore.sh /var/backups/trainova/dnevnik-<нужный>.dump
```

Скрипт покажет, что сейчас в базе, спросит подтверждение (ввести буквально `да`), остановит
web, накатит дамп, прогонит `migrate --check` и поднимет web. Сверьте итоговые счётчики с
эталоном из §0 — они должны совпасть точно.

Если `migrate --check` упал, дамп старее кода: `git checkout <коммит из §0>`, восстановить,
затем `git pull` и обычный деплой — миграции применятся по порядку.

## 4. Сертификат и переключение домена

Сертификат лучше **скопировать**, а не выпускать заново. Это файл, валидный независимо от
того, какая машина его предъявляет, поэтому к моменту переключения DNS новый сервер уже
отдаёт правильный TLS и окна без HTTPS не возникает вовсе. Выпуск после переключения такое
окно создаёт, а при включённом HSTS браузер в это окно не пустит и кнопки «всё равно перейти»
не покажет. Плюс у Let's Encrypt лимит: 5 неудачных попыток в час на домен.

Если сертификат истёк или до конца меньше месяца — копировать смысла нет, выпускайте заново.

```bash
# со своей машины: tar, а не scp -r — live/ это симлинки в archive/, scp их разыменует,
# и следующее продление разъедется
ssh старый-сервер 'sudo tar -czf - -C /etc letsencrypt' > /tmp/le.tgz
scp /tmp/le.tgz новый-сервер:/tmp/le.tgz
# на новом сервере
sudo tar -xzf /tmp/le.tgz -C /etc --same-owner --numeric-owner && sudo rm /tmp/le.tgz
sudo certbot certificates
ls -l /etc/letsencrypt/live/trainova.hotbar.pro/    # симлинки должны остаться симлинками
grep -E 'authenticator|installer' /etc/letsencrypt/renewal/trainova.hotbar.pro.conf
```

`python3-certbot-nginx` должен быть установлен **до** копирования: в `renewal/*.conf` записан
плагин, которым продлевать. Дальше включаем TLS в nginx, не обращаясь в УЦ:

```bash
sudo certbot install --cert-name trainova.hotbar.pro --nginx
sudo nginx -t && sudo systemctl reload nginx
```

`install`, а не `--nginx`: не тратит лимит и не перевыпускает, а дописывает в конфиг ровно
тот же TLS-блок.

Теперь проверяем весь путь **до** переключения DNS — приёмом с `--resolve`, пока домен ещё
смотрит на старый сервер:

```bash
# со своей машины
curl -sI --resolve trainova.hotbar.pro:443:НОВЫЙ_IP https://trainova.hotbar.pro/accounts/login/
curl -sI --resolve trainova.hotbar.pro:80:НОВЫЙ_IP  http://trainova.hotbar.pro/
```

Ожидаем 200 с валидным TLS (**без** `-k`) и заголовком `strict-transport-security`, по HTTP —
301 на https. Затем пропишите `НОВЫЙ_IP trainova.hotbar.pro` в свой `/etc/hosts`, войдите в
браузере и посмотрите историю тренировок. Не переключайте DNS, пока это не пройдено.

Переключить A-запись (и AAAA, если она есть — иначе часть клиентов уйдёт по IPv6 на старый
сервер), проверить распространение и убедиться, что автопродление живёт на новом месте:

```bash
for ns in 8.8.8.8 1.1.1.1 77.88.8.8; do dig +short @$ns trainova.hotbar.pro A; done
sudo certbot renew --dry-run      # единственная проверка, требующая переключённого DNS
systemctl list-timers certbot.timer
```

Сразу после переключения закрыть старый сервер наглухо, но **не удалять** — это откат:

```bash
# на старом сервере
sudo -u deploy /opt/trainova/scripts/prod.sh down    # тома с базой сохраняются
sudo ufw deny 80 && sudo ufw deny 443
```

## 5. Проверки после переезда

| Что | Как | Ожидание |
|---|---|---|
| HTTPS | `curl -I https://trainova.hotbar.pro/accounts/login/` | 200, есть `strict-transport-security` |
| Редирект | `curl -I http://trainova.hotbar.pro/` | 301 на https |
| Сессии пережили | открыть в браузере, где были залогинены до переезда | остались залогинены — значит `SECRET_KEY` не менялся |
| Данные | история тренировок, подходы, места | счётчики совпали в §3 |
| Статика | Ctrl+F5, вкладка Network | CSS/JS 200, имена с хэшами |
| Почта | `./scripts/prod.sh exec web python manage.py sendtestemail вы@example.com` | письмо пришло |
| Регистрация | зарегистрировать тестовый адрес | письмо подтверждения дошло, ссылка работает |
| Логи | `./scripts/prod.sh logs web \| tail -50` | без трейсбеков и ошибок SMTP |
| Сеть | `./scripts/check_speed.sh` со своей машины | ноль зависаний дольше 2 с |

Включить крон бэкапов и прогнать руками. Права 644 и владелец root обязательны — иначе cron
файл молча игнорирует:

```bash
sudo cp /opt/trainova/deploy/cron/trainova-backup /etc/cron.d/trainova-backup
sudo chown root:root /etc/cron.d/trainova-backup && sudo chmod 644 /etc/cron.d/trainova-backup
sudo /opt/trainova/scripts/backup.sh
sudo rclone ls backup:trainova-backups | tail -3
```

Обновить в GitHub секрет `DEPLOY_KNOWN_HOSTS` — у нового сервера другой host key, и без
этого деплой упадёт на проверке отпечатка. Так и задумано: known_hosts берётся из секрета
именно затем, чтобы подменённый DNS не увёл деплой на чужой сервер.

```bash
ssh-copy-id -i ~/.ssh/trainova_deploy.pub deploy@НОВЫЙ_IP
ssh-keyscan -t ed25519 trainova.hotbar.pro     # это значение — в секрет
```

`DEPLOY_HOST`, `DEPLOY_USER` и `DEPLOY_SSH_KEY` менять не нужно: host — это домен. Проверить
пустым коммитом в `main`, что job доходит до «приложение отвечает».

Повторить `./scripts/check_speed.sh` вечером и на следующий день — ради вечернего замера
переезд и затевался.

## 6. Откат

Старый сервер держать минимум **14 дней**, том с базой не удалять.

Если сломались сразу, пока на новом никто ничего не записал: вернуть A-запись на старый IP и
поднять там всё обратно. Сертификат на старом сервере остался нетронутым (мы копировали, а
не переносили), поэтому TLS поднимется сразу и HSTS не помешает.

```bash
# на старом сервере
sudo ufw allow 80 && sudo ufw allow 443
sudo -u deploy /opt/trainova/scripts/prod.sh up -d
sudo systemctl start nginx
sudo cp /opt/trainova/deploy/cron/trainova-backup /etc/cron.d/trainova-backup
sudo systemctl enable --now certbot.timer
```

Если прошло время и на новом уже есть новые записи — откат превращается в переезд в обратную
сторону теми же шагами §3 с обменом ролями.

Откатываться не надо, если проблема оказалась в сети нового хостера: старый покидается ровно
поэтому. Тогда ищут третий сервер, а не возвращаются на второй.

## 7. Вывод старого сервера

Не раньше чем через две недели и после хотя бы одного успешного ночного бэкапа с нового
сервера, доехавшего в облако.

1. Убедиться, что в облаке есть свежие дампы **с нового** сервера.
2. Снять со старого последний архивный дамп и положить рядом с `.env.prod` — страховка на
   случай «данные пропали, а заметили через месяц».
3. Убрать ссылки на старый адрес: DNS (A, AAAA), `~/.ssh/config`, мониторинг, файрволы.
4. Погасить VDS и закрыть услугу.
5. `ssh-keygen -R <старый IP>` и `ssh-keygen -R trainova.hotbar.pro` на своей машине —
   иначе следующее подключение встретит предупреждение о смене host key.
