#!/bin/sh
# Деплой market-bot на прод. Живёт в git (deploy/deploy.sh) и на сервере
# (/opt/market-bot/deploy.sh); запускается по ssh одним файлом:
#     ssh <host> '/opt/market-bot/deploy.sh <шаг> [аргумент]'
#
# Образец — deploy.sh соседнего проекта на этом же хосте, вместе с его правилами:
#   • набор действий фиксирован и лежит в git — видно, что именно уедет на прод;
#   • артефакты кладутся в staging ($STAGE) через scp ДО запуска;
#   • успешный шаг убирает свой staging, упавший — оставляет для разбора;
#   • старые релизы, бэкапы фронта и конфигов ротируются, а не копятся.
#
# Шаги:
#   install <домен>  каталоги и /opt/market-bot/deploy.conf (идемпотентно)
#   env              /opt/market-bot/.env из $STAGE/secrets.env; секреты генерирует
#                    на сервере; существующий .env НЕ перезаписывает никогда
#   release <sha>    $STAGE/release.tar.gz → releases/<sha> → docker build → up →
#                    ожидание healthy → при сбое откат на предыдущий релиз
#   web              $STAGE/web.tar.gz → web-releases/<время>, ссылка web переключается атомарно
#   web-rollback     ссылка web — на прошлый выпуск фронта
#   nginx            сайт из шаблона релиза; нет сертификата — выпуск через webroot;
#                    nginx -t ДО reload, при ошибке — возврат прежнего конфига
#   token            замена токена бота из $STAGE/secrets.env после перевыпуска
#   ai-key           ключ OpenRouter для ИИ-трейдера из $STAGE/secrets.env: проверка у
#                    OpenRouter через прокси хоста → .env (+ AI_PROXY_URL) → пересоздание
#   menu             кнопка меню бота → текущий домен (адрес мини-аппа живёт у Telegram)
#   backup           бэкап базы сейчас (с зашифрованной копией владельцу в Telegram)
#   watchdog         проверка сторожем сейчас (обычно — таймер раз в 5 минут)
#   support          юниты systemd, скрипты бэкапа и сторожа, ключ бэкапов из текущего релиза
#   smoke            проверка: сайт, кэш index.html, API, авторизация, режим, прокси
#   status           контейнеры, текущий релиз, хвост логов
set -eu

APP=/opt/market-bot
STAGE=/tmp/market-bot-deploy
KEEP=3
CONF="$APP/deploy.conf"
CONTAINERS="market-bot market-bot-api market-bot-redis"

DOMAIN=""
[ -f "$CONF" ] && . "$CONF"

compose() {
  tag="$1"; shift
  RELEASE_TAG="$tag" docker compose -p market-bot -f "$APP/docker-compose.yml" --env-file "$APP/.env" "$@"
}

current_tag() {
  cat "$APP/current_tag" 2>/dev/null || true
}

wait_healthy() {
  limit=$1
  waited=0
  while [ "$waited" -lt "$limit" ]; do
    bad=0
    for c in $CONTAINERS; do
      st=$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || echo missing)
      [ "$st" = "healthy" ] || bad=1
    done
    [ "$bad" = 0 ] && return 0
    sleep 5
    waited=$((waited + 5))
  done
  for c in $CONTAINERS; do
    echo "    $c: $(docker inspect -f '{{.State.Status}}/{{.State.Health.Status}}' "$c" 2>/dev/null || echo missing)"
  done
  return 1
}

step_install() {
  mkdir -p "$APP/releases" "$APP/backups" "$APP/web" "$STAGE"
  chmod 755 "$APP"
  chmod 700 "$APP/backups" "$STAGE"
  if [ ! -f "$CONF" ]; then
    [ -n "${1:-}" ] || { echo "  использование: deploy.sh install <домен>"; exit 2; }
    printf 'DOMAIN=%s\n' "$1" > "$CONF"
    echo "  записал $CONF: DOMAIN=$1"
  else
    echo "  $CONF уже есть: $(cat "$CONF")"
  fi
  echo "  каталоги готовы"
}

