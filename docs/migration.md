# Переезд на другой VDS

Инструкция для случая «сервер меняется, домен остаётся». Если сервер потерян целиком и
данные есть только в облаке — смотрите [docs/backup.md](backup.md), раздел «Если потерян
весь VDS»: там короткий путь без этапов сверки со старым сервером.

Схема не меняется: системный nginx + системный certbot, gunicorn и postgres в докере.
Установка нового сервера — это [docs/deploy.md](deploy.md) **§2** (пользователь, docker,
файрвол). Разделы §3 и §4 оттуда **заменены** разделами 3 и 4 этой инструкции: `.env.prod`
переносится дословно, а не создаётся заново, и сертификат выпускается позже (§6). Раздел
deploy.md §5 (`seed`, `createsuperuser`) при переезде не выполняется вовсе.

Порядок разделов — это и есть порядок работы. Раздел §0 делается за сутки, §1–§4 не трогают
работающий сайт совсем, §5 начинается с точки невозврата, §6 переключает домен.

## Что физически переносится

Ровно три вещи. Всё остальное приезжает из git и пересоздаётся само.

| Что | Почему нельзя пересоздать |
|---|---|
| Дамп базы `pg_dump -Fc` | все данные приложения |
| `.env.prod` | не в git; `DJANGO_SECRET_KEY` держит сессии, `POSTGRES_*` должны совпасть с дампом |
| `/root/.config/rclone/rclone.conf` | не в git; пароли обфусцированы, а при crypt-remote без него не расшифровать старые дампы |

**Сертификат выпускается на новом сервере заново** (§6), а не копируется. Копирование
`/etc/letsencrypt` тоже возможно и убирает окно без HTTPS, но требует таскать между машинами
архив с приватными ключами; здесь выбран простой путь. Цена решения — два следствия, которые
надо знать заранее:

- между переключением DNS и успешным `certbot` сайт какое-то время без HTTPS. При
  `DJANGO_SECURE_HSTS_SECONDS=3600` это терпимо, при значении в год браузер в это окно не
  пустит вовсе и кнопки «всё равно перейти» не покажет — тогда сначала понизьте HSTS до 3600,
  дайте суткам пройти и только потом переезжайте;
- браузерную проверку нового сервера до переключения DNS сделать не получится:
  `SECURE_SSL_REDIRECT` отправит на https, которого ещё нет. Поэтому §4 и §5 проверяют путь
  локальным curl с подделанными заголовками, а браузером смотрят уже после §6 (шаг «Данные»
  в таблице §8).

Если окно без HTTPS неприемлемо — в конце §6 описан выпуск заранее через DNS-01.

**Переносить не нужно:** загруженных файлов в проекте нет вовсе (ни одного `FileField`),
`staticfiles` пересобирается `collectstatic` при каждом старте контейнера, кэш живёт в
памяти процесса (`LocMemCache`), а том postgres переносится дампом, а не копированием
каталога — версии и инициализация тома между машинами не обязаны совпадать.

**Не запускать при переезде:** `seed` и `createsuperuser`. Все справочники, новости и
учётные записи приедут из дампа. `seed` идемпотентен по имени, но если что-то
переименовывали в админке — он создаст дубль со старым названием.

## 0. За сутки до переезда

Ничего не ломает, но сделать надо заранее — иначе §6 будет ждать впустую.

Понадобится `dig` (пакет `dnsutils`): в WSL и на минимальных системах его нет, а в §6 он
обязателен — там опрашиваются NS-серверы зоны.

```bash
# своя машина
command -v dig >/dev/null || sudo apt -y install dnsutils
dig +noall +answer trainova.hotbar.pro A       # вторая колонка — текущий TTL, запишите
dig +noall +answer trainova.hotbar.pro AAAA    # пусто — записи AAAA нет
```

Два вызова, а не один: `dig` берёт из строки только последний тип, и `A AAAA` спросит
только AAAA.

Понизьте TTL записей A и AAAA до 300 в панели DNS и **дождитесь, пока пройдёт старое
значение**: резолверы уже раздали ответ со старым TTL, и пока он не истечёт, новый им не
поможет. Было 3600 — ждать час, было 86400 — сутки.

```bash
dig +noall +answer trainova.hotbar.pro A           # в ответе уже 300 — можно переезжать
```

Заведите ssh-алиасы: дальше вся инструкция ходит по ним, и адреса вводятся руками ровно
здесь. Подставьте свои значения вместо `НОВЫЙ_IP`, `СТАРЫЙ_IP` и `ваш-пользователь` **до**
выполнения — блок дописывает файл, повторно его вставлять не нужно.

```bash
cat >> ~/.ssh/config <<'CFG'
Host trainova-new
    HostName НОВЫЙ_IP
    User ваш-пользователь
Host trainova-old
    HostName СТАРЫЙ_IP
    User ваш-пользователь
CFG
chmod 600 ~/.ssh/config
ssh trainova-new true      # первый вход руками: отпечаток сверить с консолью хостера
ssh trainova-old true

# адрес нового сервера ещё и переменной — для curl --resolve, который алиасов не знает
NEW_IP=<адрес нового сервера>
```

Переменная живёт только в текущей оболочке: открыли новое окно — задайте заново. Свой
почтовый адрес в командах (`sendtestemail`, `certbot -m`) подставляйте вместо
`вы@example.com` — они выполняются на сервере, где этой переменной нет.

Ключ деплоя должен быть под рукой, и публичная половина нужна **файлом** (в §2 она уезжает
через `scp`):

```bash
ls -l ~/.ssh/trainova_deploy
[ -s ~/.ssh/trainova_deploy.pub ] \
  || ssh-keygen -y -f ~/.ssh/trainova_deploy > ~/.ssh/trainova_deploy.pub
chmod 644 ~/.ssh/trainova_deploy.pub
ssh-keygen -l -f ~/.ssh/trainova_deploy.pub    # печатает отпечаток, а не ошибку
```

