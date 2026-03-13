# Market Bot - Торговая система

**Версия архитектуры:** 1.0 (FROZEN)  
**Статус:** Production Ready

---

## 🚨 ВАЖНО: Архитектурная заморозка

**Архитектура системы версии 1.0 заморожена.**

Все архитектурные изменения должны следовать формальному процессу ADR (Architecture Decision Records).

**См. документы:**
- 📋 [ARCHITECTURE_FREEZE_v1.0.md](ARCHITECTURE_FREEZE_v1.0.md) — официальное объявление о заморозке
- 🏗️ [SYSTEM_ARCHITECTURE_CANONICAL.md](SYSTEM_ARCHITECTURE_CANONICAL.md) — каноническая архитектура (единственный источник истины)
- 📝 [docs/adr/README.md](docs/adr/README.md) — процесс ADR

---

## 📚 Документация

### Архитектура
- **Каноническая архитектура:** [SYSTEM_ARCHITECTURE_CANONICAL.md](SYSTEM_ARCHITECTURE_CANONICAL.md)
- **Архитектурная заморозка:** [ARCHITECTURE_FREEZE_v1.0.md](ARCHITECTURE_FREEZE_v1.0.md)
- **Общая архитектура:** [ARCHITECTURE.md](ARCHITECTURE.md)

### Процесс управления архитектурой
- **Процесс ADR:** [docs/adr/README.md](docs/adr/README.md)
- **Шаблон ADR:** [docs/adr/ADR-XXXX-title.md](docs/adr/ADR-XXXX-title.md)

### Специализированная документация
- [META_DECISION_BRAIN_ARCHITECTURE.md](META_DECISION_BRAIN_ARCHITECTURE.md)
- [COGNITIVE_ENGINE_ARCHITECTURE.md](COGNITIVE_ENGINE_ARCHITECTURE.md)
- [PORTFOLIO_BRAIN_ARCHITECTURE.md](PORTFOLIO_BRAIN_ARCHITECTURE.md)
- [POSITION_SIZER_ARCHITECTURE.md](POSITION_SIZER_ARCHITECTURE.md)
- [DRIFT_DETECTOR_ARCHITECTURE.md](DRIFT_DETECTOR_ARCHITECTURE.md)
- [REPLAY_ENGINE_ARCHITECTURE.md](REPLAY_ENGINE_ARCHITECTURE.md)
- [SIGNAL_SNAPSHOT_ARCHITECTURE.md](SIGNAL_SNAPSHOT_ARCHITECTURE.md)
- [DECISION_TRACE_ARCHITECTURE.md](DECISION_TRACE_ARCHITECTURE.md)
- [MARKET_STATE_ARCHITECTURE.md](MARKET_STATE_ARCHITECTURE.md)

### Операционная документация
- [START_BOT.md](START_BOT.md) — запуск бота
- [SERVER_SETUP.md](SERVER_SETUP.md) — настройка сервера
- [SERVICE_SETUP.md](SERVICE_SETUP.md) — настройка systemd service

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

См. [START_BOT.md](START_BOT.md) для подробных инструкций.

**Быстрый запуск:**
```bash
python runner.py
```

**Запуск как systemd service (рекомендуется):**
```bash
sudo cp market-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable market-bot
sudo systemctl start market-bot
```

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
- См. [ARCHITECTURE_FREEZE_v1.0.md](ARCHITECTURE_FREEZE_v1.0.md)
- См. [docs/adr/README.md](docs/adr/README.md)

---

## 📄 Лицензия

[Укажите лицензию проекта]

---

**Примечание:** Этот проект следует строгим архитектурным принципам fail-safe дизайна. Все изменения архитектуры должны проходить через формальный процесс ADR.