step_env() {
  if [ -f "$APP/.env" ]; then
    echo "  $APP/.env уже есть — не трогаю"
    rm -f "$STAGE/secrets.env"
    return 0
  fi
  [ -n "$DOMAIN" ] || { echo "  DOMAIN не задан — сначала deploy.sh install <домен>"; exit 1; }
  [ -f "$STAGE/secrets.env" ] || { echo "  нет $STAGE/secrets.env"; exit 1; }
  # shellcheck disable=SC1090,SC1091
  . "$STAGE/secrets.env"
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${OWNER_TELEGRAM_ID:-}" ]; then
    echo "  в secrets.env нужны TELEGRAM_BOT_TOKEN и OWNER_TELEGRAM_ID"
    exit 1
  fi
  redis_password=$(openssl rand -hex 24)
  encryption_key=$(openssl rand 32 | base64 | tr '+/' '-_')
  umask 077
  cat > "$APP/.env" <<EOF
# Создан deploy.sh env $(date '+%Y-%m-%d %H:%M'). Секреты сгенерированы на сервере.
ENVIRONMENT=production

ADMIN_CHAT_ID=$OWNER_TELEGRAM_ID
ALLOWED_USER_IDS=

TELEGRAM_BOT_TOKEN=$TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID=$OWNER_TELEGRAM_ID
TELEGRAM_PROXY_URL=http://host.docker.internal:12334

# Бумажная торговля: биржа вызывается только за рыночными данными
PAPER_TRADING=true
DRY_RUN=true
LIVE_TRADING=false
BYBIT_TESTNET=false
BYBIT_DEMO=false
REAL_CAPITAL_CAP_USDT=0
PAPER_INITIAL_BALANCE_USDT=100
PAPER_TAKER_FEE_PCT=0.055
RISK_PERCENT=2.0
MIN_POSITION_SIZE_USDT=5
MAX_POSITION_SIZE_USDT=1000

BYBIT_API_KEY=
BYBIT_API_SECRET=

BOT_INTERVAL=300
LOG_TO_FILE=false

DB_PATH=/data/db/market_bot.db
DATABASE_URL=
ENCRYPTION_KEY=$encryption_key

CORS_ORIGINS=https://$DOMAIN,https://web.telegram.org,https://telegram.org
DISABLE_AUTH=false
REDIS_PASSWORD=$redis_password
REDIS_URL=redis://:$redis_password@redis:6379
RATE_LIMIT_PER_MINUTE=120
RATE_LIMIT_WRITE_PER_MINUTE=10
API_IP_WHITELIST=
SENTRY_DSN=
EOF
  chmod 600 "$APP/.env"
  shred -u "$STAGE/secrets.env" 2>/dev/null || rm -f "$STAGE/secrets.env"
  echo "  создан $APP/.env (права 600; значения секретов в вывод не печатаются)"
}

install_support_files() {
  rel="$1"
  changed=0
  for unit in market-bot-backup.service market-bot-backup.timer \
              market-bot-watchdog.service market-bot-watchdog.timer; do
    if ! cmp -s "$rel/deploy/systemd/$unit" "/etc/systemd/system/$unit"; then
      cp "$rel/deploy/systemd/$unit" "/etc/systemd/system/$unit"
      changed=1
    fi
  done
  [ "$changed" = 1 ] && systemctl daemon-reload
  # Переименованием, а не перезаписью: таймер может запустить скрипт посреди копирования.
  for f in backup.sh watchdog.sh notify.sh; do
    cp "$rel/deploy/$f" "$APP/$f.next"
    chmod 755 "$APP/$f.next"
    mv -f "$APP/$f.next" "$APP/$f"
  done
  # Ключ шифрования копий базы, уходящих за пределы хоста. Создаётся один раз и
  # никогда не перезаписывается: им зашифрованы уже отправленные копии.
  if [ ! -s "$APP/backup.passphrase" ]; then
    (umask 077; head -c 32 /dev/urandom | base64 > "$APP/backup.passphrase")
    echo "  создан ключ шифрования бэкапов — сохраните его вне сервера (deploy/README.md)"
  fi
  systemctl enable --now market-bot-backup.timer market-bot-watchdog.timer >/dev/null 2>&1 || true
}

step_support() {
  tag=$(current_tag)
  [ -n "$tag" ] && [ -d "$APP/releases/$tag" ] || { echo "  нет текущего релиза"; exit 1; }
  install_support_files "$APP/releases/$tag"
  echo "  вспомогательные файлы релиза $tag установлены"
}

prune_releases() {
  cur=$(current_tag)
  # shellcheck disable=SC2012
  ls -1t "$APP/releases" | tail -n +$((KEEP + 1)) | while read -r old; do
    [ -n "$old" ] && [ "$old" != "$cur" ] || continue
    rm -rf "${APP:?}/releases/$old"
    docker rmi "market-bot:$old" >/dev/null 2>&1 || true
    echo "    прибрал старый релиз $old"
  done
}