Приватного ключа нет нигде (из секретов GitHub он обратно не читается) — заведите новую пару
`ssh-keygen -t ed25519 -C "github-actions-trainova" -f ~/.ssh/trainova_deploy -N ""`. Тогда
новый `.pub` нужно положить не только на новый сервер (§2), но и в `authorized_keys`
пользователя `deploy` на **старом**: §3 и §5 ходят туда именно этим ключом. И в §7
дополнительно обновить секрет `DEPLOY_SSH_KEY`.

Заодно посмотрите, сколько осталось сертификату старого сервера:

```bash
ssh trainova-old                # живая сессия: sudo попросит пароль, нужен tty
sudo certbot certificates
```

На этом сертификате держится откат из §9: после §6 домен смотрит на новый сервер, и HTTP-01
со старого больше не пройдёт — продлить его там уже не выйдет. Кончится срок — возвращаться
будет некуда.

**На время переезда не пушить в `main`.** Job `deploy` ходит по `DEPLOY_HOST` — а это домен,
и он смотрит туда же, куда DNS. Пуш в неудачный момент сделает `git reset --hard` и
пересборку на сервере, который вы как раз собрались замораживать.

## 1. Проверка сети нового сервера — до всего остального

Главный этап, если переезжаете из-за проблем с сетью. Делается на голом сервере.

```bash
# новый сервер (ssh trainova-new)
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
ssh trainova-new 'WINDOW=300 PINGS=100 bash -s' < scripts/check_network.sh
```

Секции про localhost ожидаемо отругаются — приложения там ещё нет. Счётчик переотправок без
трафика бессмыслен (скрипт сам предупредит «мало трафика»), поэтому в соседней сессии на те
же пять минут дайте нагрузку:

```bash
# соседняя сессия, своя машина. Остановить Ctrl+C, когда check_network.sh допечатает отчёт
ssh trainova-new 'while true; do curl -s -o /dev/null https://speed.hetzner.de/100MB.bin --max-time 60; done'
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
# новый сервер
timeout 10 openssl s_client -connect smtp.yandex.ru:465 -brief </dev/null
# ждём CONNECTION ESTABLISHED и Verification: OK; таймаут или connect:errno=111 —
# хостер режет исходящий SMTP, письма подтверждения с этого сервера уходить не будут
```

Пока эти проверки не зелёные в двух прогонах — дальше не идти. Плохой сервер меняют, а не
переезжают на него.

## 2. Сборка нового сервера

Сервер собирается по [docs/deploy.md](deploy.md) §2: пакеты (включая `python3-certbot-nginx`,
он понадобится в §6), пользователь `deploy`, docker, ufw, автообновления. Разделы §3 и §4
deploy.md выполняются не оттуда, а ниже — в §3 и §4 этой инструкции; `certbot --nginx` из
deploy.md §4 не запускается совсем, он в §6 после переключения DNS; `seed` и `createsuperuser`
из §5 при переезде не запускаются вовсе.

Два места в deploy.md §2 спросят подтверждение, и через `ssh host 'команда'` без tty они не
пройдут: `ufw enable` предупреждает про обрыв ssh (отвечать `y`, OpenSSH разрешён строкой
выше) и `dpkg-reconfigure -plow unattended-upgrades` открывает диалог. Делайте этот раздел в
живой сессии.

Ниже — только то, чем переезд отличается от установки с нуля.

**Каталог именно `/opt/trainova`** — он захардкожен в `deploy/cron/trainova-backup`.

**Таймзона ставится до крон-файла.** `/etc/cron.d/trainova-backup` запускает бэкап в 04:17
локального времени; у свежего VDS это обычно UTC, и задача уедет на утро.

```bash
# своя машина: какая зона на старом сервере
ssh trainova-old timedatectl | grep 'Time zone'

# новый сервер: ставим ту же
sudo timedatectl set-timezone Europe/Moscow
timedatectl | grep 'Time zone'
```

**Своп при 1 ГБ памяти.** Тяжелее всего не работа, а сборка: `prod.sh build` гонит
`uv sync` рядом с живым postgres, и без свопа её убивает OOM-killer — причём повторится это
при каждом автодеплое.

```bash
free -m; swapon --show
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab   # без этой строки своп исчезнет после ребута
```

**Ротация docker-логов.** gunicorn пишет access-лог в stdout, docker складывает его в
json-файл без ограничения размера, а в compose ключа `logging` нет.

```bash
sudo tee /etc/docker/daemon.json >/dev/null <<'JSON'
{ "log-driver": "json-file", "log-opts": { "max-size": "10m", "max-file": "3" } }
JSON
sudo systemctl restart docker
```

**Ключ пользователя `deploy` кладётся руками.** `ssh-copy-id` из deploy.md §8 на свежем
сервере не работает: он должен сначала войти под `deploy`, а `adduser --disabled-password`
не оставил ни пароля, ни ключа.

```bash
# своя машина
scp ~/.ssh/trainova_deploy.pub trainova-new:/tmp/trainova_deploy.pub

# новый сервер, под своим пользователем с sudo
sudo install -d -m 700 -o deploy -g deploy /home/deploy/.ssh
sudo install -m 600 -o deploy -g deploy /tmp/trainova_deploy.pub /home/deploy/.ssh/authorized_keys
rm /tmp/trainova_deploy.pub
sudo usermod -aG adm "$USER"      # чтобы потом читать логи nginx без sudo (нужен перезаход)
```

Права критичны: при `StrictModes yes` (умолчание sshd) каталог не 700 или файл не
`deploy:deploy` — и ключ молча игнорируется. Диагноз виден только в
`sudo journalctl -u ssh -n 20` строкой `Authentication refused: bad ownership or modes`.

Проверка со своей машины:

```bash
ssh -i ~/.ssh/trainova_deploy -o StrictHostKeyChecking=accept-new deploy@trainova-new \
  'id; docker ps >/dev/null && echo docker-ok'
```

