#!/usr/bin/env bash
# Отчёт о потерях пакетов для техподдержки хостинга. Запускается НА СЕРВЕРЕ.
#
# Чем отличается от check_network.sh: тот отвечает «что у нас происходит» и
# пишет в терминал, а этот собирает файл, который можно приложить к тикету.
# Отсюда три особенности, каждая закрывает типичный ответ поддержки:
#
#   «это ваше приложение»  → контрольный замер: сервер обращается к самому себе
#                            через localhost, где сети между машинами нет вовсе;
#   «это ваши настройки»   → счётчики ядра: очереди, conntrack, ресурсы, NIC;
#   «у нас всё в порядке»  → несколько раундов, разнесённых по времени, а не
#                            один снимок, который легко объявить случайностью.
#
# Запуск на сервере:   ./scripts/network_report.sh [файл]
# Или снаружи по ssh:  ssh ваш-хост 'bash -s' < scripts/network_report.sh > отчёт.txt
#
# Без аргумента файл кладётся в /tmp и его путь печатается последней строкой.
set -uo pipefail

ROUNDS="${ROUNDS:-5}"        # сколько раундов замера
GAP="${GAP:-120}"            # пауза между раундами, секунды
PINGS="${PINGS:-30}"         # пакетов на адрес в каждом раунде
RUNS="${RUNS:-20}"           # запросов в контрольном замере на localhost
DOMAIN="${DOMAIN:-trainova.hotbar.pro}"
WEB_PORT="${WEB_PORT:-8000}"
# Три независимых адреса в разных автономных системах: потери сразу до всех
# означают, что узкое место рядом с сервером, а не на одном далёком маршруте.
TARGETS="${TARGETS:-8.8.8.8 1.1.1.1 77.88.8.8}"

OUT="${1:-/tmp/network-report-$(date +%Y%m%d-%H%M).txt}"
exec > >(tee "$OUT") 2>&1

rule() { printf '%s\n' "----------------------------------------------------------------------"; }

cat <<HEADER
ОТЧЁТ О ПОТЕРЯХ ПАКЕТОВ
======================================================================

Сервер:        $(hostname) / $(hostname -I 2>/dev/null | awk '{print $1}')
Отчёт собран:  $(date '+%Y-%m-%d %H:%M:%S %Z')
Работает:      $(uptime -p 2>/dev/null || uptime)
Система:       $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME"), ядро $(uname -r)

СУТЬ ОБРАЩЕНИЯ

  1. На сетевой интерфейс этой машины приходит трафик, адресованный другим
     IP-адресам — в замере ниже это подавляющая часть всех входящих пакетов.
     Мы этот трафик не запрашивали и получать не должны: судя по всему,
     виртуальный коммутатор отправляет нам трафик соседей по сегменту.

  2. Из-за этого наша полоса занята чужими пакетами. Когда кого-то из соседей
     заливают, теряются и наши пакеты: доля переотправок TCP доходила до 10%,
     потери до внешних адресов — до 70-80%, задержка на ближайшем хопе
     вырастала с 0,5 мс до 4000-8000 мс. Прикладной эффект: новые TCP-соединения
     зависают на 16, 32 и более секунд (шаг ретрансмиссии TCP), сайт
     периодически недоступен.

  3. Отдельно обращаю внимание, что видимость чужого трафика — это и нарушение
     изоляции: если чужие пакеты доходят до нас, наши, вероятно, доходят до
     соседей.

  Сервер и его настройки к потерям не причастны, и замеры ниже это показывают:
  обращаясь к самому себе через localhost, он отвечает за миллисекунды, все
  очереди ядра пусты, ресурсы свободны, ошибок на сетевой карте нет.

  Прошу изолировать наш порт от чужого трафика и проверить сетевой узел, к
  которому подключена эта машина.

HEADER