step_release() {
  sha="${1:-}"
  [ -n "$sha" ] || { echo "  использование: deploy.sh release <sha>"; exit 2; }
  tarball="$STAGE/release.tar.gz"
  [ -f "$tarball" ] || { echo "  нет $tarball"; exit 1; }
  [ -f "$APP/.env" ] || { echo "  нет $APP/.env — сначала deploy.sh env"; exit 1; }

  dir="$APP/releases/$sha"
  rm -rf "$dir"
  mkdir -p "$dir"
  tar -xzf "$tarball" -C "$dir"

  echo "  собираю образ market-bot:$sha"
  docker build -q -t "market-bot:$sha" "$dir" >/dev/null

  prev=$(current_tag)
  cp "$dir/deploy/docker-compose.prod.yml" "$APP/docker-compose.yml"
  install_support_files "$dir"

  echo "  поднимаю релиз $sha (предыдущий: ${prev:-нет})"
  compose "$sha" up -d --remove-orphans

  if wait_healthy 180; then
    echo "$sha" > "$APP/current_tag"
    echo "  релиз $sha здоров"
    prune_releases
    rm -f "$tarball"
    # Себя обновляем переименованием, а не перезаписью: работающий sh читает
    # скрипт из открытого файла, и перезапись на ходу рвала бы его посередине.
    cp "$dir/deploy/deploy.sh" "$APP/deploy.sh.next"
    chmod 755 "$APP/deploy.sh.next"
    mv -f "$APP/deploy.sh.next" "$APP/deploy.sh"
    # Релиз выполняла прежняя копия скрипта, и install_support_files выше — её
    # логика. 10.09.2026 так не встали сторож и ключ бэкапов: их установку
    # принёс этот же релиз, а прежний скрипт о них не знал, но новый backup.sh
    # уже скопировал. Поэтому вспомогательные файлы ставит ещё раз новый скрипт.
    "$APP/deploy.sh" support
    return 0
  fi

  echo "  ОШИБКА: релиз $sha не стал здоровым за 180 с"
  compose "$sha" logs --tail 60 || true
  if [ -n "$prev" ] && [ "$prev" != "$sha" ] && [ -d "$APP/releases/$prev" ]; then
    echo "  откат на $prev"
    cp "$APP/releases/$prev/deploy/docker-compose.prod.yml" "$APP/docker-compose.yml"
    compose "$prev" up -d --remove-orphans
    if wait_healthy 120; then
      echo "  откат удался — работает $prev"
    else
      echo "  ОТКАТ НЕ УДАЛСЯ — нужен человек"
    fi
  fi
  exit 1
}

# Фронт (5.4). Каждый выпуск — свой каталог web-releases/<время>, а web — символическая
# ссылка на текущий: nginx читает root через неё. Переключение атомарное (ln -sfn во
# временную ссылку и mv -T поверх), без мгновения, когда каталога нет. Хешированные
# ассеты прошлого выпуска переносятся в новый без перезаписи: клиент, загрузивший
# старый index.html за миг до переключения, найдёт свои файлы. Хранятся KEEP выпусков;
# откат — deploy.sh web-rollback. До 10.09.2026 web был обычным каталогом, который
# подменялся двумя переименованиями.
WEB_RELEASES="$APP/web-releases"

switch_web() {
  ln -sfn "$1" "$APP/web.next"
  mv -T "$APP/web.next" "$APP/web"
}

prune_web_releases() {
  cur=$(readlink -f "$APP/web")
  # shellcheck disable=SC2012
  ls -1dt "$WEB_RELEASES"/*/ 2>/dev/null | sed 's:/$::' | tail -n +$((KEEP + 1)) | while read -r old; do
    [ "$(readlink -f "$old")" = "$cur" ] || rm -rf "$old"
  done
  # копии прежней схемы (web.bak-*) — выпуски теперь в web-releases
  rm -rf "$APP"/web.bak-*
}

step_web() {
  tarball="$STAGE/web.tar.gz"
  [ -f "$tarball" ] || { echo "  нет $tarball"; exit 1; }
  mkdir -p "$WEB_RELEASES"
  stamp=$(date +%Y%m%d-%H%M%S)
  new="$WEB_RELEASES/$stamp"
  rm -rf "$new"
  mkdir -p "$new"
  tar -xzf "$tarball" -C "$new"
  [ -f "$new/index.html" ] || { echo "  в архиве нет index.html"; rm -rf "$new"; exit 1; }

  # Однократная миграция со старой схемы: реальный каталог web становится выпуском.
  if [ -d "$APP/web" ] && [ ! -L "$APP/web" ]; then
    mv "$APP/web" "$WEB_RELEASES/legacy-$stamp"
    ln -sfn "$WEB_RELEASES/legacy-$stamp" "$APP/web"
  fi

  # Свои ассеты выпуска — списком, до переноса чужих: следующий выпуск перенесёт
  # только их.
  (cd "$new" && find assets -type f 2>/dev/null | sort) > "$new/.build-assets"

  # Ассеты прошлого выпуска — в новый, без перезаписи (имена хешированные): открытое
  # у пользователя приложение ещё догружает свои чанки. Переносятся только файлы
  # сборки прошлого выпуска, а не всё, что он сам унаследовал: до 11.09.2026 каждая
  # выкладка тащила дальше ассеты всех прошлых сборок (8 из 16 — со старым SDK).
  if [ -f "$APP/web/.build-assets" ]; then
    while read -r f; do
      if [ -f "$APP/web/$f" ] && [ ! -e "$new/$f" ]; then
        mkdir -p "$(dirname "$new/$f")"
        cp -p "$APP/web/$f" "$new/$f"
      fi
    done < "$APP/web/.build-assets"
  elif [ -d "$APP/web/assets" ]; then
    # у выпусков до 11.09 списка нет — один раз переносим всё, как раньше
    mkdir -p "$new/assets"
    cp -rn "$APP/web/assets/." "$new/assets/"
  fi
  chmod -R a+rX "$new"
  switch_web "$new"
  prune_web_releases
  rm -f "$tarball"
  echo "  фронт обновлён: $stamp ($(find "$new" -type f | wc -l) файлов)"
}

step_web_rollback() {
  cur=$(readlink -f "$APP/web")
  # shellcheck disable=SC2012
  prev=$(ls -1dt "$WEB_RELEASES"/*/ 2>/dev/null | sed 's:/$::' | while read -r d; do
    [ "$(readlink -f "$d")" != "$cur" ] && echo "$d"
  done | head -n 1)
  [ -n "$prev" ] || { echo "  нет прошлого выпуска фронта"; exit 1; }
  switch_web "$prev"
  echo "  фронт откатан на $(basename "$prev")"
}