`deploy@trainova-new` — это алиас из §0 с подменённым пользователем: адрес берётся из
`~/.ssh/config`, а входим не собой, а `deploy`.

Ключ берётся **тот же**, что работал на старом сервере, — тогда секрет `DEPLOY_SSH_KEY` в
GitHub менять не придётся, поменяется только `DEPLOY_KNOWN_HOSTS` (§7).

## 3. Код, `.env.prod` и rclone

Порядок жёсткий: сначала клон (`git clone` требует пустой каталог), потом `.env.prod`, и
только после этого `up -d` из §4. `POSTGRES_PASSWORD` применяется **только** при
инициализации тома, и позже файл на пароль базы уже не влияет — иначе пришлось бы удалять том.

```bash
# новый сервер
sudo mkdir -p /opt/trainova && sudo chown deploy:deploy /opt/trainova
sudo -u deploy -H git clone https://github.com/romanpaltsev/trainova.git /opt/trainova
sudo -u deploy -H git -C /opt/trainova log --oneline -1

# своя машина: приватный ключ деплоя лежит только здесь
ssh -i ~/.ssh/trainova_deploy deploy@trainova-old 'git -C /opt/trainova rev-parse HEAD'
```

Коммит на новом сервере должен быть тот же или новее. Если он новее и в нём есть миграции,
которых нет в дампе, `restore.sh` штатно остановится на `migrate --check` — это не поломка,
а известная ветка, она разобрана в §5. Чтобы сравнивать одинаковое, смотрите на обеих
машинах `git -C /opt/trainova rev-parse --short HEAD`.

`.env.prod` поедет на новый сервер **дословно**. Забирать его нужно от имени `deploy`: он
владеет файлом, поэтому sudo не участвует совсем. Через `ssh 'sudo cat …'` файл не забрать —
без tty sudo попросит пароль, и приедет пустота; а `ssh -t` добавит `\r` в каждую строку, и
это сломает `scripts/env.sh` (он режет строки `cut`, `\r` не снимает).

```bash
# своя машина
umask 077
ssh -i ~/.ssh/trainova_deploy deploy@trainova-old 'cat /opt/trainova/.env.prod' > ~/envprod
ssh -i ~/.ssh/trainova_deploy deploy@trainova-old 'sha256sum /opt/trainova/.env.prod'
sha256sum ~/envprod                         # суммы обязаны совпасть
scp ~/envprod trainova-new:~/envprod && shred -u ~/envprod

# новый сервер
sudo install -o deploy -g deploy -m 600 ~/envprod /opt/trainova/.env.prod
shred -u ~/envprod
sudo grep -c $'\r' /opt/trainova/.env.prod        # обязан напечатать 0
sudo tail -c 1 /opt/trainova/.env.prod | od -An -c  # ждём \n — файл не обрезан
```

Что в нём проверить перед первым запуском:

```bash
cd /opt/trainova
diff <(sudo grep -oE '^[A-Z_]+=' .env.prod | sort) <(grep -oE '^[A-Z_]+=' .env.prod.example | sort)
sudo grep -E '^(POSTGRES_DB|POSTGRES_USER|WEB_PORT|DJANGO_ALLOWED_HOSTS|CSRF_TRUSTED_ORIGINS|DJANGO_SECURE_HSTS_SECONDS|GUNICORN_WORKERS)=' .env.prod
sudo ss -lntp | grep -E ':8000\b' || echo '8000 свободен'
```

- `DJANGO_SECRET_KEY` менять нельзя: на нём держатся сессии из `django_session` и ссылки
  подтверждения email — смените, и разлогинятся все.
- `POSTGRES_USER` и `POSTGRES_DB` должны совпасть с тем, что внутри дампа: `restore.sh`
  зовёт `pg_restore` именно этими `-U` и `-d`.
- `DJANGO_ALLOWED_HOSTS` и `CSRF_TRUSTED_ORIGINS` не трогаем, домен тот же.
- `WEB_PORT` тоже не трогаем. Порт 8000 зашит числом ещё в двух местах —
  `proxy_pass http://127.0.0.1:8000` в `deploy/nginx/trainova.hotbar.pro.conf` и health-check
  job `deploy` в `ci.yml`; при другом порте сайт отдаст 502, а деплой после 15 попыток
  напишет «приложение не поднялось». Правильный ход — освободить 8000 на новом сервере.
- `GUNICORN_WORKERS` — единственное, что законно подогнать под новую машину: при 1 ГБ RAM
  поставьте 2.
- Посмотрите фактическое `DJANGO_SECURE_HSTS_SECONDS`: от него зависит, насколько болезненно
  окно без HTTPS в §6.

Конфиг rclone переносим файлом, а не пересоздаём через `rclone config`: при crypt-remote без
него старые дампы не расшифровать. Класть его нужно root'у — бэкап запускается кроном от root.

```bash
# старый сервер, живая сессия (файл root'овый — нужен tty для пароля sudo)
ssh trainova-old
sudo rclone config file          # фактический путь; обычно /root/.config/rclone/rclone.conf
sudo install -o "$USER" -g "$USER" -m 600 /root/.config/rclone/rclone.conf ~/rclone.conf
sha256sum ~/rclone.conf          # запомнить: сверим на новом сервере

# своя машина
umask 077
scp trainova-old:~/rclone.conf ~/rclone.conf && scp ~/rclone.conf trainova-new:~/rclone.conf
ssh trainova-old 'shred -u ~/rclone.conf'; shred -u ~/rclone.conf

# новый сервер
sudo install -d -m 700 -o root -g root /root/.config/rclone
sudo install -o root -g root -m 600 ~/rclone.conf /root/.config/rclone/rclone.conf
shred -u ~/rclone.conf
sudo sha256sum /root/.config/rclone/rclone.conf    # та же сумма, что на старом сервере
sudo rclone listremotes                            # имя совпадает с BACKUP_RCLONE_REMOTE
sudo rclone ls backup:trainova-backups | tail -3   # видит старые дампы — конфиг доехал
```

