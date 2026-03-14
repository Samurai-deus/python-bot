# Market Bot — Контекст проекта для Claude

## Что это за проект

Автоматизированный бот для торговли криптовалютой на бирже **Bybit**.
Написан на **Python 3.12+**, использует AsyncIO, SQLite, Telegram Bot API.

**Конечная цель:** Профессиональный торговый бот с UI в виде Telegram Mini App (React + TypeScript).

---

## Текущая стадия (на 13.03.2026)

**Статус: Signal Generator — НЕ торговый бот**
**Фаза 0 завершена. Текущая активная ветка: develop**

Бот генерирует торговые сигналы и отправляет уведомления в Telegram.
Реального исполнения сделок НЕТ. Только чтение рыночных данных с Bybit (read-only API).

---

## История фаз

| Фаза | Статус | Дата |
|------|--------|------|
| Фаза 0: Стабилизация | ✅ ЗАВЕРШЕНА | 13.03.2026 |
| Фаза 1: Завершение ядра | 🔄 В РАБОТЕ | — |
| Фаза 2–7 | ⏳ Запланировано | — |

---

## Расположение

```
C:/Users/Дмитрий/Documents/market_bot/   ← основной проект (184 МБ)
C:/Users/Дмитрий/Projects/MyTelegramBot/  ← заброшенный проект, не трогать
```

---

## Технологический стек

| Компонент | Технология |
|-----------|-----------|
| Язык | Python 3.12+ |
| Async | asyncio |
| HTTP | httpx, aiohttp, requests |
| Telegram | python-telegram-bot >= 20.7 |
| База данных | SQLite (database.py) |
| Биржа | Bybit (только чтение, `/v5/market/kline`) |
| Деплой | systemd (Linux), batch (Windows) |

---

## Структура проекта

```
market_bot/
├── core/                   # Ядро принятия решений
│   ├── decision_core.py    # ГЛАВНЫЙ — единственный источник решений о торговле
│   ├── system_guardian.py  # Контролёр инвариантов системы
│   ├── risk_core.py        # Политики риска (FSM: SAFE/LIMITED/LOCKED/HALTED)
│   ├── market_state.py     # Состояние рынка (enum A/B/C/D)
│   ├── portfolio_brain.py  # Анализ портфеля
│   ├── position_sizer.py   # Расчёт размера позиции
│   ├── cognitive_engine.py # Уверенность/энтропия решений
│   ├── drift_detector.py   # Обнаружение деградации стратегии
│   ├── replay_engine.py    # Бэктест / воспроизведение сигналов
│   ├── signal_snapshot.py  # Иммутабельный снимок сигнала
│   ├── signal_snapshot_store.py  # Хранение снимков в SQLite
│   ├── decision_trace.py   # Объяснение принятых решений
│   ├── system_state_machine.py  # FSM: RUNNING/DEGRADED/SAFE_HALT/RECOVERY/FATAL
│   ├── timeout_guard.py    # Таймауты для критичных модулей
│   └── data_validator.py
│
├── brains/                 # Специализированные аналитические модули
│   ├── meta_decision_brain.py    # Когда НЕ торговать (HARD_BLOCK/SOFT_BLOCK/ALLOW)
│   ├── market_regime_brain.py    # Режим рынка (тренд/флет)
│   ├── risk_exposure_brain.py    # Расчёт допустимой экспозиции
│   ├── cognitive_filter.py       # Защита от когнитивных искажений
│   └── opportunity_awareness.py  # Обнаружение паттернов
│
├── execution/
│   └── gatekeeper.py       # Валидация сигналов перед отправкой (52KB)
│
├── docs/adr/               # Architecture Decision Records
│   └── ADR-002-fastapi-dependency-lifecycle.md
│
├── contracts/              # Контракты модулей (интерфейсы и инварианты)
│   └── ...                 # MarketState, SignalSnapshot и др.
│
├── operations/             # Операционные документы
│   └── ...                 # SERVER_SETUP, SERVICE_SETUP, START_BOT
│
├── archive/                # Исторические документы
│   └── PRODUCTION_DEBUG_REPORT.md
│
├── runner.py               # Главный цикл бота (точка входа, 6000+ строк)
├── signal_generator.py     # Генерация торговых сигналов
├── data_loader.py          # Загрузка свечей с Bybit API
├── database.py             # SQLite операции
├── telegram_bot.py         # Telegram соединение (токен загружается из .env)
├── telegram_commands.py    # Обработчики команд
├── config.py               # Конфигурация: 24 символа, таймфреймы, риск
├── indicators.py           # Технические индикаторы
├── risk.py                 # Расчёты риска
├── scoring.py              # Скоринг сигналов
├── .env.example            # Шаблон переменных окружения
├── .pre-commit-config.yaml # Хуки pre-commit (black, isort, flake8)
├── requirements.txt
└── requirements-dev.txt    # Зависимости для разработки
```

