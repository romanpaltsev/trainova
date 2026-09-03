#!/usr/bin/env bash
# Проверка сети НА СЕРВЕРЕ. Парная к check_speed.sh, который мерит с клиента.
#
# Зачем нужны обе. check_speed.sh отвечает на вопрос «медленно ли мне», но не
# отличает потери у моего провайдера от потерь у хостера. Этот скрипт смотрит с
# другого конца: он проверяет сервер сам на себе (через localhost, где сети
# между машинами нет вовсе) и потом его же путь наружу к нескольким
# независимым адресам. Если localhost чист, а наружу теряется — виноват не код
# и не настройки, а сеть, и починить её может только хостер.
#
# Запуск на сервере:   ./scripts/check_network.sh
# Или снаружи по ssh:  ssh ваш-хост 'bash -s' < scripts/check_network.sh
set -uo pipefail

RUNS="${RUNS:-20}"          # сколько запросов в каждой серии на localhost
WINDOW="${WINDOW:-60}"      # окно замера переотправок, секунды
PINGS="${PINGS:-30}"        # пакетов на каждый внешний адрес
DOMAIN="${DOMAIN:-trainova.hotbar.pro}"
WEB_PORT="${WEB_PORT:-8000}"
# Три независимых адреса в разных сетях: потери сразу до всех значат, что дело
# не в одном далёком маршруте, а рядом с сервером.
TARGETS="${TARGETS:-8.8.8.8 1.1.1.1 77.88.8.8}"

echo "== Сам себя через localhost: сети между машинами тут нет =="
echo "-- прямо в gunicorn (127.0.0.1:$WEB_PORT), $RUNS новых соединений --"
# Host и X-Forwarded-Proto обязательны: без них Django ответит DisallowedHost
# или уведёт в редирект на https, и замер станет мерить не то.
for _ in $(seq 1 "$RUNS"); do
  curl -s --no-keepalive -o /dev/null -m 20 -w "%{time_starttransfer} " \
    "http://127.0.0.1:$WEB_PORT/accounts/login/" \
    -H "Host: $DOMAIN" -H "X-Forwarded-Proto: https"
done
echo
echo "-- через nginx по TLS на себя же, $RUNS новых соединений --"
# --resolve вместо адреса в URL: имя нужно настоящее, иначе не совпадёт
# server_name и сертификат, а идти запрос всё равно должен на loopback.
for _ in $(seq 1 "$RUNS"); do
  curl -s --no-keepalive -k -o /dev/null -m 20 -w "%{time_starttransfer} " \
    "https://$DOMAIN/accounts/login/" --resolve "$DOMAIN:443:127.0.0.1"
done
echo; echo

echo "== Переотправки за последние $WINDOW с =="
# Именно за окно, а не с момента загрузки: суммарные счётчики за неделю не
# отвечают на вопрос «плохо ли сейчас», а лечить надо то, что происходит сейчас.
counters() { nstat -az TcpRetransSegs TcpOutSegs 2>/dev/null |
  awk '/TcpRetransSegs/{r=$2} /TcpOutSegs/{o=$2} END{print r" "o}'; }
before=$(counters)
sleep "$WINDOW"
after=$(counters)
echo "$before $after" | awk '{
  sent = $4 - $2; retrans = $3 - $1
  printf "  отправлено %d, переотправлено %d", sent, retrans
  if (sent > 0) printf " (%.2f%%)", 100 * retrans / sent
  print ""
  if (sent < 50) print "  (мало трафика для вывода — повторите под нагрузкой)"
}'
echo

echo "== Потери и задержки наружу =="
for host in $TARGETS; do
  printf "  %-12s " "$host"
  # -q даёт две итоговые строки; берём их целиком, без вырезания кусков
  # регуляркой: на разных дистрибутивах формат чуть разный.
  ping -c "$PINGS" -i 0.2 -W 2 -q "$host" 2>&1 | tail -2 | tr '\n' ' ' |
    sed -E 's/.*transmitted, [0-9]+ received, ([0-9.]+)% packet loss.*rtt [^=]*= ([^ ]*).*/потери \1%, rtt мин\/сред\/макс\/разброс \2/'
  echo
done
echo

echo "== Путь наружу по TCP =="
# Именно --tcp: промежуточные хопы отвечают на ICMP по остаточному принципу, и
# обычный traceroute показывает потери там, где их нет. Настоящими считайте
# только те, что наследуются всеми последующими хопами.
if command -v mtr >/dev/null; then
  mtr --tcp -P 443 -r -c 20 -n "${TARGETS%% *}"
else
  echo "  mtr не установлен: apt install mtr-tiny"
fi
echo

echo "== Живые соединения: задержка и доля переотправленных байт =="
# rtt заметно больше minrtt плюс ненулевой bytes_retrans — соединение реально
# страдает, а не просто далеко расположено.
# Разбор построчно, а не grep с paste: у соединения без переотправок поля
# bytes_retrans нет вовсе, и столбцы съезжали бы вперемешку между соединениями.
ss -tin state established '( sport = :443 or sport = :22 )' 2>/dev/null |
  awk '
    /rtt:/ {
      rtt = minrtt = sent = retrans = "—"
      for (i = 1; i <= NF; i++) {
        if ($i ~ /^rtt:/)                { split(substr($i, 5), a, "/"); rtt = a[1] }
        else if ($i ~ /^minrtt:/)        { minrtt = substr($i, 8) }
        else if ($i ~ /^bytes_sent:/)    { sent = substr($i, 12) }
        else if ($i ~ /^bytes_retrans:/) { retrans = substr($i, 15) }
      }
      share = (sent + 0 > 0 && retrans != "—") ? sprintf(" (%.1f%%)", 100 * retrans / sent) : ""
      printf "  rtt %-9s minrtt %-8s отправлено %-8s переотправлено %s%s\n",
             rtt, minrtt, sent, retrans, share
    }' | head -10
echo

cat <<'HINT'
Как читать:
  localhost чист, наружу потери
      → сеть между сервером и миром; ни код, ни nginx, ни sysctl тут не помогут.
        Потери у хостера видит только хостер — это письмо в поддержку с числами
        выше. Разобранный случай и готовый текст письма: docs/deploy.md;
  localhost тоже зависает
      → это уже сервер или приложение: ./scripts/prod.sh logs web, docker stats;
  потери сразу до всех трёх адресов
      → узкое место рядом с сервером, а не на далёком маршруте;
  rtt в разы больше minrtt
      → канал перегружен: пакеты стоят в очереди, а не теряются бесследно.
HINT
