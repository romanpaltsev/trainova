#!/usr/bin/env bash
# Первичное получение сертификата Let's Encrypt.
#
# Проблема курицы и яйца: nginx не стартует без файлов сертификата, а certbot не
# может пройти проверку домена без работающего nginx. Поэтому сначала кладём
# самоподписанную заглушку, поднимаем nginx, получаем настоящий сертификат и
# перезагружаем конфиг. Запускается один раз при первом деплое.
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="./scripts/prod.sh"

if [[ ! -f .env.prod ]]; then
  echo "Нет .env.prod — скопируйте .env.prod.example и заполните." >&2
  exit 1
fi
# shellcheck disable=SC1091
source ./scripts/env.sh

DOMAIN="$(env_require DOMAIN)"
LETSENCRYPT_EMAIL="$(env_require LETSENCRYPT_EMAIL)"
STAGING="$(env_get LETSENCRYPT_STAGING 0)"
CERT_PATH="/etc/letsencrypt/live/${DOMAIN}"

echo "==> Домен: ${DOMAIN}"

if $COMPOSE run --rm --entrypoint sh certbot -c "[ -f ${CERT_PATH}/fullchain.pem ]" 2>/dev/null; then
  echo "Сертификат уже есть, ничего не делаю. Для перевыпуска удалите том certbot_conf."
  exit 0
fi

echo "==> Кладу самоподписанную заглушку, чтобы nginx смог стартовать"
$COMPOSE run --rm --entrypoint sh certbot -c "
  mkdir -p '${CERT_PATH}' &&
  openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout '${CERT_PATH}/privkey.pem' \
    -out '${CERT_PATH}/fullchain.pem' \
    -subj '/CN=${DOMAIN}' >/dev/null 2>&1"

echo "==> Поднимаю nginx"
$COMPOSE up -d nginx
sleep 3

echo "==> Запрашиваю сертификат у Let's Encrypt"
STAGING_FLAG=""
[[ "$STAGING" != "0" ]] && STAGING_FLAG="--staging"

# Заглушку удаляем перед выпуском: иначе certbot решит, что серт уже есть.
$COMPOSE run --rm --entrypoint sh certbot -c "rm -rf '${CERT_PATH}' '/etc/letsencrypt/archive/${DOMAIN}' '/etc/letsencrypt/renewal/${DOMAIN}.conf'"

$COMPOSE run --rm --entrypoint certbot certbot certonly \
  --webroot -w /var/www/certbot \
  ${STAGING_FLAG} \
  -d "${DOMAIN}" \
  --email "${LETSENCRYPT_EMAIL}" \
  --agree-tos --no-eff-email \
  --non-interactive

echo "==> Перезагружаю nginx с настоящим сертификатом"
$COMPOSE exec nginx nginx -s reload

echo "Готово. Сертификат выпущен, продление делает контейнер certbot."