---

## Архитектурные принципы (FROZEN v1.0, с 19.12.2024)

1. **Fail-Safe First** — блокировка торговли при любой неопределённости
2. **Single Source of Truth** — DecisionCore — единственный авторитет
3. **No Bypass** — нельзя обойти DecisionCore или SystemGuardian
4. **Architecture == Runtime** — документация должна соответствовать коду
5. Любые изменения архитектуры требуют ADR (Architecture Decision Record)

### Системные инварианты

| ID | Инвариант | Нарушение → |
|----|-----------|-------------|
| INV-1 | Критичные модули должны быть доступны | SAFE_HALT |
| INV-2 | Все сделки через DecisionCore | SAFE_HALT |
| INV-3 | Консистентность StateМachine | SAFE_HALT |
| INV-4 | SystemGuardian проверяется первым | SAFE_HALT |
| INV-5 | Критичные модули не fail-open | SAFE_HALT |

### Состояния системы

```
RUNNING   → нормальная работа, торговля включена
DEGRADED  → некритичные модули недоступны, торговля продолжается
SAFE_HALT → торговля заблокирована (TTL 600с), авто-восстановление
RECOVERY  → восстановление, торговля отключена
FATAL     → терминальное состояние, процесс завершается
```

### Таймауты модулей

| Модуль | Таймаут | Тип |
|--------|---------|-----|
| DecisionCore | 5с | CRITICAL |
| SystemStateMachine | 0.1с | CRITICAL |
| SystemGuardian | 0.1с | CRITICAL |
| Gatekeeper | 5с | CRITICAL |
| RiskExposureBrain | 3с | CRITICAL |
| MetaDecisionBrain | 3с | NON_CRITICAL |
| MarketRegimeBrain | 5с | NON_CRITICAL |
| PortfolioBrain | 5с | NON_CRITICAL |
| PositionSizer | 3с | NON_CRITICAL |
| TelegramBot | 10с | NON_CRITICAL |

---

## Конфигурация торговли (config.py)

- **Начальный баланс:** $10,000 USDT
- **Риск на сделку:** 2%
- **Мин. размер позиции:** $10 USDT
- **Макс. размер позиции:** $1,000 USDT
- **24 символа:** BTC, ETH, BNB, SOL, XRP, ADA, DOGE, AVAX, DOT, MATIC, LINK, UNI, AAVE, MKR, ARB, OP, SUI, APT, SHIB, ATOM, NEAR, FTM, ALGO + USDT пары
- **Таймфреймы:** 5m, 15m, 30m, 1h, 4h
- **Интервал анализа:** 300с (5 минут)
- **Bybit API:** `https://api.bybit.com/v5/market/kline` (только чтение)

---

## Известные баги и технический долг

Все известные баги закрыты. Технический долг перед Фазой 5 отсутствует.

| Проблема | Файл | Статус |
|----------|------|--------|
| Старый токен в git history | — | ✅ Отозван в BotFather |
| MetaDecisionBrain/PositionSizer fail-open, нарушает INV-5 | gatekeeper.py | ✅ Закрыт (ADR-004, fail-closed) |
| tests/rso_report_*.json в git | tests/ | ✅ Добавлен в .gitignore |
| qty рассчитывался без учёта qtyStep биржи | gatekeeper.py | ✅ Исправлен (Decimal + floor) |

---

## Pipeline прохождения сигнала (текущий vs целевой)

### Сейчас (реальное состояние):
```
Bybit API → data_loader → signal_generator → indicators
→ Gatekeeper:
    ✓ SystemGuardian.can_trade()           → первая проверка
    ✓ (optional) MetaDecisionBrain.evaluate() → try/except, fail-open если недоступен
    ✓ DecisionCore.should_i_trade()
    ✓ PortfolioBrain.evaluate()
    ✓ (optional) PositionSizer.calculate() → try/except, fail-open если недоступен
→ Telegram уведомление (нет исполнения)
```

