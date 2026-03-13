# ADR-004: MetaDecisionBrain и PositionSizer — перевод в fail-closed (CRITICAL)

**Status:** ACCEPTED
**Date:** 2026-03-13
**Deciders:** Project team
**Supersedes:** ADR-003 (в части классификации MetaDecisionBrain и PositionSizer)

---

## Context

ADR-003 сохранил `MetaDecisionBrain` и `PositionSizer` как `NON_CRITICAL` (fail-open):
- ImportError → `META_DECISION_AVAILABLE = False`, торговля продолжается
- Init failure → модуль устанавливается в `None`, торговля продолжается
- Runtime exception → возвращается `None`, торговля продолжается

Такая схема нарушает **INV-5**: критичные модули не должны fail-open.

Оба модуля являются **обязательными** этапами в pipeline:

```
SystemGuardian → RiskCore → MetaDecisionBrain → DecisionCore → PortfolioBrain → PositionSizer
```

Пропуск любого из них означает, что сигнал проходит без полной проверки.
Это нарушает принцип **Fail-Safe First** и дух **Single Source of Truth**.

**Текущая проблема:**
- Если `MetaDecisionBrain` падает → HARD_BLOCK условия не проверяются → сигнал проходит
- Если `PositionSizer` падает → размер позиции не рассчитывается → сигнал проходит с исходным размером

---

## Decision

Перевести `MetaDecisionBrain` и `PositionSizer` в **fail-closed** (CRITICAL):

1. **Import**: прямой импорт без `try/except`. ImportError → `gatekeeper.py` не загружается → `SystemGuardian` переводит систему в `SAFE_HALT`.

2. **Init**: прямая инициализация без `try/except`. Exception → `Gatekeeper.__init__` падает → `SAFE_HALT`.

3. **Runtime exception**: возвращать блокирующий результат вместо `None`:
   - `MetaDecisionBrain` exception → `MetaDecisionResult(allow_trading=False, block_level=HARD)`
   - `PositionSizer` exception → `PositionSizingResult(position_allowed=False, ...)`

Убрать флаги `META_DECISION_AVAILABLE` и `POSITION_SIZER_AVAILABLE`.

---

## Implementation

Файл: `execution/gatekeeper.py`

**Изменения:**
1. Удалить `try/except ImportError` блоки для обоих модулей → прямые импорты
2. Удалить флаги `META_DECISION_AVAILABLE`, `POSITION_SIZER_AVAILABLE`
3. В `__init__`: убрать `if META_DECISION_AVAILABLE` / `if POSITION_SIZER_AVAILABLE` guards → прямая инициализация
4. В `send_signal`: убрать `if self.meta_decision_brain and ...` guard → всегда вызывать
5. В `_check_meta_decision`:
   - Удалить guard `if not self.meta_decision_brain or not META_DECISION_AVAILABLE: return None`
   - Exception → вернуть `MetaDecisionResult(allow_trading=False, block_level=HARD, ...)`
6. В `_calculate_position_size`:
   - Удалить guard `if not self.position_sizer or not POSITION_SIZER_AVAILABLE: return None`
   - Exception → вернуть `PositionSizingResult(position_allowed=False, ...)`

---

## Consequences

**Positive:**
- INV-5 выполнен: оба модуля fail-closed
- Pipeline полный: каждый сигнал проходит все обязательные проверки
- Безопасность: неисправный модуль блокирует торговлю, а не пропускает её

**Negative:**
- Если `MetaDecisionBrain` или `PositionSizer` содержат баги → торговля останавливается
  (Приемлемо: лучше остановить, чем торговать без проверки)
- Требует стабильности обоих модулей (покрытие тестами — prerequisite)

**Neutral:**
- `DecisionTrace` остаётся NON_CRITICAL (fail-open): он не влияет на решение о торговле,
  только на логирование. ImportError/exception → graceful degradation без изменения ADR-003.
