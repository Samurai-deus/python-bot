#!/bin/sh
# Сторож market-bot. Запускается таймером market-bot-watchdog.timer раз в 5 минут
# и пишет владельцу в Telegram, когда что-то сломалось и когда починилось.
#
# Живёт на хосте, а не в боте: упавший, зависший или пропавший контейнер о себе
# не расскажет. Что проверяет:
#   • контейнеры бота, API и Redis запущены и healthy — healthcheck бота видит
#     зависший цикл событий и остановившийся цикл анализа (utils/liveness.py);
#   • бот не перезапускался с прошлой проверки;
#   • последний бэкап моложе 26 ч, копия вне сервера отправлялась за 26 ч,
#     юнит бэкапа не в состоянии failed;
#   • диск заполнен меньше чем на 90 %;
#   • сертификат сайта действует ещё 14 дней и больше;
#   • сайт мини-аппа отвечает 200.
#
# Пишет только при смене набора проблем: появилась проблема, всё починилось.
# Пока проблема держится — напоминание раз в 6 ч. Отправить не удалось —
# состояние не сохраняется, следующий запуск повторит (exit 1, юнит failed).
#
# Чего сторож не видит: смерть самого хоста и отказ прокси до Telegram — тогда
# писать некому. Косвенный признак для владельца — ежедневная копия базы от
# backup.sh: перестала приходить, значит, хост молчит.
set -u

APP="${MARKET_BOT_APP:-/opt/market-bot}"
STATE="${WATCHDOG_STATE:-/var/lib/market-bot-watchdog}"
NOW="${WATCHDOG_NOW:-$(date +%s)}"
REMIND_SEC=21600
STALE_SEC=93600
CONTAINERS="market-bot market-bot-api market-bot-redis"

DOMAIN=""
[ -f "$APP/deploy.conf" ] && . "$APP/deploy.conf"
CERT="${WATCHDOG_CERT:-/etc/letsencrypt/live/$DOMAIN/fullchain.pem}"
. "$APP/notify.sh"

mkdir -p "$STATE"
chmod 700 "$STATE"

is_num() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
  esac
  return 0
}

problems=""
problem() {
  problems="${problems}• $1
"
}

# --- контейнеры ---
for c in $CONTAINERS; do
  st=$(docker inspect -f '{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$c" 2>/dev/null) || st=""
  case "$st" in
    running/healthy|running/starting) ;;
    *)
      why=$(docker inspect -f '{{if .State.Health}}{{range .State.Health.Log}}{{.Output}}{{end}}{{end}}' "$c" 2>/dev/null \
        | grep -v '^[[:space:]]*$' | tail -n 1 | cut -c1-160)
      problem "контейнер $c: ${st:-не найден}${why:+ ($why)}"
      ;;
  esac
done

# --- перезапуски бота: событие, а не состояние ---
restarts=$(docker inspect -f '{{.RestartCount}}' market-bot 2>/dev/null) || restarts=""
prev_restarts=$(cat "$STATE/restarts" 2>/dev/null || true)
event=""
if is_num "$restarts" && is_num "$prev_restarts" && [ "$restarts" -gt "$prev_restarts" ]; then
  event="бот перезапускался: $((restarts - prev_restarts)) раз(а) с прошлой проверки (docker logs market-bot)"
fi

# --- бэкап и его копия вне сервера ---
newest=$(ls -1t "$APP/backups"/market_bot-*.db.gz 2>/dev/null | head -n 1)
if [ -z "$newest" ]; then
  problem "бэкапов базы нет"
else
  backup_age=$((NOW - $(stat -c %Y "$newest")))
  if [ "$backup_age" -gt "$STALE_SEC" ]; then
    problem "последний бэкап базы сделан $((backup_age / 3600)) ч назад"
  fi
fi
offsite=$(cat "$APP/backups/.offsite_ok" 2>/dev/null || echo 0)
is_num "$offsite" || offsite=0
if [ $((NOW - offsite)) -gt "$STALE_SEC" ]; then
  problem "копия базы вне сервера не отправлялась больше 26 ч"
fi
if systemctl is-failed --quiet market-bot-backup.service 2>/dev/null; then
  problem "последний запуск бэкапа завершился ошибкой (journalctl -u market-bot-backup)"
fi

# --- диск ---
use=$(df -P "$APP" 2>/dev/null | awk 'NR==2 {sub("%", "", $5); print $5}')
if is_num "$use" && [ "$use" -ge 90 ]; then
  problem "диск заполнен на $use %"
fi

# --- сертификат и сайт ---
if [ -n "$DOMAIN" ]; then
  end=$(openssl x509 -enddate -noout -in "$CERT" 2>/dev/null | cut -d= -f2)
  if [ -z "$end" ]; then
    problem "не читается сертификат $CERT"
  else
    expires=$(date -d "$end" +%s 2>/dev/null || echo 0)
    days=$(( (expires - NOW) / 86400 ))
    if [ "$days" -lt 14 ]; then
      problem "сертификат сайта истекает через $days дн."
    fi
  fi
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://$DOMAIN/" 2>/dev/null) || true
  if [ "$code" != 200 ]; then
    problem "сайт мини-аппа отвечает ${code:-ничем} вместо 200"
  fi
fi

# --- что отправить ---
if [ -n "$problems" ]; then
  key=$(printf '%s' "$problems" | LC_ALL=C sort | md5sum | cut -c1-32)
else
  key=ok
fi
prev_key=$(cat "$STATE/key" 2>/dev/null || true)
last_sent=$(cat "$STATE/last_sent" 2>/dev/null || echo 0)
is_num "$last_sent" || last_sent=0

msg=""
if [ "$key" != ok ]; then
  if [ "$key" != "$prev_key" ]; then
    msg="🔴 market-bot: проблема
$problems"
  elif [ $((NOW - last_sent)) -ge "$REMIND_SEC" ]; then
    msg="🔴 market-bot: всё ещё не в порядке
$problems"
  fi
elif [ -z "$prev_key" ]; then
  msg="✅ market-bot: сторож включён, всё в порядке"
elif [ "$prev_key" != ok ]; then
  msg="✅ market-bot: всё снова в порядке"
fi
if [ -n "$event" ]; then
  msg="${msg}${msg:+
}⚠️ $event"
fi

if [ -n "$msg" ]; then
  if ! tg_text "$msg"; then
    echo "watchdog: не удалось отправить в Telegram, повторю следующим запуском:"
    printf '%s\n' "$msg"
    exit 1
  fi
  echo "$NOW" > "$STATE/last_sent"
fi
echo "$key" > "$STATE/key"
if is_num "$restarts"; then
  echo "$restarts" > "$STATE/restarts"
fi

if [ "$key" = ok ]; then
  echo "watchdog: ok"
else
  printf 'watchdog: проблемы\n%s' "$problems"
fi