### Целевой (полный):
```
Bybit API → data_loader → signal_generator → indicators
→ Gatekeeper:
    ✓ SystemGuardian.can_trade()       → SAFE_HALT если нет
    ✓ MetaDecisionBrain.evaluate()     → стоп если HARD_BLOCK
    ✓ DecisionCore.should_i_trade()    → стоп если нет
    ✓ RiskExposureBrain.evaluate()     → стоп если LOCKED
    ✓ PortfolioBrain.evaluate()        → стоп если лимиты
    ✓ PositionSizer.calculate()        → размер позиции
→ OrderExecutor → Bybit Trading API → позиция открыта
→ PositionTracker → мониторинг SL/TP
→ Telegram уведомление с деталями
```

---

## Полный план развития (дорожная карта)

### Фаза 0: Стабилизация ✅ Завершена 13.03.2026
- [~] Вынести Telegram токен и ключи в `.env` (python-dotenv) — код исправлен, нужно вручную `pip install python-dotenv`
- [x] Закоммитить все текущие изменения по смысловым группам
- [x] Создать ветку `develop`, `main` — только стабильный код
- [x] Настроить `.gitignore` для `venv/`, `*.log`, `.env`
- [x] Удалить дублирующиеся markdown-документы
- [x] Добавить `pre-commit` хуки (black, isort, flake8)

### Фаза 1: Завершение ядра (1–2 недели)
- [x] Интегрировать SystemGuardian в gatekeeper.py (первая проверка)
- [x] Интегрировать MetaDecisionBrain в gatekeeper.py (после Guardian)
- [x] Интегрировать PositionSizer вместо упрощённого расчёта
- [ ] Перевести MetaDecisionBrain/PositionSizer с optional (fail-open) на required (fail-closed, INV-5) — требует ADR
- [ ] Настроить pytest + pytest-asyncio + pytest-cov
- [ ] Покрыть тестами: DecisionCore, RiskCore, PositionSizer, MetaDecisionBrain
- [ ] GitHub Actions CI: тесты на каждый push
- [ ] Цель покрытия: >80%

### Фаза 2: Реальное исполнение сделок (2–3 недели)
- [ ] Добавить `pybit` (официальная библиотека Bybit)
- [ ] Создать `exchange/bybit_client.py` (аутентификация, ордера, позиции)
- [ ] Начать с **Bybit Testnet** (не реальные деньги)
- [ ] Создать `execution/order_executor.py` (размещение ордеров с SL/TP)
- [ ] Создать `execution/position_tracker.py` (мониторинг открытых позиций)
- [ ] Создать `trade_lifecycle.py` (PENDING→OPEN→PARTIAL→CLOSED)
- [ ] Обновить database.py: таблицы orders, positions, trade_history, pnl_history
- [ ] Режимы: `DRY_RUN` / `PAPER_TRADING` / `LIVE_TRADING` через .env

### Фаза 3: Аналитика и бэктест (2–3 недели)
- [ ] Доработать ReplayEngine: slippage, комиссии Bybit, полная статистика
- [ ] Sharpe ratio, Max Drawdown, Win Rate, Profit Factor
- [ ] `analytics/performance_tracker.py`: P&L breakdown по символу/режиму/времени
- [ ] Адаптивные параметры через DriftDetector (снижение риска при деградации)

### Фаза 4: Профессиональный Telegram Bot (1 неделя)
- [ ] Расширенные команды: /status, /balance, /positions, /history, /stats, /settings, /pause, /resume, /close_all, /report
- [ ] Inline кнопки для управления позициями
- [ ] Умные уведомления: открытие/закрытие сделки, ежедневный/еженедельный отчёт
- [ ] Алерты при аномалиях (просадка, ошибка API)

### Фаза 5: Telegram Mini App (3–4 недели)

**Стек:**
- Frontend: React + TypeScript + Vite
- UI Kit: `@telegram-apps/sdk-react` (официальный SDK)
- Стили: Tailwind CSS
- Графики: Lightweight Charts (TradingView, бесплатная)
- Backend: FastAPI (новый `api/` модуль)
- State: Zustand + TanStack Query
- Real-time: WebSocket

**Backend API (`api/`):**
- [ ] `api/main.py` — FastAPI приложение
- [ ] `api/routers/positions.py`
- [ ] `api/routers/signals.py`
- [ ] `api/routers/analytics.py`
- [ ] `api/routers/settings.py`
- [ ] `api/routers/ws.py` — WebSocket real-time
- [ ] Аутентификация через Telegram InitData