## 4. Стек и nginx на новом сервере

```bash
sudo docker volume ls | grep trainova-prod || echo 'томов нет — так и нужно'
sudo -u deploy /opt/trainova/scripts/prod.sh build
sudo -u deploy /opt/trainova/scripts/prod.sh up -d
sudo -u deploy /opt/trainova/scripts/prod.sh ps        # db: Up (healthy), web: Up
```

База после этого будет пустой, но с накатанными миграциями — так и задумано:
`deploy/entrypoint.sh` при каждом старте гонит `migrate` и `collectstatic`, а восстановление
из дампа схему перезапишет целиком. Заодно это проверяет, что пароль в `.env.prod` совпал с
инициализированным томом и приложение вообще поднимается — до точки невозврата.

```bash
curl -sI -H "Host: trainova.hotbar.pro" -H "X-Forwarded-Proto: https" \
  http://127.0.0.1:8000/accounts/login/ | head -1     # 200
curl -sI http://127.0.0.1:8000/accounts/login/ | head -1                                 # 400 — норма
curl -sI -H "Host: trainova.hotbar.pro" http://127.0.0.1:8000/accounts/login/ | head -1  # 301 — норма
```

Оба заголовка подделывают то, что добавит nginx: без `Host` будет 400 (`ALLOWED_HOSTS`), без
`X-Forwarded-Proto` — 301 на https (`SECURE_SSL_REDIRECT`). Ровно этот запрос делает job
`deploy` в CI.

Дальше конфиг nginx — **только HTTP-блок**, TLS допишет certbot в §6. И заглушка для
неизвестных доменов: в git её нет, хотя `deploy/nginx/trainova.hotbar.pro.conf` рассчитывает,
что на сервере она уже есть (и потому сам намеренно без `default_server`).

```bash
sudo tee /etc/nginx/sites-available/000-catch-all >/dev/null <<'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 444;
}
NGINX
sudo rm -f /etc/nginx/sites-enabled/default        # у дефолтного сайта Ubuntu тоже default_server
sudo ln -sfn /etc/nginx/sites-available/000-catch-all /etc/nginx/sites-enabled/000-catch-all

sudo cp /opt/trainova/deploy/nginx/trainova.hotbar.pro.conf \
  /etc/nginx/sites-available/trainova.hotbar.pro
sudo ln -sfn /etc/nginx/sites-available/trainova.hotbar.pro \
  /etc/nginx/sites-enabled/trainova.hotbar.pro

# IPv6 у сервера нет — убрать строки [::], иначе nginx -t упадёт на обоих файлах
ip -6 addr show scope global | grep -q inet6 \
  || sudo sed -i '/listen \[::\]:80/d' /etc/nginx/sites-available/000-catch-all \
       /etc/nginx/sites-available/trainova.hotbar.pro

sudo nginx -t && sudo systemctl reload nginx
sudo nginx -T | grep -E '^\s*listen .*default_server' | sort | uniq -c   # только заглушка, без дублей
```

Проверка пути nginx → gunicorn, ещё без TLS и без DNS:

```bash
# новый сервер
curl -sI -H "Host: trainova.hotbar.pro" http://127.0.0.1/ | head -3     # 301 на https от Django

# своя машина: домен ещё на старом сервере, поэтому адрес подставляем руками
curl -sI --resolve "trainova.hotbar.pro:80:$NEW_IP" http://trainova.hotbar.pro/ | head -1
# 301 на https — тот же ответ, что и локально
```

И ребут — единственная честная проверка, что машина вернётся целой после ночного
`unattended-upgrade`: своп поднимется из fstab, контейнеры встанут сами
(`restart: unless-stopped`), nginx стартует.

```bash
# новый сервер
sudo reboot            # ssh-сессия оборвётся, это нормально

# своя машина, через минуту
ssh trainova-new

# новый сервер, после ребута
sudo -u deploy /opt/trainova/scripts/prod.sh ps        # контейнеры поднялись сами
swapon --show; systemctl is-active docker nginx
curl -sI -H "Host: trainova.hotbar.pro" -H "X-Forwarded-Proto: https" \
  http://127.0.0.1:8000/accounts/login/ | head -1
```

Выяснять это сейчас, на пустой базе, дешевле, чем в первую ночь с боевыми данными.

## 5. Точка невозврата: заморозка старого сервера и перенос данных

Точка невозврата — остановка web на старом сервере, а не переключение DNS. С этой минуты
дамп фиксирует состояние, и всё написанное позже потеряется. Дальше — без пауз.

Крон бэкапа гасится **первым**: иначе он продолжит каждую ночь класть в **тот же**
rclone-remote дампы замороженной базы, и через неделю в списке «свежих» окажется устаревший
файл. Это самая тихая ошибка при переезде.

```bash
# своя машина (из сессии нового сервера сначала exit) → живая сессия старого:
# sudo здесь просит пароль, поэтому нужен tty
ssh trainova-old
sudo rm -f /etc/cron.d/trainova-backup
cd /opt/trainova && sudo -u deploy ./scripts/prod.sh stop web
sudo -u deploy ./scripts/prod.sh ps           # web: Exited, db: Up (healthy)
```

Останавливаем web, а не весь стек: база нужна живой для `pg_dump`.

Дальше гасим фронт, чтобы клиент с закэшированным DNS не попал на старый сервер и не записал
туда то, чего не будет нигде. Пусть лучше получит ошибку соединения — громкий отказ честнее
тихой потери данных. Вариантов два, **выполнить нужно ровно один**:

```bash
ls -l /etc/nginx/sites-enabled/     # только наш сайт или есть соседи?
```

Вариант А, машина целиком наша. `disable` — чтобы ночной автообновляющий ребут не поднял
фронт обратно:

