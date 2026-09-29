#!/bin/sh
# split-env.sh <карта> <общий .env> <каталог> — файлы окружения по сервисам (аудит 29.09.2026, пакет 8).
#
# Каждый сервис из карты (deploy/service-secrets.conf) получает <каталог>/<сервис>.env: все несекретные строки
# общего .env и только свои секреты. Секрет — имя, в котором есть KEY, SECRET, TOKEN, PASSWORD, PASSPHRASE или
# DSN, а также REDIS_URL. Общий .env остаётся единственным местом правки (шаги token, ai-key, bybit-key);
# файлы сервисов пересобираются перед каждым docker compose up. В вывод — только имена, значения никогда.
set -eu
map="$1"
src="$2"
out="$3"
umask 077
mkdir -p "$out"
chmod 700 "$out"
grep -E '^[a-z][a-z0-9_-]*:' "$map" | while IFS=: read -r svc allow; do
  ALLOW="$allow" awk '
    BEGIN { n = split(ENVIRON["ALLOW"], a, " "); for (i = 1; i <= n; i++) ok[a[i]] = 1 }
    /^[ \t]*(#|$)/ { print; next }
    {
      name = $0
      sub(/=.*/, "", name)
      sub(/^[ \t]*(export[ \t]+)?/, "", name)
      if ((name ~ /(KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE|DSN)/ || name == "REDIS_URL") && !(name in ok)) {
        dropped = dropped " " name
        next
      }
      print
    }
    END { print dropped > "/dev/stderr" }' "$src" > "$out/$svc.env.new" 2> "$out/$svc.dropped"
  chmod 600 "$out/$svc.env.new"
  mv -f "$out/$svc.env.new" "$out/$svc.env"
  dropped=$(cat "$out/$svc.dropped")
  rm -f "$out/$svc.dropped"
  echo "  env/$svc.env: чужие секреты убраны —${dropped:- нет}"
done