**Экраны Mini App:**
- [ ] **Dashboard** — баланс, P&L сегодня/неделя, открытые позиции, статус системы
- [ ] **Positions** — список позиций с real-time P&L, кнопки закрыть/изменить SL
- [ ] **Signals** — лента сигналов, фильтрация, одобрение/отклонение вручную
- [ ] **Analytics** — график P&L, win rate, best/worst символы, drawdown
- [ ] **Risk Monitor** — состояние RiskCore, экспозиция vs лимиты, история блокировок
- [ ] **Settings** — режим торговли, риск (слайдер), символы, временные фильтры
- [ ] **System Health** — статус каждого модуля, uptime, последние ошибки

**Деплой Mini App:**
- [ ] Сборка React → статика, хостинг на Cloudflare Pages или nginx
- [ ] HTTPS обязателен (Let's Encrypt)
- [ ] Регистрация в @BotFather

### Фаза 6: Production (2 недели)
- [ ] VPS: Ubuntu 22.04 LTS (2 CPU, 4 GB RAM минимум)
- [ ] Docker Compose: market-bot, api-server, nginx, postgres, redis
- [ ] Prometheus + Grafana (метрики)
- [ ] Sentry (трекинг ошибок)
- [ ] Ежедневный бэкап БД
- [ ] Rate limiting, IP whitelist, шифрование API ключей в БД
- [ ] 2FA для критических команд

### Фаза 7: Продвинутые функции (ongoing)
- [ ] Grid trading стратегия
- [ ] DCA (Dollar Cost Averaging) режим
- [ ] ML скоринг сигналов (sklearn/lightgbm)
- [ ] Walk-forward optimization
- [ ] Мультибиржевая поддержка (OKX адаптер)
- [ ] Арбитраж между биржами

---

## Приоритет — следующие шаги (в порядке выполнения)

### Ручные действия (pending, требуют выполнить вручную)

1. **СРОЧНО: Отозвать старый токен** — зайти в @BotFather → /mybots → выбрать бота → API Token → Revoke, затем обновить `.env`
2. **Активировать git хуки:** `pre-commit install` (один раз в venv)
3. **Установить зависимости:** `pip install python-dotenv` (или `pip install -r requirements.txt`)

### Фаза 1 (текущий приоритет)

1. **Hardening pipeline:** перевести MetaDecisionBrain/PositionSizer с fail-open на fail-closed (INV-5), с ADR
2. **Bybit Testnet:** первые реальные ордера в тестовой среде
3. **FastAPI backend:** начать `api/` модуль параллельно с Testnet
4. **Mini App:** React scaffolding после стабилизации API

---

## Справочная информация

### Запуск бота
```bash
cd C:/Users/Дмитрий/Documents/market_bot
source venv/bin/activate  # или venv\Scripts\activate на Windows
python runner.py
```

### Переменные окружения (файл `.env`)
```env
TELEGRAM_BOT_TOKEN=...
BYBIT_API_KEY=...
BYBIT_API_SECRET=...
BYBIT_TESTNET=true
BOT_INTERVAL=300
DRY_RUN=true
PAPER_TRADING=false
LIVE_TRADING=false
MAX_CONSECUTIVE_ERRORS=5
ERROR_PAUSE=600
LOG_FILE=monitor.log
```

### Bybit API endpoints (текущие)
- Свечи: `https://api.bybit.com/v5/market/kline`
- Инструменты: `https://api.bybit.com/v5/market/instruments-info`
- Testnet: `https://api-testnet.bybit.com`

### Документация (главный документ)
- `SYSTEM_ARCHITECTURE_CANONICAL.md` — авторитетный источник архитектуры
- `archive/PRODUCTION_DEBUG_REPORT.md` — честный статус реализации (перемещён в archive/)
- `docs/adr/` — Architecture Decision Records

---

## Правила работы с проектом

1. **Не менять архитектуру** без создания ADR в `docs/adr/`
2. **Не обходить** DecisionCore или SystemGuardian — это нарушение INV-2 и INV-4
3. **Все новые модули** должны иметь таймауты через `TimeoutGuard`
4. **Критичные модули** (список выше) — только fail-closed, никогда fail-open
5. **Тесты обязательны** для любого нового кода в `core/` и `brains/`
6. **Secrets никогда** не коммитить в git (`.env` в `.gitignore`)
7. **Bybit Testnet** — обязательный этап перед любым live кодом