```bash
sudo systemctl stop nginx && sudo systemctl disable nginx
```

Вариант Б, на машине есть соседние сайты (deploy.md говорит, что nginx обслуживает не только
нас). Снимаем только наш vhost, иначе положите чужие сайты на все две недели:

```bash
sudo rm -f /etc/nginx/sites-enabled/trainova.hotbar.pro
sudo nginx -t && sudo systemctl reload nginx     # домен уйдёт в catch-all `return 444`
```

Таймер certbot на старом сервере не трогаем: сертификат там остаётся валидным и держит откат,
а продлевать ему после переключения DNS всё равно не удастся. Выключается он в §10, при
выводе сервера.

Эталонные счётчики снимаются **здесь**, на уже замороженной базе, а не заранее: между
подготовкой и остановкой web в базу могли записать подход, и расхождение было бы законным.
Имена `-U trainova -d trainova` ниже — значения по умолчанию; если в `.env.prod` другие
`POSTGRES_USER`/`POSTGRES_DB`, подставьте свои.

```bash
# старый сервер, та же сессия
sudo -u deploy ./scripts/prod.sh exec -T db psql -U trainova -d trainova -tAc "
select 'users', count(*) from accounts_user
union all select 'workouts', count(*) from workouts_workout
union all select 'sets', count(*) from workouts_strengthset
union all select 'sports', count(*) from workouts_sport
union all select 'exercises', count(*) from workouts_exercise
union all select 'locations', count(*) from workouts_location
union all select 'notes', count(*) from workouts_exercisenote
union all select 'cardio', count(*) from workouts_cardiodetails
union all select 'settings', count(*) from workouts_exercisesettings
union all select 'changelog', count(*) from workouts_changelogentry
union all select 'emails', count(*) from account_emailaddress
union all select 'sessions', count(*) from django_session
union all select 'migrations', count(*) from django_migrations
order by 1" | tee /tmp/counters-old.txt
```

Дамп — под строгой маской: перенаправление выполняет ваша оболочка, и без `umask` файл ляжет
с правами 644, а в нём email'ы и хэши паролей всех пользователей.

```bash
# старый сервер, та же сессия
umask 077
sudo -u deploy ./scripts/prod.sh exec -T db pg_dump -U trainova -d trainova -Fc > /tmp/final.dump
sudo -u deploy ./scripts/prod.sh exec -T db pg_restore --list < /tmp/final.dump | head
sha256sum /tmp/final.dump | tee /tmp/final.sha256
```

`pg_restore --list` ничего не восстанавливает: он читает заголовок и оглавление и доказывает,
что файл — дамп, а не обрезок. Данные целиком он не читает, поэтому дальше сверяем sha256 на
каждом плече.

Штатный `./scripts/backup.sh` тут тоже сгодится, но он потянет ещё выгрузку в облако с
ротацией — на точке невозврата лишние движения не нужны.

```bash
# своя машина — БЕЗ ssh -t: псевдотерминал испортит бинарный дамп
umask 077
ssh trainova-old 'cat /tmp/final.dump' > /tmp/final.dump
sha256sum /tmp/final.dump; ssh trainova-old 'cat /tmp/final.sha256'   # первое плечо
scp /tmp/final.dump trainova-new:/tmp/final.dump
scp trainova-old:/tmp/counters-old.txt /tmp/counters-old.txt

# новый сервер
sha256sum /tmp/final.dump      # второе плечо: та же сумма, что на старом сервере
sudo install -d -o root -g root -m 755 /var/backups/trainova
sudo install -o deploy -g deploy -m 600 /tmp/final.dump \
  /var/backups/trainova/dnevnik-$(date +%F-%H%M).dump
sudo -u deploy bash -c 'sha256sum /var/backups/trainova/dnevnik-*.dump'   # третье плечо
shred -u /tmp/final.dump
```

Каталог остаётся `755 root:root` — таким его создаёт `backup.sh`, и в нём вы сами можете
делать `ls`. Закрыт сам файл дампа. Копии на своей машине и на старом сервере затираем:
старый сервер простоит ещё две недели с открытым ssh.

```bash
shred -u /tmp/final.dump                                     # своя машина
ssh trainova-old 'shred -u /tmp/final.dump /tmp/final.sha256'
```

Восстановление:

```bash
# новый сервер
cd /opt/trainova
DUMP=$(ls -t /var/backups/trainova/dnevnik-*.dump | head -1); echo "$DUMP"
sudo -u deploy ./scripts/restore.sh "$DUMP" 2>&1 | tee /tmp/restore.log
```

Скрипт покажет, что сейчас в базе, спросит подтверждение (ввести буквально `да`), остановит
web, накатит дамп, прогонит `migrate --check` и поднимет web. Предупреждения вида
`must be owner of extension plpgsql` безобидны; строки про `relation ... already exists`,
`duplicate key` или `invalid command \N` — нет.

Если `migrate --check` упал, скрипт остановится и web останется лежать: значит дамп старее
кода. На новом сервере том одноразовый, поэтому чище пересоздать его, чем откатывать код:

```bash
sudo -u deploy ./scripts/prod.sh down -v
sudo -u deploy ./scripts/prod.sh up -d --wait db   # --wait: ждём healthy, иначе попадём в initdb
sudo -u deploy bash -c "/opt/trainova/scripts/prod.sh exec -T db pg_restore \
  -U trainova -d trainova --no-owner --no-privileges < '$DUMP'"
sudo -u deploy ./scripts/prod.sh up -d web      # entrypoint накатит недостающие миграции вперёд
```

Путь «`git checkout` старого коммита → восстановить → `git pull`» на свежем сервере хуже:
таблицы, созданные новыми миграциями, `--clean` не дропнет (их нет в архиве), и следующий
`migrate` упрётся в `relation already exists`.

Когда восстановление прошло — убедитесь, что приложение поднялось обратно:

