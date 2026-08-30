# Деплой на VDS

Стек: **системный nginx** (TLS, редирект, ACME) → **gunicorn** в докере (Django, статику
отдаёт whitenoise) → **postgres** в докере. Сертификат Let's Encrypt выпускает и продлевает
системный **certbot** (пакет `python3-certbot-nginx`, продление — его собственный
systemd-таймер).

nginx намеренно не в контейнере: на этом VDS он уже стоит и обслуживает другие сайты,
и два nginx не поделят порты 80/443. Приложение публикует порт **только на 127.0.0.1** —
снаружи к нему ходит лишь локальный nginx.

Все команды прод-стека идут через обёртку `./scripts/prod.sh` — она добавляет
`--env-file .env.prod` и нужный compose-файл. Без `--env-file` подстановка `${POSTGRES_*}`
в compose не работает, и postgres не поднимется.

## Почему статику отдаёт whitenoise, а не nginx

- Статика лежит внутри контейнера web (`collectstatic` при старте) — nginx не нужен доступ
  к файлам приложения, и невозможна рассинхронизация «код обновили, а nginx отдаёт старые».
- Имена файлов содержат хэш содержимого, поэтому кэш выставлен на год, а рядом лежат
  заранее пожатые `.br`/`.gz` — отдача почти не стоит воркеру времени.
- Для дневника на несколько человек разница между nginx и whitenoise не измеряется,
  а движущихся частей на одну меньше.

Если позже понадобится отдавать статику самим nginx — примонтируйте `staticfiles` наружу
и добавьте `location /static/` в конфиг; код при этом не меняется.

## 0. Что нужно заранее

- VDS с Ubuntu 24.04 (или Debian 12), 1–2 ГБ RAM хватает.
- Домен и доступ к его DNS.
- Ящик на Яндексе и **пароль приложения** для него (не пароль от аккаунта):
  Яндекс ID → Безопасность → Пароли приложений → Почта.

## 1. DNS

Заведите A-запись на IP сервера (AAAA — если есть IPv6):

```
trainova.hotbar.pro.  A  203.0.113.10
```

Проверьте до выпуска сертификата, иначе certbot израсходует попытки
(лимит боевого УЦ — 5 неудач в час на домен):

```bash
getent hosts trainova.hotbar.pro
# dig +short trainova.hotbar.pro — если установлен dnsutils (в WSL по умолчанию его нет)
```

## 2. Сервер: пользователь, docker, файрвол

```bash
# Админские шаги — под своим пользователем с sudo.
sudo apt update && sudo apt -y upgrade
sudo apt -y install docker.io docker-compose-v2 git rclone nginx python3-certbot-nginx

# Сервисный пользователь для деплоя: вход только по ключу, sudo ему не нужен —
# приватный ключ лежит в секретах GitHub, и права root к нему прилагаться не должны.
sudo adduser --disabled-password --gecos "" deploy
sudo usermod -aG docker deploy

sudo apt -y install unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades

sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443
sudo ufw enable
```

## 3. Код и окружение

```bash
sudo mkdir -p /opt/trainova && sudo chown deploy:deploy /opt/trainova
sudo -u deploy git clone https://github.com/romanpaltsev/trainova.git /opt/trainova
cd /opt/trainova

sudo -u deploy cp .env.prod.example .env.prod
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # ключ для DJANGO_SECRET_KEY
sudo -u deploy nano .env.prod

# В файле пароль базы и SECRET_KEY — читать его должен только deploy
sudo chown deploy:deploy .env.prod && sudo chmod 600 .env.prod
```

