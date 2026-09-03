#!/usr/bin/env bash
# Замер скорости сайта: где теряется время — в приложении или в соединении.
#
# Зачем именно так: типичная жалоба «сайт тормозит» одинаково выглядит и при
# медленном рендере, и при потерях на установке соединения, а лечится
# противоположными способами. Скрипт разделяет эти случаи.
#
# Запуск: ./scripts/check_speed.sh [адрес]
set -uo pipefail

SITE="${1:-https://trainova.hotbar.pro}"
PAGE="$SITE/accounts/login/"   # публичная страница: логин не нужен
CONTROL="https://ya.ru"        # контрольный хост: отделяет проблему сайта от своего канала
RUNS="${RUNS:-12}"
SLOW_AFTER=2                   # секунды, после которых считаем ответ зависшим

host_of() { echo "$1" | sed -E 's|^https?://||; s|/.*$||'; }

echo "Сайт: $SITE"
echo

echo "== По одному соединению (keep-alive): столько стоит сам ответ =="
# Массив, а не строка: в строке curl не разбирает пробелы внутри -w.
args=()
# Порядок «URL, потом его опции»: иначе первый -w печатается до первой передачи и даёт ноль.
for _ in $(seq 1 "$RUNS"); do args+=("$PAGE" -o /dev/null -w '%{time_starttransfer} '); done
curl -s -4 "${args[@]}"
echo; echo

echo "== Новым соединением каждый раз: столько стоит его установить =="
slow=0
for _ in $(seq 1 "$RUNS"); do
  t=$(curl -s -4 --no-keepalive -o /dev/null -m 70 -w "%{time_starttransfer}" "$PAGE")
  printf "%s " "$t"
  awk -v t="$t" -v limit="$SLOW_AFTER" 'BEGIN { exit (t + 0 > limit) ? 1 : 0 }' || slow=$((slow + 1))
done
echo; echo "  зависших дольше ${SLOW_AFTER}с: $slow из $RUNS"
echo

echo "== Фазы одного запроса =="
curl -s -S -4 --no-keepalive -m 70 "$PAGE" -o /dev/null \
  -w "  код %{response_code}  dns %{time_namelookup}  tcp %{time_connect}  tls %{time_appconnect}  ttfb %{time_starttransfer}\n"
echo

echo "== ICMP до сервера =="
ping -c 10 -i 0.3 -W 2 "$(host_of "$SITE")" 2>&1 | tail -2
echo

echo "== Контроль ($CONTROL): если и здесь рвано — дело в вашем канале =="
for _ in $(seq 1 5); do curl -s -4 -o /dev/null -m 70 -w "%{time_starttransfer} " "$CONTROL"; done
echo; echo

cat <<'HINT'
Как читать:
  keep-alive быстрый, новые соединения зависают кратно 16 с
      → теряются пакеты; кратность 16 — шаг ретрансмиссии TCP. Смотрите
        «Диагностика медленных ответов» в docs/deploy.md: первым делом там
        проверка ssh на 22-м порту — если зависает и он, приложение ни при чём;
  в «фазах» стоит tls, а tcp мгновенный
      → ядро соединение приняло, а первый пакет данных не дошёл; это про сеть,
        а не про nginx или Django;
  зависают и keep-alive, и новые
      → медленный сервер или приложение: ./scripts/prod.sh logs web, docker stats;
  контрольный хост тоже рвано
      → проблема в вашей сети, а не на сервере;
  код 000 в «фазах»
      → соединение вообще не установилось, это тот же симптом в крайней форме.

Запустите скрипт и с другой точки (телефон по мобильной сети, другой провайдер):
если зависания видны отовсюду — дело на сервере, если только у вас — в канале.
HINT