rule
echo "1. КОНТРОЛЬНЫЙ ЗАМЕР: СЕРВЕР САМ НА СЕБЕ (сеть между машинами не участвует)"
rule
echo
echo "Время ответа, секунды. Прямо в приложение, $RUNS новых соединений:"
for _ in $(seq 1 "$RUNS"); do
  curl -s --no-keepalive -o /dev/null -m 20 -w "%{time_starttransfer} " \
    "http://127.0.0.1:$WEB_PORT/accounts/login/" \
    -H "Host: $DOMAIN" -H "X-Forwarded-Proto: https"
done
echo; echo
echo "Через nginx по TLS на себя же, $RUNS новых соединений:"
for _ in $(seq 1 "$RUNS"); do
  curl -s --no-keepalive -k -o /dev/null -m 20 -w "%{time_starttransfer} " \
    "https://$DOMAIN/accounts/login/" --resolve "$DOMAIN:443:127.0.0.1"
done
echo; echo
echo "Вывод: через loopback зависаний нет — веб-сервер и приложение исправны."
echo

rule
echo "2. СОСТОЯНИЕ СЕРВЕРА: почему настройки и ресурсы ни при чём"
rule
echo
echo "-- Очереди приёма соединений (ненулевые счётчики означали бы переполнение) --"
nstat -az 2>/dev/null | grep -iE 'ListenOverflows|ListenDrops|TCPBacklogDrop|TCPReqQFullDrop' ||
  echo "   счётчики недоступны"
echo
echo "-- Слушающие сокеты: Recv-Q должен быть нулевым --"
ss -lnt
echo
echo "-- Таблица conntrack: занято из максимума --"
cat /proc/sys/net/netfilter/nf_conntrack_count /proc/sys/net/netfilter/nf_conntrack_max 2>/dev/null |
  paste -sd'/' - || echo "   недоступна"
echo
echo "-- Ресурсы --"
uptime
free -m | head -2
echo "ядер: $(nproc)"
echo
echo "-- Сетевая карта: errors и dropped должны быть нулевыми --"
NIC=$(ip route get 8.8.8.8 2>/dev/null | grep -oP 'dev \K\S+')
ip -s link show "${NIC:-eth0}" 2>/dev/null
echo

rule
echo "3. ЧУЖОЙ ТРАФИК НА НАШЕМ ИНТЕРФЕЙСЕ"
rule
echo
echo "Ключевой раздел: сколько из приходящих пакетов адресовано вообще не нам."
echo "Чужой трафик занимает нашу полосу, и когда соседа по сегменту заливают,"
echo "наши пакеты теряются вместе с его. Такие пакеты ядро отбрасывает на"
echo "маршрутизации, до firewall, поэтому на нагрузке сервера это не видно."
echo
NIC="${NIC:-eth0}"
read -r rx_bytes_1 rx_packets_1 <<<"$(awk -v n="$NIC:" '$1 == n {print $2, $3}' /proc/net/dev)"
sleep 10
read -r rx_bytes_2 rx_packets_2 <<<"$(awk -v n="$NIC:" '$1 == n {print $2, $3}' /proc/net/dev)"
echo "$rx_bytes_1 $rx_packets_1 $rx_bytes_2 $rx_packets_2" | awk '{
  printf "  входящих пакетов/с: %.0f, входящих байт/с: %.0f (%.2f Мбит/с)\n",
         ($4 - $2) / 10, ($3 - $1) / 10, ($3 - $1) * 8 / 10 / 1000000
}'
echo
MY=$(hostname -I 2>/dev/null | awk '{print $1}')
if command -v tcpdump >/dev/null; then
  CAP=$(mktemp)
  timeout 30 tcpdump -nn -i "$NIC" ip -c "${CAPTURE:-500}" 2>/dev/null >"$CAP"
  total=$(grep -c "" "$CAP")
  mine=$(grep -c "$MY" "$CAP")
  echo "  захвачено пакетов: $total"
  echo "  адресовано нам ($MY): $mine"
  echo "  не имеет к нам отношения: $((total - mine))"
  echo
  echo "  Получатели чужого трафика (топ):"
  grep -v "$MY" "$CAP" | awk '{print $5}' | sed -E 's/:$//' | sort | uniq -c | sort -rn |
    head -8 | sed 's/^/    /'
  rm -f "$CAP"