nginx_apply() {
  previous="${1:-}"
  if nginx -t 2>"$STAGE/nginx-test.log"; then
    systemctl reload nginx
    return 0
  fi
  echo "  ОШИБКА nginx -t:"
  cat "$STAGE/nginx-test.log"
  if [ -n "$previous" ]; then
    cp "$previous" /etc/nginx/sites-available/market-bot
  else
    rm -f /etc/nginx/sites-enabled/market-bot
  fi
  if nginx -t >/dev/null 2>&1; then
    systemctl reload nginx
    echo "  прежний конфиг восстановлен, nginx перезагружен"
  fi
  exit 1
}

# Длинное имя поддомена (…sslip.io, 30 символов) не влезает в корзину хэша имён
# серверов по умолчанию — на этом хосте 32. nginx -t падал: «could not build
# server_names_hash, you should increase server_names_hash_bucket_size: 32».
# Директива уровня http; задаём её СВОИМ файлом в conf.d (он подключается внутри
# http{}), общий nginx.conf соседей не трогаем. Если размер уже задан где-то ещё —
# свой файл убираем: повторная директива сама роняет nginx -t.
ensure_server_names_bucket() {
  own=/etc/nginx/conf.d/market-bot-server-names.conf
  if grep -Eqs '^[[:space:]]*server_names_hash_bucket_size' /etc/nginx/nginx.conf; then
    rm -f "$own"
    return 0
  fi
  for f in /etc/nginx/conf.d/*.conf; do
    [ -f "$f" ] && [ "$f" != "$own" ] || continue
    if grep -Eqs '^[[:space:]]*server_names_hash_bucket_size' "$f"; then
      rm -f "$own"
      return 0
    fi
  done
  printf '%s\n' "# market-bot: длинное имя поддомена не влезает в корзину хэша по умолчанию" \
    "server_names_hash_bucket_size 64;" > "$own"
}

step_nginx() {
  [ -n "$DOMAIN" ] || { echo "  DOMAIN не задан"; exit 1; }
  ensure_server_names_bucket
  cur=$(current_tag)
  src="$APP/releases/${cur:-none}/deploy/nginx"
  [ -d "$src" ] || { echo "  нет шаблонов nginx в $src — сначала deploy.sh release"; exit 1; }
  site=/etc/nginx/sites-available/market-bot
  link=/etc/nginx/sites-enabled/market-bot

  mkdir -p /etc/nginx/snippets
  cp "$src/market-bot-headers.conf" /etc/nginx/snippets/market-bot-headers.conf

  previous=""
  if [ -f "$site" ]; then
    previous="$site.bak-$(date +%s)"
    cp "$site" "$previous"
  fi

  if [ ! -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    echo "  сертификата для $DOMAIN нет — выпускаю через webroot"
    sed "s/__DOMAIN__/$DOMAIN/g" "$src/market-bot-bootstrap.conf.template" > "$site"
    ln -sf "$site" "$link"
    nginx_apply "$previous"
    mkdir -p /var/www/html
    # Без --agree-tos намеренно: учётная запись ACME на хосте уже есть. Если certbot
    # попросит принять условия — это решение владельца, шаг остановится.
    certbot certonly --webroot -w /var/www/html -d "$DOMAIN" --non-interactive --keep-until-expiring
  fi

  sed "s/__DOMAIN__/$DOMAIN/g" "$src/market-bot.conf.template" > "$site"
  ln -sf "$site" "$link"
  nginx_apply "$previous"
  # shellcheck disable=SC2012
  ls -t /etc/nginx/sites-available/market-bot.bak-* 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r old; do
    rm -f "$old"
  done
  echo "  nginx: сайт $DOMAIN применён"
}

step_smoke() {
  [ -n "$DOMAIN" ] || { echo "  DOMAIN не задан"; exit 1; }
  fail=0

  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://$DOMAIN/")
  if [ "$code" = 200 ]; then echo "  ok  сайт: 200"; else echo "  ОШИБКА сайт: $code (ждали 200)"; fail=1; fi

  cache=$(curl -sI --max-time 15 "https://$DOMAIN/" | tr -d '\r' | awk -F': ' 'tolower($1)=="cache-control" {print $2}')
  if echo "$cache" | grep -q no-store; then echo "  ok  index.html: no-store"; else echo "  ОШИБКА index.html: Cache-Control='$cache'"; fail=1; fi

  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://$DOMAIN/api/system/health")
  if [ "$code" = 401 ]; then echo "  ok  API снаружи без initData: 401"; else echo "  ОШИБКА API снаружи: $code (ждали 401)"; fail=1; fi

  for path in /metrics /docs /openapi.json; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "https://$DOMAIN$path")
    if [ "$code" = 404 ]; then echo "  ok  $path наружу: 404"; else echo "  ОШИБКА $path наружу: $code"; fail=1; fi
  done

  echo "  авторизация на живом API (изнутри контейнера):"
  docker exec market-bot-api python scripts/smoke_auth.py || fail=1

  for c in $CONTAINERS; do
    st=$(docker inspect -f '{{.State.Health.Status}}' "$c" 2>/dev/null || echo missing)
    if [ "$st" = healthy ]; then echo "  ok  $c: healthy"; else echo "  ОШИБКА $c: $st"; fail=1; fi
  done

  # healthcheck бота видит зависание, но не падение по кругу: между перезапусками
  # бот успевает снова стать healthy. Счётчик перезапусков отличает.
  restarts=$(docker inspect -f '{{.RestartCount}}' market-bot 2>/dev/null || echo "?")
  if [ "$restarts" = 0 ]; then
    echo "  ok  бот не перезапускался"
  else
    echo "  ОШИБКА бот перезапускался: $restarts раз(а) — смотрите deploy.sh status"
    fail=1
  fi

  # Ожидаемый режим записывает шаг mode (demo → TESTNET); без файла — бумажная торговля
  expected=$(cat "$APP/trading_mode.expected" 2>/dev/null || echo PAPER_TRADING)
  mode=$(docker exec market-bot python -c "from trading_mode import get_trading_mode; print(get_trading_mode().value)" 2>/dev/null || echo "?")
  if [ "$mode" = "$expected" ]; then echo "  ok  режим торговли: $mode"; else echo "  ОШИБКА режим торговли: $mode (ожидался $expected)"; fail=1; fi

  tg=$(docker exec market-bot python -c "import os, httpx; print(httpx.get('https://api.telegram.org', proxy=os.environ.get('TELEGRAM_PROXY_URL') or None, timeout=15).status_code)" 2>/dev/null || echo "нет ответа")
  case "$tg" in
    2*|3*) echo "  ok  Telegram из контейнера через прокси: $tg" ;;
    *) echo "  ОШИБКА Telegram из контейнера: $tg"; fail=1 ;;
  esac

  if [ -L "$APP/web" ] && [ -f "$APP/web/index.html" ]; then
    echo "  ok  фронт: выпуск $(basename "$(readlink -f "$APP/web")")"
  else
    echo "  ОШИБКА фронт: $APP/web — не ссылка на выпуск с index.html"; fail=1
  fi
  for t in market-bot-backup.timer market-bot-watchdog.timer; do
    if systemctl is-active --quiet "$t"; then echo "  ok  таймер $t активен"; else echo "  ОШИБКА таймер $t не активен"; fail=1; fi
  done
  if [ -s "$APP/backup.passphrase" ]; then echo "  ok  ключ шифрования бэкапов на месте"; else echo "  ОШИБКА нет $APP/backup.passphrase"; fail=1; fi

  if [ "$fail" = 0 ]; then echo "  SMOKE OK"; else echo "  SMOKE FAIL"; exit 1; fi
}

# Замена токена бота после перевыпуска в BotFather. Шаг env существующий .env не
# трогает (там секреты, созданные один раз), поэтому ротации нужен свой шаг:
# сверить новый токен с Telegram ДО правки, заменить одну строку, сохранить копию,
# пересоздать контейнеры (env_file читается только при создании) и убедиться, что
# прежний токен отозван. Сам токен в вывод не попадает — только коды ответов.
step_token() {
  secrets="$STAGE/secrets.env"
  [ -f "$secrets" ] || { echo "  нет $secrets"; exit 1; }
  # shellcheck disable=SC1090
  . "$secrets"
  new="${TELEGRAM_BOT_TOKEN:-}"
  [ -n "$new" ] || { echo "  в secrets.env нет TELEGRAM_BOT_TOKEN"; shred -u "$secrets" 2>/dev/null || rm -f "$secrets"; exit 1; }
  case "$new" in
    *[!A-Za-z0-9:_-]*) echo "  токен содержит недопустимые символы"; shred -u "$secrets" 2>/dev/null || rm -f "$secrets"; exit 1 ;;
  esac
  old=$(grep '^TELEGRAM_BOT_TOKEN=' "$APP/.env" | cut -d= -f2-)
  if [ "$old" = "$new" ]; then
    echo "  этот токен уже стоит в .env — менять нечего"
    shred -u "$secrets" 2>/dev/null || rm -f "$secrets"
    return 0
  fi

  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -x http://127.0.0.1:12334 "https://api.telegram.org/bot$new/getMe")
  if [ "$code" != 200 ]; then
    echo "  новый токен Telegram не принимает (HTTP $code) — .env не трогаю"
    shred -u "$secrets" 2>/dev/null || rm -f "$secrets"
    exit 1
  fi
  echo "  новый токен принят Telegram: getMe HTTP 200"

  cp -p "$APP/.env" "$APP/.env.bak-$(date +%s)"
  umask 077
  awk -v t="$new" '/^TELEGRAM_BOT_TOKEN=/ {print "TELEGRAM_BOT_TOKEN=" t; next} {print}' "$APP/.env" > "$APP/.env.new"
  chmod 600 "$APP/.env.new"
  mv -f "$APP/.env.new" "$APP/.env"
  shred -u "$secrets" 2>/dev/null || rm -f "$secrets"
  # shellcheck disable=SC2012
  ls -1t "$APP"/.env.bak-* 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r f; do rm -f "$f"; done

  old_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -x http://127.0.0.1:12334 "https://api.telegram.org/bot$old/getMe")
  echo "  прежний токен: getMe HTTP $old_code (401 — отозван)"

  echo "  пересоздаю бот и API с новым токеном"
  compose "$(current_tag)" up -d --force-recreate bot api
  if wait_healthy 120; then echo "  контейнеры здоровы"; else echo "  ОШИБКА: контейнеры не стали здоровыми"; exit 1; fi
}

# Кнопка меню бота — адрес, по которому мини-апп открывают из Telegram. В коде бота
# его нет: он хранится у Telegram. 10.09.2026 кнопка всё ещё вела на адрес прежнего
# развёртывания (не отвечает), и мини-апп «не работал» при исправном сервере: из
# Telegram не пришло ни одного запроса. Шаг ставит кнопку на текущий домен и читает
# её обратно. Чтение сразу после установки отдаёт прежнее значение (замечено
# 10.09.2026: ok на установку, старый адрес при чтении, новый — через минуту),
# поэтому читать с повтором, а не один раз.
step_menu() {
  [ -n "$DOMAIN" ] || { echo "  DOMAIN не задан"; exit 1; }
  token=$(grep '^TELEGRAM_BOT_TOKEN=' "$APP/.env" | cut -d= -f2-)
  [ -n "$token" ] || { echo "  в .env нет TELEGRAM_BOT_TOKEN"; exit 1; }
  api="https://api.telegram.org/bot$token"
  url="https://$DOMAIN/"
  body=$(printf '{"type":"web_app","text":"%s","web_app":{"url":"%s"}}' "${MENU_TEXT:-Signal Bot}" "$url")

  resp=$(curl -s --max-time 20 -x http://127.0.0.1:12334 "$api/setChatMenuButton" --data-urlencode "menu_button=$body")
  case "$resp" in
    *'"ok":true'*) echo "  setChatMenuButton: ok" ;;
    *) echo "  ОШИБКА setChatMenuButton: $resp"; exit 1 ;;
  esac
  i=0
  while [ $i -lt 12 ]; do
    now=$(curl -s --max-time 20 -x http://127.0.0.1:12334 "$api/getChatMenuButton")
    case "$now" in
      *"\"url\":\"$url\""*) echo "  ok  кнопка меню ведёт на $url"; return 0 ;;
    esac
    i=$((i + 1)); sleep 10
  done
  echo "  ОШИБКА кнопка меню через 2 минуты всё ещё: $now"
  exit 1
}

# Ключ OpenRouter для ИИ-трейдера (docs/AI_TRADER_PLAN.md) из $STAGE/secrets.env.
# Шаг env существующий .env не трогает, поэтому у ключа свой шаг, как у токена.
# Ключ проверяется у OpenRouter ДО правки .env и через прокси хоста: с этого IP
# напрямую OpenRouter отвечает 403, поэтому рядом с ключом ставится AI_PROXY_URL.
# Ключ не попадает ни в вывод, ни в аргументы процессов (они видны в ps):
# curl получает его конфигом через stdin, awk — через окружение.
step_ai_key() {
  secrets="$STAGE/secrets.env"
  [ -f "$secrets" ] || { echo "  нет $secrets"; exit 1; }
  # shellcheck disable=SC1090
  . "$secrets"
  shred -u "$secrets" 2>/dev/null || rm -f "$secrets"
  key="${OPENROUTER_API_KEY:-}"
  [ -n "$key" ] || { echo "  в secrets.env нет OPENROUTER_API_KEY"; exit 1; }
  case "$key" in
    *[!A-Za-z0-9_-]*) echo "  ключ содержит недопустимые символы"; exit 1 ;;
  esac

  answer=$(printf 'header = "Authorization: Bearer %s"\n' "$key" \
    | curl -s -K - --max-time 20 -x http://127.0.0.1:12334 -w '\n%{http_code}' https://openrouter.ai/api/v1/key) || true
  code=$(printf '%s\n' "$answer" | tail -n 1)
  if [ "$code" != 200 ]; then
    echo "  OpenRouter ключ не принял (HTTP $code) — .env не трогаю"
    exit 1
  fi
  echo "  ключ принят OpenRouter: $(printf '%s\n' "$answer" | sed '$d' | grep -oE '"(limit|limit_remaining|usage)":[^,}]*' | tr '\n' ' ')"

  cp -p "$APP/.env" "$APP/.env.bak-$(date +%s)"
  umask 077
  AI_KEY="$key" awk -v proxy="http://host.docker.internal:12334" '
    /^OPENROUTER_API_KEY=/ { print "OPENROUTER_API_KEY=" ENVIRON["AI_KEY"]; seen_key = 1; next }
    /^AI_PROXY_URL=/       { print "AI_PROXY_URL=" proxy; seen_proxy = 1; next }
    { print }
    END {
      if (!seen_key)   print "OPENROUTER_API_KEY=" ENVIRON["AI_KEY"]
      if (!seen_proxy) print "AI_PROXY_URL=" proxy
    }' "$APP/.env" > "$APP/.env.new"
  chmod 600 "$APP/.env.new"
  mv -f "$APP/.env.new" "$APP/.env"
  # shellcheck disable=SC2012
  ls -1t "$APP"/.env.bak-* 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r f; do rm -f "$f"; done

  echo "  пересоздаю бот и API с ключом OpenRouter"
  compose "$(current_tag)" up -d --force-recreate bot api
  if wait_healthy 120; then echo "  контейнеры здоровы"; else echo "  ОШИБКА: контейнеры не стали здоровыми"; exit 1; fi
}

step_bybit_key() {
  # Ключи демо-счёта Bybit (11.09.2026). Проверка на демо-бирже ДО правки .env;
  # ключ и секрет — через stdin контейнера и окружение awk, не аргументами
  # процессов; в вывод — только баланс демо-счёта или текст ошибки биржи.
  # Режим торговли не меняется и контейнеры не пересоздаются — это шаг mode.
  secrets="$STAGE/secrets.env"
  [ -f "$secrets" ] || { echo "  нет $secrets"; exit 1; }
  # shellcheck disable=SC1090
  . "$secrets"
  shred -u "$secrets" 2>/dev/null || rm -f "$secrets"
  key="${BYBIT_API_KEY:-}"
  secret="${BYBIT_API_SECRET:-}"
  { [ -n "$key" ] && [ -n "$secret" ]; } || { echo "  в secrets.env нет BYBIT_API_KEY и BYBIT_API_SECRET"; exit 1; }
  case "$key$secret" in
    *[!A-Za-z0-9]*) echo "  ключ или секрет содержат недопустимые символы"; exit 1 ;;
  esac

  answer=$(printf '%s\n%s\n' "$key" "$secret" | docker exec -i market-bot python -c '
import sys
key, secret = sys.stdin.read().split()
from exchange.bybit_client import BybitClient
wallet = BybitClient(api_key=key, api_secret=secret, demo=True).get_wallet_balance()
print("OK equity=%.2f available=%.2f" % (float(wallet.total_equity), float(wallet.available_balance)))
' 2>&1 | tail -n 1) || true
  case "$answer" in
    OK*) echo "  ключ принят демо-биржей Bybit: ${answer#OK }" ;;
    *) echo "  демо-биржа ключ не приняла — .env не трогаю: $answer"; exit 1 ;;
  esac

  cp -p "$APP/.env" "$APP/.env.bak-$(date +%s)"
  umask 077
  BYBIT_KEY="$key" BYBIT_SECRET="$secret" awk '
    /^BYBIT_API_KEY=/    { print "BYBIT_API_KEY=" ENVIRON["BYBIT_KEY"]; seen_key = 1; next }
    /^BYBIT_API_SECRET=/ { print "BYBIT_API_SECRET=" ENVIRON["BYBIT_SECRET"]; seen_secret = 1; next }
    { print }
    END {
      if (!seen_key)    print "BYBIT_API_KEY=" ENVIRON["BYBIT_KEY"]
      if (!seen_secret) print "BYBIT_API_SECRET=" ENVIRON["BYBIT_SECRET"]
    }' "$APP/.env" > "$APP/.env.new"
  chmod 600 "$APP/.env.new"
  mv -f "$APP/.env.new" "$APP/.env"
  # shellcheck disable=SC2012
  ls -1t "$APP"/.env.bak-* 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r f; do rm -f "$f"; done
  echo "  ключи записаны в .env; режим торговли не менялся — переключение: deploy.sh mode demo"
}

step_mode() {
  # Режим торговли прода: demo — ордера на демо-счёт Bybit с потолком капитала
  # 100 $, paper — бумажная торговля. Меняет только флаги режима в .env.
  target="${1:-}"
  case "$target" in
    demo)
      grep -qE '^BYBIT_API_KEY=[A-Za-z0-9]+$' "$APP/.env" \
        || { echo "  в .env нет ключа Bybit — сначала deploy.sh bybit-key"; exit 1; }
      pairs="PAPER_TRADING=false DRY_RUN=false LIVE_TRADING=false BYBIT_TESTNET=false BYBIT_DEMO=true REAL_CAPITAL_CAP_USDT=100 RISK_PERCENT=1"
      expected=TESTNET; others=False ;;
    paper)
      pairs="PAPER_TRADING=true DRY_RUN=true LIVE_TRADING=false BYBIT_TESTNET=false BYBIT_DEMO=false REAL_CAPITAL_CAP_USDT=0 RISK_PERCENT=2"
      expected=PAPER_TRADING; others=True ;;
    *) echo "  использование: deploy.sh mode demo|paper"; exit 2 ;;
  esac

  # Открытые сделки другого режима: сверка при старте закрыла бы бумажные по цене
  # входа с PnL 0, а бумажный монитор биржевые не ведёт — переключаемся на чистом журнале
  open=$(docker exec market-bot python -c "import database; print(database.count_open_trades(on_exchange=$others))" 2>/dev/null || echo "?")
  [ "$open" = 0 ] || { echo "  в журнале открытых сделок другого режима: $open — дождитесь их закрытия и повторите"; exit 1; }

  cp -p "$APP/.env" "$APP/.env.bak-$(date +%s)"
  umask 077
  PAIRS="$pairs" awk '
    BEGIN {
      n = split(ENVIRON["PAIRS"], kv, " ")
      for (i = 1; i <= n; i++) { split(kv[i], p, "="); want[p[1]] = p[2]; order[i] = p[1] }
    }
    {
      name = $0; sub(/=.*/, "", name)
      if (name in want) { print name "=" want[name]; done[name] = 1; next }
      print
    }
    END { for (i = 1; i <= n; i++) if (!(order[i] in done)) print order[i] "=" want[order[i]] }
  ' "$APP/.env" > "$APP/.env.new"
  chmod 600 "$APP/.env.new"
  mv -f "$APP/.env.new" "$APP/.env"
  # shellcheck disable=SC2012
  ls -1t "$APP"/.env.bak-* 2>/dev/null | tail -n +$((KEEP + 1)) | while read -r f; do rm -f "$f"; done
  echo "$expected" > "$APP/trading_mode.expected"

  echo "  режим $target — пересоздаю бот и API"
  compose "$(current_tag)" up -d --force-recreate bot api
  if wait_healthy 120; then echo "  контейнеры здоровы"; else echo "  ОШИБКА: контейнеры не стали здоровыми"; exit 1; fi
  now=$(docker exec market-bot python -c "from trading_mode import get_trading_mode, uses_demo_endpoint; print(get_trading_mode().value, 'DEMO' if uses_demo_endpoint() else '')" 2>/dev/null || echo "?")
  echo "  бот сообщает режим: $now"
}