Обязательно поменяйте: `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `POSTGRES_PASSWORD` (и тот же пароль внутри `DATABASE_URL`),
`EMAIL_URL`, `DEFAULT_FROM_EMAIL`. Если порт 8000 на сервере занят — задайте `WEB_PORT`.

Про `EMAIL_URL` для Яндекса:

```
EMAIL_URL=smtp+ssl://dnevnik%40yandex.ru:parolprilozheniya@smtp.yandex.ru:465
```

- `@` в логине пишется как `%40`, спецсимволы пароля тоже %-кодируются;
- `DEFAULT_FROM_EMAIL` обязан совпадать с этим ящиком, иначе Яндекс отклонит отправку;
- значения с пробелами и `<>` берите в кавычки.

`.env.prod` закрыт `.gitignore` и остаётся только на сервере.

## 4. Приложение, nginx и сертификат

Сначала поднимаем приложение — оно слушает только localhost:

```bash
./scripts/prod.sh build
./scripts/prod.sh up -d
./scripts/prod.sh ps
curl -sI http://127.0.0.1:8000/accounts/login/    # 200 прямо с сервера
```

Потом отдаём его наружу системным nginx:

```bash
sudo cp deploy/nginx/trainova.conf /etc/nginx/sites-available/trainova
sudo ln -s /etc/nginx/sites-available/trainova /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

В конфиге только HTTP-сервер: блок TLS дописывает certbot, ему нужен работающий
80-й порт для проверки домена.

Пока конфига нет, домен отвечает пустотой (`curl: (52) Empty reply from server`) —
это нормально: запрос попадает в catch-all заглушку `server_name _; return 444`,
которая закрывает соединение для неизвестных доменов. После `reload` nginx выберет
наш блок по точному совпадению `server_name`, заглушка на него больше не влияет.

```bash
sudo certbot --nginx -d trainova.hotbar.pro     # выпустит серт и добавит редирект на https
sudo certbot renew --dry-run                    # репетиция продления
```

Сомневаетесь в DNS — прогоните сначала на тестовом УЦ: тот же вызов с `--dry-run`
или `--test-cert` (браузер такому серту не поверит, это нормально). Боевой УЦ даёт
5 неудач в час на домен.

Продление certbot делает сам (systemd-таймер `certbot.timer`) и перезагружает nginx —
крон для сертификата не нужен, в отличие от бэкапов.

## 5. Первые данные

```bash
./scripts/prod.sh exec web python manage.py createsuperuser
./scripts/prod.sh exec web python manage.py seed      # виды спорта и базовые упражнения
```

Суперпользователь попадёт в `/admin/`, но не в само приложение: подтверждение email
обязательно. Пометить его адрес подтверждённым:

```bash
./scripts/prod.sh exec web python manage.py shell -c "
from allauth.account.models import EmailAddress
from accounts.models import User
u = User.objects.get(email='вы@example.com')
EmailAddress.objects.update_or_create(user=u, email=u.email,
    defaults={'verified': True, 'primary': True})
"
```

## 6. Проверка

```bash
curl -I https://trainova.hotbar.pro/accounts/login/     # 200, есть strict-transport-security
curl -I http://trainova.hotbar.pro/                     # 301 на https
./scripts/prod.sh exec web python manage.py sendtestemail вы@example.com
```

Затем в браузере: регистрация настоящим адресом → письмо «Подтвердите email — Дневник
тренировок» → переход по ссылке → вход. Если письма нет:

```bash
./scripts/prod.sh logs web | tail -50      # ошибки SMTP видны здесь
```

Частые причины: используется пароль аккаунта вместо пароля приложения; `@` в логине не
закодирован как `%40`; `DEFAULT_FROM_EMAIL` не совпадает с ящиком.

## 7. Бэкапы

```bash
sudo mkdir -p /var/backups/trainova
sudo cp deploy/cron/trainova-backup /etc/cron.d/trainova-backup
sudo chown root:root /etc/cron.d/trainova-backup && sudo chmod 644 /etc/cron.d/trainova-backup
```

Крон-файл ставит две задачи: ежедневный бэкап и ежедневную проверку продления
сертификата. Настройка выгрузки в облако и процедура восстановления — в
[backup.md](backup.md).

## 8. Автодеплой через GitHub Actions

