# Деплой market-bot

Прод — один хост с Docker и nginx, рядом с другими проектами. Бот, API и Redis —
в `docker compose` проекта `market-bot`; Mini App — статика под nginx на отдельном
поддомене со своим сертификатом. Соседние сайты хоста не затрагиваются.

| Что | Где на сервере |
|---|---|
| скрипт деплоя | `/opt/market-bot/deploy.sh` (копия `deploy/deploy.sh`) |
| окружение | `/opt/market-bot/.env` (600, создаётся один раз шагом `env`) |
| домен | `/opt/market-bot/deploy.conf` |
| релизы | `/opt/market-bot/releases/<sha>`, хранятся 3 |
| текущий релиз | `/opt/market-bot/current_tag` |
| фронт | `/opt/market-bot/web` → `web-releases/<время>`, хранятся 3; откат — `deploy.sh web-rollback` |
| бэкапы базы | `/opt/market-bot/backups`, 14 дней, таймер `market-bot-backup.timer`; зашифрованная копия — владельцу в Telegram |
| ключ шифрования бэкапов | `/opt/market-bot/backup.passphrase` (600, создаётся при первом релизе) |
| сторож | `/opt/market-bot/watchdog.sh`, таймер `market-bot-watchdog.timer`, состояние `/var/lib/market-bot-watchdog` |
| база | том `market-bot_market_data` → `/data/db/market_bot.db` в контейнерах |

## Первый запуск

```bash
# на сервере, один раз
scp deploy/deploy.sh <host>:/opt/market-bot/deploy.sh
ssh <host> '/opt/market-bot/deploy.sh install <домен>'

# секреты: файл с TELEGRAM_BOT_TOKEN и OWNER_TELEGRAM_ID → $STAGE/secrets.env,
# шаг env создаёт .env и уничтожает файл; остальные секреты генерируются на сервере
ssh <host> '/opt/market-bot/deploy.sh env'

# дальше — обычный деплой
DEPLOY_HOST=<host> deploy/ship.sh release web nginx smoke
```

## Обычный деплой

```bash
DEPLOY_HOST=<host> deploy/ship.sh release web smoke
```

`release` собирает образ с тегом git sha, поднимает его и ждёт, пока все три
контейнера станут healthy. Не стали за 180 секунд — откат на предыдущий релиз.
Отправляется только закоммиченное состояние: с грязным деревом `ship.sh` откажется.

## Что проверяет smoke

- сайт отвечает 200, `index.html` отдаётся с `no-store`;
- API снаружи без initData — 401; `/metrics`, `/docs`, `/openapi.json` снаружи — 404;
- изнутри контейнера API: без подписи 401, чужой пользователь 403, владелец 200;
- все контейнеры healthy; режим торговли — `PAPER_TRADING` (после `deploy.sh mode demo` — `TESTNET`, ожидаемый режим хранится в `/opt/market-bot/trading_mode.expected`);
- Telegram из контейнера бота доступен через прокси.

## Откат вручную

```bash
ssh <host> 'cat /opt/market-bot/current_tag; ls -t /opt/market-bot/releases'
ssh <host> 'cd /opt/market-bot && RELEASE_TAG=<предыдущий sha> docker compose -p market-bot -f docker-compose.yml --env-file .env up -d'
```

## Алерты

Сторож `watchdog.sh` запускается раз в 5 минут и пишет владельцу в Telegram, когда
что-то сломалось, и ещё раз — когда починилось. Пока проблема держится —
напоминание раз в 6 часов. Проверяет:

- контейнеры запущены и healthy; healthcheck бота видит зависший цикл событий и
  остановившийся цикл анализа (метки `utils/liveness.py`);
- бот не перезапускался с прошлой проверки;
- последний бэкап моложе 26 ч, копия вне сервера отправлялась за 26 ч;
- диск заполнен меньше чем на 90 %, сертификат действует ещё 14 дней, сайт отвечает 200.

```bash
ssh <host> /opt/market-bot/deploy.sh watchdog     # проверить сейчас
```

Сторож не видит смерть самого хоста и отказ прокси до Telegram — тогда писать
некому. Косвенный признак — перестала приходить ежедневная копия базы.

## Копия базы вне сервера

Каждый бэкап шифруется gpg (AES256) и уходит владельцу в Telegram документом без
звука. Перед отправкой шифровка расшифровывается и сверяется с архивом побайтно.

Ключ — `/opt/market-bot/backup.passphrase`. **Без копии ключа вне сервера
зашифрованные копии бесполезны**: при потере диска вместе с базой пропадёт и ключ.
Сохраните его в менеджер паролей один раз:

```bash
ssh <host> cat /opt/market-bot/backup.passphrase
```

Расшифровать копию из Telegram:

```bash
gpg --batch --pinentry-mode loopback --passphrase-file backup.passphrase -d market_bot-<время>.db.gz.gpg | gunzip > restore.db
```

## Восстановление базы из бэкапа

```bash
ssh <host>
gunzip -c /opt/market-bot/backups/market_bot-<время>.db.gz > /tmp/restore.db
docker stop market-bot market-bot-api
docker cp /tmp/restore.db market-bot:/data/db/market_bot.db
# файлы WAL от прежней базы применились бы к восстановленной — удалить
docker run --rm -v market-bot_market_data:/data alpine rm -f /data/db/market_bot.db-wal /data/db/market_bot.db-shm
docker start market-bot market-bot-api
```

## Чего здесь пока нет

- внешнего контроля доступности хоста: если хост недоступен целиком, алерт не придёт;
- PostgreSQL — на этапе бумажной торговли база SQLite.

## Демо-счёт Bybit (11.09.2026)

Прогон с настоящими ордерами без реальных денег — на демо-счёте основного
аккаунта (`api-demo.bybit.com`): цены и стакан основной биржи.

1. Ключ создаётся в режиме Demo Trading (права: ордера и позиции, без вывода).
2. `secrets.env` с `BYBIT_API_KEY` и `BYBIT_API_SECRET` кладётся в
   `/tmp/market-bot-deploy/`, затем `deploy.sh bybit-key` — ключ проверяется на
   демо-бирже до записи в `.env`, режим не меняется.
3. `deploy.sh mode demo` — ордера уходят на демо-счёт, капитал виден как счёт в
   1000 $ (`REAL_CAPITAL_CAP_USDT`), своя база капитала `DEMO`; обратно —
   `deploy.sh mode paper`.