```bash
sudo -u deploy ./scripts/prod.sh ps                    # web снова Up
curl -sI -H "Host: trainova.hotbar.pro" -H "X-Forwarded-Proto: https" \
  http://127.0.0.1:8000/accounts/login/ | head -1      # 200
```

Сверка счётчиков — файлами, а не глазами. Запрос обязан быть тем же самым, иначе строки не
сойдутся по порядку:

```bash
sudo -u deploy ./scripts/prod.sh exec -T db psql -U trainova -d trainova -tAc "
select 'users', count(*) from accounts_user
union all select 'workouts', count(*) from workouts_workout
union all select 'sets', count(*) from workouts_strengthset
union all select 'sports', count(*) from workouts_sport
union all select 'exercises', count(*) from workouts_exercise
union all select 'locations', count(*) from workouts_location
union all select 'notes', count(*) from workouts_exercisenote
union all select 'cardio', count(*) from workouts_cardiodetails
union all select 'settings', count(*) from workouts_exercisesettings
union all select 'changelog', count(*) from workouts_changelogentry
union all select 'emails', count(*) from account_emailaddress
union all select 'sessions', count(*) from django_session
union all select 'migrations', count(*) from django_migrations
order by 1" > /tmp/counters-new.txt
# со своей машины
scp trainova-new:/tmp/counters-new.txt /tmp/counters-new.txt
diff /tmp/counters-old.txt /tmp/counters-new.txt && echo 'СОВПАЛО ПОЛНОСТЬЮ'
```

Единственная строка, которой позволено разойтись, — `migrations`: если код на новом сервере
новее, недостающие миграции применятся вперёд и счётчик вырастет. Все остальные обязаны
совпасть точно.

Дополнительно стоит убедиться, что последовательности не отстали от данных — иначе первая же
новая тренировка упадёт на дубле первичного ключа:

```bash
sudo -u deploy ./scripts/prod.sh exec -T db psql -U trainova -d trainova -c \
  "select max(id), (select last_value from workouts_workout_id_seq) from workouts_workout"
sudo -u deploy ./scripts/prod.sh exec -T db psql -U trainova -d trainova -c \
  "select id, started_at, duration_min from workouts_workout order by id desc limit 5"
```

Ждём `last_value` не меньше `max(id)`. Если меньше — последовательность отстала и чинится
одной строкой (так же для остальных таблиц):

```sql
select setval(pg_get_serial_sequence('workouts_workout','id'),
              (select max(id) from workouts_workout));
```

Даты последних тренировок должны совпадать с реальностью старого сервера, а не быть
сегодняшними.

Почту проверяем **до** переключения DNS — отправка от домена не зависит, а блокировка
исходящего SMTP у нового хостера иначе вскроется на живых пользователях:

```bash
sudo -u deploy ./scripts/prod.sh exec -T web python manage.py sendtestemail вы@example.com
sudo -u deploy ./scripts/prod.sh logs web --tail 30 | grep -i smtp
```

## 6. Переключение домена и выпуск сертификата

Сертификата на новом сервере ещё нет, и получить его заранее нельзя: HTTP-01-проверка требует,
чтобы Let's Encrypt достучался до `http://trainova.hotbar.pro/.well-known/acme-challenge/…`,
то есть чтобы домен уже указывал сюда. Поэтому порядок такой: переключить DNS → сразу выпустить.

К этому моменту всё должно быть готово: `python3-certbot-nginx` установлен, HTTP-конфиг и
заглушка лежат, 80-й порт открыт в ufw, база восстановлена.

В панели DNS меняем **обе** записи: `A` — на адрес нового сервера, `AAAA` — на его IPv6, а
если IPv6 у сервера нет (проверяли в §4), запись **удалить**. Оставленная AAAA уведёт часть
клиентов по IPv6 на замороженный сервер, и заметите вы это не сразу.

```bash
# своя машина: все авторитативные NS зоны отдают новый адрес
for ns in $(dig +short NS hotbar.pro); do echo "$ns $(dig +short @$ns trainova.hotbar.pro A)"; done
```

Ждать публичных резолверов не нужно: Let's Encrypt спрашивает авторитативные серверы зоны
напрямую, а TTL 300 из §0 ограничивает кэш пятью минутами. Как только все NS отдают новый
адрес — убеждаемся, что снаружи 80-й порт доходит до нашего vhost'а, и идём в УЦ:

```bash
# своя машина: куда реально попадает обычный клиент
curl -s -o /dev/null -w 'ip=%{remote_ip} код=%{http_code}\n' http://trainova.hotbar.pro/
# ждём: ip = адрес нового сервера, код = 301 (редирект от Django через наш nginx).
# Показывает старый адрес или код=000 — это кэш вашего резолвера, повторите через 1-5 минут;
# сам сервер при этом можно проверить в обход DNS:
#   curl -s -o /dev/null -w '%{http_code}\n' --resolve "trainova.hotbar.pro:80:$NEW_IP" \
#     http://trainova.hotbar.pro/

# новый сервер
sudo certbot --nginx -d trainova.hotbar.pro \
  -m "вы@example.com" --agree-tos --no-eff-email --redirect
sudo nginx -t && sudo systemctl reload nginx
```

Проверка курлом стоит отдельным шагом потому, что у боевого УЦ лимит **5 неудачных проверок
в час на домен**: дешевле убедиться заранее, чем сжечь попытки. Проверять файлом в
`/var/www/html` бессмысленно — в нашем vhost'е только `location /` с `proxy_pass`, дефолтный
сайт Ubuntu с его `root` уже снят в §4, и до диска запрос не дойдёт. Зато 301 доказывает
нужное: домен снаружи доходит до нашего nginx и до приложения.

