#!/bin/sh
# Бэкап базы market-bot (SQLite). Запускается systemd-таймером
# market-bot-backup.timer раз в сутки; вручную — /opt/market-bot/deploy.sh backup.
#
# Копию делает scripts/backup_sqlite.py внутри контейнера: VACUUM INTO даёт
# согласованную копию живой базы в режиме WAL, integrity_check её проверяет.
# Здесь — забрать копию на хост, сжать, проверить архив и убрать старые.
#
# Вторая копия уходит за пределы хоста: архив шифруется gpg (AES256, ключ —
# $APP/backup.passphrase) и отправляется владельцу в Telegram документом без
# звука. Перед отправкой шифровка расшифровывается и сверяется с архивом
# побайтно: отправлять имеет смысл только то, что потом восстановится. Копия
# полезна, только если ключ сохранён и вне сервера (deploy/README.md).
#
# Сбой вывоза локальную копию не трогает, но роняет скрипт (exit 1): юнит
# становится failed, и сторож (watchdog.sh) пишет об этом владельцу.
set -eu

APP="${MARKET_BOT_APP:-/opt/market-bot}"
DIR="$APP/backups"
KEY="$APP/backup.passphrase"
KEEP_DAYS=14
name="market_bot-$(date +%Y%m%d-%H%M%S).db"
. "$APP/notify.sh"

mkdir -p "$DIR"
chmod 700 "$DIR"

docker exec market-bot python scripts/backup_sqlite.py "/data/backups/$name"
docker cp "market-bot:/data/backups/$name" "$DIR/$name"
docker exec market-bot rm -f "/data/backups/$name"

gzip -9 "$DIR/$name"
gzip -t "$DIR/$name.gz"
chmod 600 "$DIR/$name.gz"

find "$DIR" -name 'market_bot-*.db.gz' -mtime +"$KEEP_DAYS" -print -delete

echo "backup ok: $DIR/$name.gz ($(du -h "$DIR/$name.gz" | cut -f1))"

# Базы исполнителей И14/И18/И13 (аудит 29.09.2026: не копировались вовсе). В них пик стоимости,
# отметка остановки по правилу и дата старта — потеря тома обнулила бы стоп и итоги эксперимента.
# Та же согласованная копия backup_sqlite.py внутри каждого контейнера (путь базы — через DB_PATH).
stamp=$(date +%Y%m%d-%H%M%S)
work="$DIR/executors-$stamp"
mkdir -p "$work"
for spec in market-bot-portfolio:/portfolio/portfolio.db market-bot-btcalts:/btcalts/btcalts.db market-bot-carry:/carry/carry.db; do
  ctr=${spec%%:*}
  db=${spec#*:}
  copy="$(dirname "$db")/backup-$stamp.db"
  docker exec -e DB_PATH="$db" "$ctr" python scripts/backup_sqlite.py "$copy"
  docker cp "$ctr:$copy" "$work/$(basename "$db")"
  docker exec "$ctr" rm -f "$copy"
done
tar -czf "$work.tar.gz" -C "$work" .
rm -rf "$work"
tar -tzf "$work.tar.gz" > /dev/null
chmod 600 "$work.tar.gz"
find "$DIR" -name 'executors-*.tar.gz' -mtime +"$KEEP_DAYS" -print -delete
echo "backup ok: $work.tar.gz ($(du -h "$work.tar.gz" | cut -f1))"

# Форвардная запись И19б (межбиржевой фандинг, вердикт 16.11) — прошлое не перезаписать. Только
# локальная копия, 3 дня: база растёт ~5 МБ в сутки и скоро перерастёт лимит Telegram (50 МБ).
xf="xfunding_live-$stamp.db"
docker exec -e DB_PATH=/data/db/xfunding_live.db market-bot-news python scripts/backup_sqlite.py "/data/backups/$xf"
docker cp "market-bot-news:/data/backups/$xf" "$DIR/$xf"
docker exec market-bot-news rm -f "/data/backups/$xf"
gzip -9 "$DIR/$xf"
gzip -t "$DIR/$xf.gz"
chmod 600 "$DIR/$xf.gz"
find "$DIR" -name 'xfunding_live-*.db.gz' -mtime +2 -print -delete
echo "backup ok: $DIR/$xf.gz ($(du -h "$DIR/$xf.gz" | cut -f1)), только на сервере"

offsite() {
  src="$1"
  if [ ! -s "$KEY" ]; then
    echo "offsite: нет ключа $KEY — копия вне сервера не отправлена"
    return 1
  fi
  GNUPGHOME="$APP/.gnupg"
  export GNUPGHOME
  mkdir -p "$GNUPGHOME"
  chmod 700 "$GNUPGHOME"

  enc="$src.gpg"
  gpg --batch --yes --quiet --pinentry-mode loopback --passphrase-file "$KEY" \
    --symmetric --cipher-algo AES256 -o "$enc" "$src" || true
  if ! gpg --batch --quiet --pinentry-mode loopback --passphrase-file "$KEY" -d "$enc" 2>/dev/null | cmp -s - "$src"; then
    rm -f "$enc"
    echo "offsite: ОШИБКА расшифрованная копия не совпала с архивом — не отправляю"
    return 1
  fi

  size=$(du -h "$enc" | cut -f1)
  if ! tg_file "$enc" "market-bot: бэкап базы $(basename "$src") ($size). Расшифровка: gpg -d, ключ — backup.passphrase с сервера."; then
    rm -f "$enc"
    echo "offsite: ОШИБКА отправка в Telegram"
    return 1
  fi
  rm -f "$enc"
  date +%s > "$DIR/.offsite_ok"
  echo "offsite ok: зашифрованная копия отправлена владельцу ($size)"
}

offsite "$DIR/$name.gz" || exit 1
offsite "$work.tar.gz" || exit 1
