# Деплой на VDS

Стек: **nginx** (TLS, редирект, ACME) → **gunicorn** (Django, статику отдаёт whitenoise)
→ **postgres**. Сертификат Let's Encrypt выпускает и продлевает **certbot**, вызываемый из
крона. Наружу открыты только 80 и 443.

Все команды прод-стека идут через обёртку `./scripts/prod.sh` — она добавляет
`--env-file .env.prod` и нужный compose-файл. Без `--env-file` подстановка `${DOMAIN}`
в compose не работает, и nginx поднимется с пустым `server_name`.

## Почему статику отдаёт whitenoise, а не nginx

- Статика лежит внутри контейнера web (`collectstatic` при старте) — nginx не нужен общий
  том, и невозможна рассинхронизация «код обновили, а nginx отдаёт старые файлы».
- Имена файлов содержат хэш содержимого, поэтому кэш выставлен на год, а рядом лежат
  заранее пожатые `.br`/`.gz` — отдача почти не стоит воркеру времени.
- Для дневника на несколько человек разница между nginx и whitenoise не измеряется,
  а движущихся частей на одну меньше.

Если позже понадобится отдавать статику nginx — добавляется том `staticfiles` и один
`location /static/`, код при этом не меняется.

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
# от root
adduser roman && usermod -aG sudo roman
# дальше — под roman
sudo apt update && sudo apt -y upgrade
sudo apt -y install docker.io docker-compose-v2 git rclone
sudo usermod -aG docker roman && newgrp docker

sudo apt -y install unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades

sudo ufw allow OpenSSH && sudo ufw allow 80 && sudo ufw allow 443
sudo ufw enable
```

## 3. Код и окружение

```bash
sudo mkdir -p /opt/trainova && sudo chown roman:roman /opt/trainova
git clone https://github.com/romanpaltsev/trainova.git /opt/trainova
cd /opt/trainova

cp .env.prod.example .env.prod
python3 -c "import secrets; print(secrets.token_urlsafe(64))"   # ключ для DJANGO_SECRET_KEY
nano .env.prod
```

Обязательно поменяйте: `DJANGO_SECRET_KEY`, `DOMAIN`, `DJANGO_ALLOWED_HOSTS`,
`CSRF_TRUSTED_ORIGINS`, `LETSENCRYPT_EMAIL`, `POSTGRES_PASSWORD` (и тот же пароль внутри
`DATABASE_URL`), `EMAIL_URL`, `DEFAULT_FROM_EMAIL`.

Про `EMAIL_URL` для Яндекса:

```
EMAIL_URL=smtp+ssl://dnevnik%40yandex.ru:parolprilozheniya@smtp.yandex.ru:465
```

- `@` в логине пишется как `%40`, спецсимволы пароля тоже %-кодируются;
- `DEFAULT_FROM_EMAIL` обязан совпадать с этим ящиком, иначе Яндекс отклонит отправку;
- значения с пробелами и `<>` берите в кавычки.

`.env.prod` закрыт `.gitignore` и остаётся только на сервере.

## 4. Сертификат и первый запуск

```bash
./scripts/prod.sh build
./scripts/init_letsencrypt.sh     # заглушка → nginx → настоящий серт → reload
./scripts/prod.sh up -d
./scripts/prod.sh ps
```

`init_letsencrypt.sh` решает проблему курицы и яйца: nginx не стартует без файлов
сертификата, а certbot не пройдёт проверку домена без работающего nginx.

Сомневаетесь в DNS — поставьте `LETSENCRYPT_STAGING=1`, прогоните выпуск на тестовом УЦ
(браузер такому серту не поверит, это нормально), потом верните `0`, удалите том
`docker volume rm trainova-prod_certbot_conf` и повторите.

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

## 8. Обновление версии

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

## 9. Что где смотреть

```bash
./scripts/prod.sh logs -f web       # gunicorn и Django
./scripts/prod.sh logs -f nginx     # запросы и TLS
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