step_status() {
  echo "  релиз: $(current_tag)"
  docker ps --filter name=market-bot --format '  {{.Names}}  {{.Status}}'
  echo "  последние строки бота:"
  # Токены вычищаются и здесь, независимо от того, что пишет сам бот. 10.09.2026
  # этот шаг вывел хвост лога, где httpx записал URL запросов к Telegram вместе с
  # токеном, — и токен ушёл в вывод сессии, из которой запускали деплой.
  docker logs --tail 15 market-bot 2>&1 \
    | sed -E 's/(bot[0-9]{5,12}):[A-Za-z0-9_-]{20,}/\1:***/g; s/^/    /'
}

step="${1:-}"
[ $# -gt 0 ] && shift
case "$step" in
  install) step_install "$@" ;;
  env)     step_env ;;
  release) step_release "$@" ;;
  web)     step_web ;;
  web-rollback) step_web_rollback ;;
  nginx)   step_nginx ;;
  token)   step_token ;;
  ai-key)  step_ai_key ;;
  bybit-key) step_bybit_key ;;
  mode)    step_mode "$@" ;;
  menu)    step_menu ;;
  backup)  "$APP/backup.sh" ;;
  watchdog) "$APP/watchdog.sh" ;;
  support) step_support ;;
  smoke)   step_smoke ;;
  status)  step_status ;;
  *) echo "шаги: install <домен> | env | release <sha> | web | web-rollback | nginx | token | ai-key | bybit-key | mode demo|paper | menu | backup | watchdog | support | smoke | status"; exit 2 ;;
esac