Workflow `.github/workflows/ci.yml`: на каждый push и pull request прогоняются ruff,
`manage.py check`, проверка незакоммиченных миграций, тесты и `collectstatic` с манифестом.
Если push пришёл в `main` и проверки зелёные — job `deploy` заходит на сервер по SSH,
делает `git reset --hard origin/main`, пересобирает стек и ждёт, пока приложение ответит.

Деплой-ключ (на своей машине):

```bash
ssh-keygen -t ed25519 -C "github-actions-trainova" -f ~/.ssh/trainova_deploy -N ""
ssh-copy-id -i ~/.ssh/trainova_deploy.pub deploy@trainova.hotbar.pro
ssh-keyscan -t ed25519 trainova.hotbar.pro          # строка для DEPLOY_KNOWN_HOSTS
cat ~/.ssh/trainova_deploy                           # приватный ключ целиком, с BEGIN/END
```

Secrets в GitHub (Settings → Secrets and variables → Actions → Secrets):

| Секрет | Значение |
|---|---|
| `DEPLOY_HOST` | `trainova.hotbar.pro` |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | содержимое `~/.ssh/trainova_deploy` (приватный ключ) |
| `DEPLOY_KNOWN_HOSTS` | вывод `ssh-keyscan -t ed25519 trainova.hotbar.pro` |

Если код лежит не в `/opt/trainova`, задайте переменную (не секрет) `DEPLOY_PATH`
в том же разделе, вкладка Variables.

Отпечаток сервера берётся из секрета, а не через `ssh-keyscan` во время деплоя:
иначе подменённый DNS-ответ увёл бы деплой (вместе с ключом) на чужой сервер.

Пользователь `deploy` должен уметь `docker` без sudo (`usermod -aG docker deploy`) и владеть
каталогом с кодом — иначе `git reset --hard` в деплое упадёт на правах. Если репозиторий
клонировали под собой, передайте владение: `sudo chown -R deploy:deploy /opt/trainova`.
`.env.prod` в git не попадает, деплой его не трогает.

## 9. Обновление версии вручную

Обычно это делает GitHub Actions (раздел 8). Руками — если Actions недоступен
или нужен откат:

```bash
cd /opt/trainova
./scripts/backup.sh                # перед обновлением — свежий дамп
git pull
./scripts/prod.sh build
./scripts/prod.sh up -d            # контейнер при старте сам применит миграции
./scripts/prod.sh logs -f web      # убедиться, что gunicorn поднялся
```

Откат: `git checkout <прошлый-коммит> && ./scripts/prod.sh build && ./scripts/prod.sh up -d`.
Если новая версия успела применить миграции, откат кода сам их не отменит — восстанавливайте
базу из дампа по [backup.md](backup.md).

## 10. Что где смотреть

```bash
./scripts/prod.sh logs -f web       # gunicorn и Django
sudo journalctl -u nginx -f         # системный nginx: запросы и TLS
sudo tail -f /var/log/nginx/error.log
./scripts/prod.sh logs -f db        # postgres
tail -f /var/log/trainova-backup.log
tail -f /var/log/trainova-cert.log
docker system df                    # место под образы и тома
```

Полный перезапуск: `./scripts/prod.sh restart`. Остановка: `./scripts/prod.sh down`
(тома с базой и сертификатами при этом сохраняются).

## Известные ограничения

- Первый запуск с `DJANGO_SECURE_HSTS_SECONDS` больше нуля «прибивает» домен к HTTPS
  в браузерах на это время. В примере стоит 3600 (час) — увеличивайте до года, когда
  убедитесь, что всё работает.
- Стек рассчитан на один сервер. Горизонтальное масштабирование потребует вынести
  postgres и статику наружу — для этого проекта не планируется.
- nginx и certbot живут в системе, а не в стеке: значит их обновления и конфиги —
  отдельная от проекта зона ответственности (`apt`, `/etc/nginx`). Взамен на сервере
  спокойно соседствуют другие сайты.