Флаги `-m … --agree-tos --no-eff-email` убирают три вопроса, которые certbot задаёт на
сервере без ACME-аккаунта (почта, согласие с условиями, рассылка EFF), `--redirect` сразу
дописывает редирект с 80-го порта. Сомневаетесь — сначала прогон на тестовом УЦ, он лимитов
боевого не тратит:

```bash
sudo certbot certonly --nginx --test-cert --cert-name probe-test -d trainova.hotbar.pro \
  -m "вы@example.com" --agree-tos --no-eff-email
sudo certbot delete --cert-name probe-test     # спросит подтверждение — ответить y
```

Сам `.well-known` обслужит плагин: на время валидации он вставляет в блок сайта
`location ^~ /.well-known/acme-challenge/`, а префикс `^~` выигрывает у `/` — запрос УЦ до
Django не доходит, и `SECURE_SSL_REDIRECT` его не перенаправляет.

Проверка:

```bash
# новый сервер
sudo certbot certificates                      # VALID, ~90 дней, issuer Let's Encrypt
sudo certbot renew --dry-run
sudo systemctl enable --now certbot.timer && systemctl list-timers certbot.timer --no-pager

# своя машина
curl -sI https://trainova.hotbar.pro/accounts/login/ | sed -n '1p;/strict-transport-security/Ip'
curl -sI http://trainova.hotbar.pro/ | sed -n '1p;/^location/Ip'
curl -s -o /dev/null -w 'ip=%{remote_ip} код=%{response_code}\n' https://trainova.hotbar.pro/accounts/login/
```

Ожидаем 200 с валидным TLS (**без** `-k`) и заголовком `strict-transport-security`, по HTTP —
301 на https, `ip=` равен новому адресу.

**Если окно без HTTPS неприемлемо** (HSTS в год, а понижать его не хочется) — сертификат можно
получить заранее через DNS-01: проверка по TXT-записи не зависит от того, куда указывает A.
Эти команды выполняются **вместо** блока выше и **до** переключения DNS — то есть сразу после
§4, пока домен ещё смотрит на старый сервер.

```bash
# новый сервер
sudo certbot certonly --manual --preferred-challenges dns -d trainova.hotbar.pro \
  -m "вы@example.com" --agree-tos --no-eff-email
# добавить названный TXT _acme-challenge.trainova.hotbar.pro и дождаться его:
#   dig +short TXT _acme-challenge.trainova.hotbar.pro @8.8.8.8
sudo certbot install --cert-name trainova.hotbar.pro --nginx --redirect
```

На этом пути `certbot renew --dry-run` и включение таймера из блока «Проверка» делайте не
сразу, а после разового перевыпуска через плагин nginx (см. ниже): у `--manual` репетиция
продления не проходит по определению.

Расплата: `--manual` без hook-скрипта автоматически не продлевается, поэтому после
переключения DNS нужно один раз перевыпустить через плагин nginx
(`sudo certbot certonly --nginx --cert-name trainova.hotbar.pro -d trainova.hotbar.pro
--force-renewal`), чтобы в `renewal/*.conf` встал `authenticator = nginx`. Лимит дубликатов —
5 в неделю на один и тот же набор имён, одна лишняя выдача в него укладывается.

Сразу после переключения закрыть старый сервер, но **не удалять** — это откат:

```bash
# своя машина (из сессии нового сервера сначала exit)
ssh trainova-old
sudo -u deploy /opt/trainova/scripts/prod.sh down    # БЕЗ -v: тома с базой сохраняются
sudo ufw deny 80 && sudo ufw deny 443                # только если сервер целиком наш
```

Пока живёт откат, на старом сервере нельзя две вещи: `docker system prune --volumes` (после
`down` том числится неиспользуемым, и prune унесёт базу) и `certbot revoke` (он отзовёт
сертификат, который старый сервер предъявит при откате).

## 7. Пост-настройка

Крон бэкапов. Права 644 и владелец root обязательны — иначе cron файл молча игнорирует:

```bash
# новый сервер (если вы ещё в сессии старого — exit)
sudo install -o root -g root -m 644 \
  /opt/trainova/deploy/cron/trainova-backup /etc/cron.d/trainova-backup
sudo /opt/trainova/scripts/backup.sh
sudo tail -20 /var/log/trainova-backup.log
REMOTE=$(sudo grep -E '^BACKUP_RCLONE_REMOTE=' /opt/trainova/.env.prod | cut -d= -f2- | tr -d '"')
sudo rclone lsl "$REMOTE" | tail -3
```

Пометка про машину здесь не формальность: поставленный на старом сервере крон-файл
воспроизведёт ровно ту тихую ошибку, от которой предостерегает §5 — два сервера в один
rclone-remote.

`backup.sh` после выгрузки делает `rclone lsf` — убеждается, что файл в облаке есть. Для
crypt-remote этого мало: листинг проходит и при неверном пароле шифрования. Поэтому один раз
прочитайте дамп обратно:

```bash
sudo bash -c 'LAST=$(rclone lsf "'"$REMOTE"'" --include "dnevnik-*.dump" | sort | tail -1)
  rclone copy "'"$REMOTE"'/$LAST" /root/verify/ && ls -lh /root/verify/'
sudo bash -c 'shred -u /root/verify/*.dump && rmdir /root/verify'   # копия была нужна только для проверки
sudo ls -la /root/verify 2>&1 | tail -1     # «No such file or directory» — расшифрованного дампа не осталось
```

Секрет `DEPLOY_KNOWN_HOSTS` — у нового сервера другой host key, и без обновления деплой
упадёт на проверке отпечатка. Так и задумано: known_hosts берётся из секрета именно затем,
чтобы подменённый DNS не увёл деплой на чужой сервер.

