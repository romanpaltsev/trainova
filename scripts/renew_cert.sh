#!/usr/bin/env bash
# Продление сертификата Let's Encrypt.
#
# certbot renew ничего не делает, если до истечения больше 30 дней, поэтому
# запускать можно хоть ежедневно. После обновления файлов nginx обязан
# перечитать конфиг — сам он этого не сделает.
set -euo pipefail

cd "$(dirname "$0")/.."

if ./scripts/prod.sh run --rm --entrypoint certbot certbot renew \
     --webroot -w /var/www/certbot --quiet; then
  ./scripts/prod.sh exec nginx nginx -s reload
  echo "$(date '+%Y-%m-%d %H:%M:%S') продление проверено, nginx перечитал конфиг"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') ОШИБКА продления сертификата" >&2
  exit 1
fi
