# shellcheck shell=sh
# Отправка владельцу в Telegram с хоста — для сторожа (watchdog.sh) и бэкапа
# (backup.sh). Подключается через `. "$APP/notify.sh"`; нужна переменная APP.
#
# С хоста api.telegram.org напрямую недоступен — идём через sing-box на
# 127.0.0.1:12334, как и контейнеры. Токен уходит curl-у конфигом через stdin
# (-K -), а не аргументом: аргументы видны всем в списке процессов.

TG_PROXY="${TG_PROXY:-http://127.0.0.1:12334}"

tg_env() {
  TG_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "$APP/.env" 2>/dev/null | cut -d= -f2-)
  TG_CHAT=$(grep '^ADMIN_CHAT_ID=' "$APP/.env" 2>/dev/null | cut -d= -f2-)
  if [ -z "$TG_TOKEN" ] || [ -z "$TG_CHAT" ]; then
    echo "  notify: в $APP/.env нет TELEGRAM_BOT_TOKEN или ADMIN_CHAT_ID" >&2
    return 1
  fi
}

# tg_call <метод> <аргументы curl...> — успех только при "ok":true в ответе.
tg_call() {
  method="$1"
  shift
  resp=$(printf 'url = "https://api.telegram.org/bot%s/%s"\n' "$TG_TOKEN" "$method" \
    | curl -s --max-time 60 -x "$TG_PROXY" -K - "$@") || resp=""
  case "$resp" in
    *'"ok":true'*) return 0 ;;
  esac
  echo "  notify: Telegram не принял $method: $(printf '%s' "$resp" | cut -c1-200)" >&2
  return 1
}

# tg_text <текст>
tg_text() {
  tg_env || return 1
  tg_call sendMessage --data-urlencode "chat_id=$TG_CHAT" --data-urlencode "text=$1"
}

# tg_file <файл> <подпись> — документом и без звука: копия базы приходит ночью.
tg_file() {
  tg_env || return 1
  tg_call sendDocument -F "chat_id=$TG_CHAT" -F "document=@$1" -F "caption=$2" -F "disable_notification=true"
}
