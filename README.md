# Market Bot - Торговая система

**Версия архитектуры:** 1.0 (FROZEN)  
**Статус:** Production Ready

---

## 🚨 ВАЖНО: Архитектурная заморозка

**Архитектура системы версии 1.0 заморожена.**

Все архитектурные изменения должны следовать формальному процессу ADR (Architecture Decision Records).

**См. документы:**
- 📋 [ARCHITECTURE_FREEZE_v1.0.md](archive/ARCHITECTURE_FREEZE_v1.0.md) — официальное объявление о заморозке
- 🏗️ [SYSTEM_ARCHITECTURE_CANONICAL.md](SYSTEM_ARCHITECTURE_CANONICAL.md) — каноническая архитектура (единственный источник истины)
- 📝 [docs/adr/README.md](docs/adr/README.md) — процесс ADR

---

## 📚 Документация

### Архитектура
- **Каноническая архитектура:** [SYSTEM_ARCHITECTURE_CANONICAL.md](SYSTEM_ARCHITECTURE_CANONICAL.md)
- **Архитектурная заморозка:** [ARCHITECTURE_FREEZE_v1.0.md](archive/ARCHITECTURE_FREEZE_v1.0.md)
- **Общая архитектура:** [ARCHITECTURE.md](archive/ARCHITECTURE.md)

### Процесс управления архитектурой
- **Процесс ADR:** [docs/adr/README.md](docs/adr/README.md)
- **Шаблон ADR:** [docs/adr/TEMPLATE.md](docs/adr/TEMPLATE.md)

### Специализированная документация
- [META_DECISION_BRAIN_ARCHITECTURE.md](archive/META_DECISION_BRAIN_ARCHITECTURE.md)
- [COGNITIVE_ENGINE_ARCHITECTURE.md](contracts/COGNITIVE_ENGINE_ARCHITECTURE.md)
- [PORTFOLIO_BRAIN_ARCHITECTURE.md](contracts/PORTFOLIO_BRAIN_ARCHITECTURE.md)
- [POSITION_SIZER_ARCHITECTURE.md](contracts/POSITION_SIZER_ARCHITECTURE.md)
- [DRIFT_DETECTOR_ARCHITECTURE.md](contracts/DRIFT_DETECTOR_ARCHITECTURE.md)
- [REPLAY_ENGINE_ARCHITECTURE.md](contracts/REPLAY_ENGINE_ARCHITECTURE.md)
- [SIGNAL_SNAPSHOT_ARCHITECTURE.md](contracts/SIGNAL_SNAPSHOT_ARCHITECTURE.md)
- [DECISION_TRACE_ARCHITECTURE.md](contracts/DECISION_TRACE_ARCHITECTURE.md)
- [MARKET_STATE_ARCHITECTURE.md](contracts/MARKET_STATE_ARCHITECTURE.md)

### Операционная документация
- [deploy/README.md](deploy/README.md) — прод: деплой, откат, бэкап, алерты
- [docs/REMEDIATION_PLAN.md](docs/REMEDIATION_PLAN.md) — план исправлений и статус задач
- [docs/AUDIT_2026-09-10.md](docs/AUDIT_2026-09-10.md) — аудит, из которого вырос план
- [operations/RUNTIME_TESTS_README.md](operations/RUNTIME_TESTS_README.md) — runtime-тесты
- [operations/RSO_README.md](operations/RSO_README.md) — RSO

---

## 🏗️ Архитектурные принципы

Система построена на следующих принципах (см. [SYSTEM_ARCHITECTURE_CANONICAL.md](SYSTEM_ARCHITECTURE_CANONICAL.md)):

1. **Fail-Safe First:** Торговля блокируется при любой неопределённости
2. **Single Source of Truth:** DecisionCore — единственный источник истины
3. **No Bypass:** Нет обходных путей для DecisionCore или SystemGuardian
4. **Architecture == Runtime:** Документация точно отражает код

---

## 🔒 Критические модули

Следующие модули являются критическими и требуют ADR для любых изменений:

