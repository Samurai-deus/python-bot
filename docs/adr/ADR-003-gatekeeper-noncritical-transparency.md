# ADR-003: Gatekeeper NON_CRITICAL Module Failure Transparency

**Status:** ACCEPTED
**Date:** 2026-03-13
**Deciders:** Project team

---

## Context

`execution/gatekeeper.py` импортирует `MetaDecisionBrain` и `PositionSizer` через `try/except`
с fail-open поведением:

- `ImportError` → `META_DECISION_AVAILABLE = False` (только WARNING лог)
- Init failure → `self.meta_decision_brain = None` (WARNING, нет перехода в DEGRADED)
- Runtime exception → `return None` (WARNING, нет перехода в DEGRADED)

Система продолжала работу в состоянии `RUNNING` даже когда NON_CRITICAL модули недоступны.
Оператор не получал сигнала о деградации системы.

Архитектура уже поддерживает правильное поведение: `PolicyEnforcer.enforce_fail_safe_policy()`
в `system_guardian.py` переводит систему в `DEGRADED` при сбое NON_CRITICAL модуля.
Но этот механизм не вызывался из gatekeeper.

Затронутые инварианты:
- **INV-5** (spirit): Критичные и важные модули не должны fail-open без уведомления системы

---

## Decision

Сохранить классификацию `NON_CRITICAL` для `MetaDecisionBrain` и `PositionSizer`.

**Все сбои** (init failure, runtime exception) должны вызывать
`SystemGuardian.report_module_failure_sync()`, что приводит к:
- Переходу системы в состояние `DEGRADED` (не `SAFE_HALT`)
- Торговля продолжается (DEGRADED ≠ SAFE_HALT)
- Оператор получает явный сигнал о деградации

**ImportError при загрузке модуля** остаётся без репортинга:
на момент импорта `SystemGuardian` ещё не инициализирован.

Структура `try/except` сохраняется — добавляется только репортинг.

---

## Implementation

1. Добавить `report_module_failure_sync()` в `SystemGuardian` как sync-обёртку
   над `policy_enforcer.enforce_fail_safe_policy()` (паттерн `AsyncToSyncAdapter`,
   идентичный `can_trade_sync()`).

2. В `gatekeeper.py` — 4 точки вызова:
   - `MetaDecisionBrain` init failure → `report_module_failure_sync("MetaDecisionBrain", "init_failure", ...)`
   - `PositionSizer` init failure → `report_module_failure_sync("PositionSizer", "init_failure", ...)`
   - `MetaDecisionBrain` runtime error → `report_module_failure_sync("MetaDecisionBrain", "runtime_error", ...)`
   - `PositionSizer` runtime error → `report_module_failure_sync("PositionSizer", "runtime_error", ...)`

3. `logger.warning` → `logger.error` в этих блоках (сбой важного модуля — это ошибка).

---

## Consequences

**Positive:**
- Оператор немедленно узнаёт о деградации через систему мониторинга
- Соответствие духу INV-5 без изменения CRITICAL/NON_CRITICAL классификации
- Торговля не прерывается (DEGRADED продолжает работу)

**Negative:**
- Повторные runtime ошибки будут многократно вызывать `enforce_fail_safe_policy`;
  идемпотентность зависит от реализации PolicyEnforcer (приемлемо для текущей фазы)

**Neutral:**
- `META_DECISION_AVAILABLE = False` при ImportError не репортируется
  (SystemGuardian недоступен на момент импорта)
