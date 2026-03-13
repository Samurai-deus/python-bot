# ADR-005: Адаптивное управление риском при обнаружении Drift

**Статус:** ACCEPTED  
**Дата:** 2026-03-13  
**Автор:** Antigravity AI  

---

## Контекст

После реализации DriftDetector (Фаза 1) система умеет обнаруживать деградацию
поведения (confidence/entropy drift, decoupling). Однако никакой обратной связи
на параметры риска нет — бот продолжает торговать с базовым risk_pct даже при
критическом drift.

## Решение

Создаётся `analytics/adaptive_risk_manager.py` — модуль, который:
1. Принимает `DriftState` от `DriftDetector`
2. Возвращает `AdaptiveRiskDecision` (новый risk_pct, мультипликатор позиции, причина)
3. Снижает риск **постепенно** — один шаг за итерацию цикла
4. При отсутствии drift ≥ 24ч — восстанавливает базовый риск (один шаг вверх)
5. Только при `HIGH drift` И уже достигнутом floor — блокирует торговлю

## Параметры (утверждены)

| Параметр | Значение |
|----------|----------|
| `BASE_RISK_PCT` | Из `config.py` (по умолчанию 2%) |
| `FLOOR_RISK_PCT` | `1.5%` |
| `STEP_DOWN` | `0.25%` за итерацию |
| `STEP_UP` | `0.25%` за итерацию (при восстановлении) |
| Период восстановления | 24 часа без drift |
| Блокировка торговли | HIGH drift + risk уже на floor |

## Логика

```
NO_DRIFT:
    → если последний drift_detected > 24ч назад:
        → new_risk = min(base, current + STEP_UP)
    → иначе: без изменений

LOW drift:
    → new_risk = max(floor, current - STEP_UP * 0.5)  # медленнее
    → Telegram WARNING

MEDIUM drift:
    → new_risk = max(floor, current - STEP_DOWN)
    → Telegram ALERT

HIGH drift:
    → если current > floor:
        → new_risk = max(floor, current - STEP_DOWN)
        → Telegram ALERT
    → если current == floor:
        → BLOCK_TRADING = True
        → Telegram CRITICAL ALERT
```

## Инварианты

- `new_risk_pct` всегда в диапазоне `[FLOOR_RISK_PCT, base_risk_pct]`
- Шаг снижения не превышает `STEP_DOWN` за одну итерацию
- `BLOCK_TRADING` устанавливается ТОЛЬКО при HIGH drift и уже достигнутом floor
- AdaptiveRiskManager НЕ меняет SystemState напрямую — только возвращает решение
- Интеграция в `runner.py` отвечает за применение решения

## Последствия

- При боевом HIGH drift бот сначала снижает риск (несколько итераций),
  и только потом блокирует торговлю — защита от ложных срабатываний
- Пользователь видит Telegram уведомления на каждом шаге
- Все изменения risk_pct логируются

## Связанные ADR

- ADR-004: fail-closed модули (PositionSizer, MetaDecisionBrain)