```bash
# своя машина: ключ берём с самого сервера, строка обязана начинаться с домена
gh auth status      # не авторизован — gh auth login; секрет можно вписать и через веб-интерфейс
ssh trainova-new "awk '{print \"trainova.hotbar.pro \" \$1 \" \" \$2}' /etc/ssh/ssh_host_ed25519_key.pub" \
  | tee /tmp/known_hosts_new
ssh-keygen -R trainova.hotbar.pro                  # выкинуть ключ старого сервера
cat /tmp/known_hosts_new >> ~/.ssh/known_hosts     # положить новый ДО проверки с BatchMode
ssh -i ~/.ssh/trainova_deploy -o BatchMode=yes deploy@trainova.hotbar.pro \
  'git -C /opt/trainova rev-parse --short HEAD'
gh secret set DEPLOY_KNOWN_HOSTS --repo romanpaltsev/trainova < /tmp/known_hosts_new
```

`ssh-keyscan -t ed25519 trainova.hotbar.pro` даёт то же значение, но только после того, как
домен переключён и адрес дошёл до вашей машины; файл на сервере — первоисточник.
`DEPLOY_HOST`, `DEPLOY_USER` и `DEPLOY_SSH_KEY` менять не нужно: host — это домен. На время
отката в секрете можно держать две строки, старого и нового сервера: ssh примет любой
совпавший ключ.

Проверка автодеплоя — запрет на push в `main` снимается только сейчас:

```bash
# своя машина
git commit --allow-empty -m "проверка автодеплоя после переезда на новый VDS" && git push origin main

# ищем прогон именно своего коммита, а не предыдущий
SHA=$(git rev-parse HEAD)
until RUN=$(gh run list --branch main --limit 5 --json databaseId,headSha \
       -q ".[] | select(.headSha==\"$SHA\") | .databaseId" | head -1); [ -n "$RUN" ]; do sleep 5; done
gh run watch "$RUN" --exit-status
gh run view "$RUN" --log | grep -n 'приложение отвечает'
```

## 8. Проверки после переезда

| Что | Как | Ожидание |
|---|---|---|
| HTTPS | `curl -I https://trainova.hotbar.pro/accounts/login/` | 200, есть `strict-transport-security` |
| Редирект | `curl -I http://trainova.hotbar.pro/` | 301 на https |
| Сессии пережили | открыть в браузере, где были залогинены до переезда | остались залогинены — значит `SECRET_KEY` не менялся |
| Данные | история тренировок, подходы, места, «Что нового» | счётчики совпали в §5 |
| Статика | Ctrl+F5, вкладка Network | CSS/JS 200, имена с хэшами |
| Регистрация | зарегистрировать тестовый адрес | письмо подтверждения дошло, ссылка работает |
| Логи | `sudo -u deploy /opt/trainova/scripts/prod.sh logs web \| tail -50`, `/var/log/nginx/error.log` | без трейсбеков и ошибок SMTP |
| Сеть | `./scripts/check_speed.sh` со своей машины | ноль зависаний дольше 2 с |

Счётчики после переключения сверяйте не на точное равенство: `sessions` вырастет на ваши
входы в браузере, `migrations` — если код новее дампа. Остальные обязаны совпасть с эталоном
из §5.

Повторить `./scripts/check_speed.sh` вечером и на следующий день — ради вечернего замера
переезд и затевался. Наутро заодно посмотреть, что ночной крон отработал сам:

```bash
ssh trainova-new 'tail -20 /var/log/trainova-backup.log'   # запись «=== готово» около 04:17
```

Через сутки после переключения верните TTL записей с 300 на прежнее значение.

## 9. Откат

Старый сервер держать минимум **14 дней**, том с базой не удалять.

Если сломались сразу, пока на новом никто ничего не записал: вернуть A (и AAAA) на старый IP
и поднять там всё обратно. Сертификат на старом сервере остался нетронутым, поэтому TLS
поднимется сразу и HSTS не помешает. TTL уже 300 — ждать минуты.

```bash
# старый сервер
sudo ufw allow 80 && sudo ufw allow 443
sudo -u deploy /opt/trainova/scripts/prod.sh up -d
sudo ln -sfn /etc/nginx/sites-available/trainova.hotbar.pro /etc/nginx/sites-enabled/trainova.hotbar.pro
sudo nginx -t && sudo systemctl enable --now nginx && sudo systemctl reload nginx
sudo install -o root -g root -m 644 \
  /opt/trainova/deploy/cron/trainova-backup /etc/cron.d/trainova-backup
```

И сразу же снять крон бэкапа на **новом** сервере (`sudo rm -f /etc/cron.d/trainova-backup`),
иначе два сервера будут писать дампы в один и тот же rclone-remote.

Если прошло время и на новом уже есть новые записи — откат превращается в переезд в обратную
сторону теми же шагами §5 с обменом ролями.

Откатываться не надо, если проблема оказалась в сети нового хостера: старый покидается ровно
поэтому. Тогда ищут третий сервер, а не возвращаются на второй.

## 10. Вывод старого сервера

Не раньше чем через две недели и после хотя бы одного успешного ночного бэкапа с нового
сервера, доехавшего в облако.

1. Убедиться, что в облаке есть свежие дампы **с нового** сервера.
2. Снять со старого последний архивный дамп и положить рядом с `.env.prod` — страховка на
   случай «данные пропали, а заметили через месяц».
3. Затереть на старом сервере секреты и дампы (`shred -u`): диск уедет обратно хостеру.
4. Выключить продление: `sudo systemctl disable --now certbot.timer`. Отзывать сертификат
   (`certbot revoke`) не нужно — он просто истечёт.
5. Убрать ссылки на старый адрес: DNS (A, AAAA), `~/.ssh/config`, мониторинг, файрволы.
6. Погасить VDS и закрыть услугу. Если машина остаётся ради соседних сайтов — удалить только
   наше: vhost, `/etc/cron.d/trainova-backup`, `prod.sh down -v`, `/opt/trainova`,
   пользователя `deploy`.
7. `ssh-keygen -R <старый IP>` и `ssh-keygen -R trainova.hotbar.pro` на своей машине —
   иначе следующее подключение встретит предупреждение о смене host key.