else
  echo "  tcpdump не установлен: apt install tcpdump"
fi
echo
echo "-- Счётчик firewall для не-нашего трафика (доказывает, что до приложений"
echo "-- эти пакеты не доходят и нагрузку сервера не создают) --"
nft list chain ip filter ufw-not-local 2>/dev/null | sed 's/^/  /' || echo "  недоступен"
echo

rule
echo "4. ПОТЕРИ ВО ВРЕМЕНИ: раундов $ROUNDS, интервал $GAP с"
rule
echo
echo "Каждый раунд: потери до трёх независимых адресов, доля переотправленных"
echo "TCP-сегментов за интервал и трассировка пути по TCP."
echo

counters() { nstat -az TcpRetransSegs TcpOutSegs 2>/dev/null |
  awk '/TcpRetransSegs/{r=$2} /TcpOutSegs/{o=$2} END{print r" "o}'; }

for round in $(seq 1 "$ROUNDS"); do
  echo "=== Раунд $round из $ROUNDS — $(date '+%H:%M:%S %Z') ==="
  before=$(counters)

  for host in $TARGETS; do
    printf "  %-12s " "$host"
    # awk, а не sed: ping печатает дробные проценты (13 из 30 — это
    # 43.3333%), и в отчёте лишние знаки только мешают читать.
    ping -c "$PINGS" -i 0.2 -W 2 -q "$host" 2>&1 | tail -2 | tr '\n' ' ' | awk '{
      loss = rtt = "?"
      if (match($0, /[0-9.]+% packet loss/)) loss = substr($0, RSTART, RLENGTH - 13)
      if (match($0, /= [0-9.\/]+ ms/))       rtt  = substr($0, RSTART + 2, RLENGTH - 5)
      printf "потери %.1f%%, rtt мин/сред/макс/разброс %s мс\n", loss, rtt
    }'
    echo
  done

  after=$(counters)
  echo "$before $after" | awk '{
    sent = $4 - $2; retrans = $3 - $1
    printf "  переотправлено %d из %d отправленных сегментов", retrans, sent
    if (sent > 0) printf " (%.2f%%)", 100 * retrans / sent
    print ""
  }'

  echo "  -- путь наружу по TCP (порт 443; ICMP-трейс здесь непригоден: --"
  echo "  -- промежуточные хопы отвечают на ICMP по остаточному принципу) --"
  if command -v mtr >/dev/null; then
    mtr --tcp -P 443 -r -c 20 -n "${TARGETS%% *}" 2>&1 | sed 's/^/  /'
  else
    echo "  mtr не установлен"
  fi

  echo
  [ "$round" -lt "$ROUNDS" ] && sleep "$GAP"
done

rule
echo "5. ЖИВЫЕ СОЕДИНЕНИЯ: задержка и доля переотправленных байт"
rule
echo
echo "rtt заметно больше minrtt означает, что пакеты стоят в очереди —"
echo "то есть канал перегружен, а не только теряет."
echo
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
      printf "  rtt %-9s minrtt %-8s байт отправлено %-8s переотправлено %s%s\n",
             rtt, minrtt, sent, retrans, share
    }' | head -20
echo

rule
echo "6. КАК ВОСПРОИЗВЕСТИ"
rule
cat <<'REPRO'

  С самого сервера:

    ping -c 30 8.8.8.8
    mtr --tcp -P 443 -r -c 20 -n 8.8.8.8
    nstat -az | grep -E 'TcpRetransSegs|TcpOutSegs'

  Снаружи (зависание видно и на 443, и на 22 — то есть от приложения
  не зависит):

    curl -o /dev/null --no-keepalive -w '%{time_connect} %{time_appconnect}\n' https://ДОМЕН/
    ssh -o ConnectTimeout=60 root@АДРЕС true

REPRO
echo
echo "Конец отчёта. Файл: $OUT"