- **DecisionCore** (`core/decision_core.py`) — единая точка принятия решений
- **SystemGuardian** (`core/system_guardian.py`) — принуждение инвариантов
- **SystemStateMachine** (`system_state_machine.py`) — управление системными состояниями
- **Gatekeeper** (`execution/gatekeeper.py`) — проверка сигналов перед отправкой
- **RiskExposureBrain** (`brains/risk_exposure_brain.py`) — расчёт риска и экспозиции

---

## 📋 Системные инварианты

Система гарантирует следующие инварианты (см. [SYSTEM_ARCHITECTURE_CANONICAL.md](SYSTEM_ARCHITECTURE_CANONICAL.md)):

- **INV-1:** CRITICAL MODULE AVAILABILITY
- **INV-2:** DECISION CORE AUTHORITY
- **INV-3:** SYSTEM STATE CONSISTENCY
- **INV-4:** GUARDIAN-FIRST ENFORCEMENT
- **INV-5:** NO FAIL-OPEN FOR CRITICAL

**Любое изменение инварианта требует ADR.**

---

## 🚀 Быстрый старт

### Установка

```bash
# Клонировать репозиторий
git clone <repository-url>
cd market_bot

# Создать виртуальное окружение
python -m venv venv
source venv/bin/activate  # Linux/Mac
# или
venv\Scripts\activate  # Windows

# Установить зависимости
pip install -r requirements.txt
```

### Запуск

Локально — из корня проекта, с `.env` по образцу `.env.example` (режим торговли
задаётся там же, по умолчанию ордера на биржу не уходят):

```bash
python runner.py
```

Прод — Docker Compose и `deploy/deploy.sh`, одной командой с рабочей машины:

```bash
DEPLOY_HOST=<host> deploy/ship.sh release web smoke
```

Подробно — [deploy/README.md](deploy/README.md): первый запуск, откат, бэкап и его
копия вне сервера, алерты. Прежние пути — systemd-юниты, `install.sh`,
`setup_*.sh`, `scripts/deploy.sh` — удалены 10.09.2026: они описывали другой
сервер и разошлись с кодом.


### Режимы торговли

Режим задаётся флагами в `.env`; разбирает их одно место — `trading_mode.py`.
Неразборчивое значение флага — ошибка запуска, а не тихое «нет».

| Флаги | Режим | Что происходит |
|---|---|---|
| `LIVE_TRADING=true` | LIVE | реальные ордера на mainnet, реальные деньги |
| `PAPER_TRADING=true` | PAPER | виртуальные сделки, биржа для ордеров не вызывается |
| `BYBIT_TESTNET=true` и `DRY_RUN=false` | TESTNET | реальные ордера на testnet |
| иначе | DRY_RUN | только сигналы |

Прод сейчас в PAPER. Переход дальше — ступенями из `docs/REMEDIATION_PLAN.md` (Фаза 7).

---

## 🔧 Разработка

### Процесс внесения изменений

1. **Обычные изменения (bug fixes, рефакторинг):**
   - Создайте Pull Request
   - Убедитесь, что тесты проходят
   - Убедитесь, что инварианты не нарушены

2. **Архитектурные изменения:**
   - **ОБЯЗАТЕЛЬНО** создайте ADR (см. [docs/adr/README.md](docs/adr/README.md))
   - Следуйте процессу ADR
   - Обновите каноническую архитектуру (если требуется)

### Тестирование

```bash
# Запустить тесты
python -m pytest tests/

# Запустить runtime тесты
./runtime_tests.sh  # Linux/Mac
.\runtime_tests.ps1  # Windows
```

---

## 📞 Контакты и поддержка

Для вопросов об архитектуре:
- См. [SYSTEM_ARCHITECTURE_CANONICAL.md](SYSTEM_ARCHITECTURE_CANONICAL.md)
- См. [ARCHITECTURE_FREEZE_v1.0.md](archive/ARCHITECTURE_FREEZE_v1.0.md)
- См. [docs/adr/README.md](docs/adr/README.md)

---

## 📄 Лицензия

[Укажите лицензию проекта]

---

**Примечание:** Этот проект следует строгим архитектурным принципам fail-safe дизайна. Все изменения архитектуры должны проходить через формальный процесс ADR.

