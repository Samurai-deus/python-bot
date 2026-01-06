# RSO Output Schemas

## JSON Schema

### Root Object

```json
{
  "rso_version": "string (required)",
  "timestamp": "ISO 8601 datetime (required)",
  "observation_type": "read_only (required)",
  "fsm_state": "FSMState | null (required)",
  "system_state": "SystemState | null (required)",
  "recent_logs": "LogEntry[] (required)",
  "fsm_transitions": "Transition[] (required)",
  "metadata": "Metadata (required)"
}
```

### FSMState

```json
{
  "current_state": "string | null",
  "duration_in_state": "number | null",
  "consecutive_errors": "number | null",
  "recovery_cycles": "number | null",
  "safe_mode_entered_at": "ISO 8601 datetime | null",
  "last_heartbeat": "ISO 8601 datetime | null",
  "transitions_count": "number | null",
  "last_transition": "Transition | null",
  "all_transitions": "Transition[]"
}
```

### SystemState

```json
{
  "timestamp": "ISO 8601 datetime",
  "system_health": {
    "is_running": "boolean",
    "safe_mode": "boolean",
    "trading_paused": "boolean",
    "last_heartbeat": "ISO 8601 datetime | null",
    "consecutive_errors": "number"
  },
  "performance_metrics": {
    "total_cycles": "number",
    "successful_cycles": "number",
    "errors": "number",
    "last_error": "string | null"
  },
  "trading_decision": {
    "can_trade": "boolean",
    "last_decision_time": "ISO 8601 datetime | null"
  },
  "market_state": "object | null",
  "risk_state": "object | null",
  "cognitive_state": "object | null",
  "opportunities_count": "number",
  "open_positions_count": "number",
  "recent_signals_count": "number"
}
```

### Transition

```json
{
  "from_state": "string",
  "to_state": "string",
  "reason": "string",
  "timestamp": "ISO 8601 datetime",
  "incident_id": "string",
  "owner": "string",
  "metadata": "object"
}
```

### LogEntry

```json
{
  "raw": "string",
  "timestamp": "string | null",
  "level": "string | null",
  "message": "string | null"
}
```

### Metadata

```json
{
  "observer_mode": "external",
  "read_only": true,
  "no_verdict": true
}
```

## Markdown Schema

### Structure

```markdown
# Runtime Safety Observer (RSO) v1.0 Report

**Observation Time:** ISO 8601 datetime
**Observer Mode:** External (Read-Only)

## FSM State
- **Current State:** `state`
- **Duration in State:** `duration`
- **Consecutive Errors:** `number`
- **Recovery Cycles:** `number`
- **Safe Mode Entered At:** `datetime | null`
- **Last Transition:** `from` → `to`
  - Reason: `reason`
  - Incident ID: `incident_id`

## System State
### System Health
- **Is Running:** `boolean`
- **Safe Mode:** `boolean`
- **Trading Paused:** `boolean`
- **Consecutive Errors:** `number`

### Performance Metrics
- **Total Cycles:** `number`
- **Successful Cycles:** `number`
- **Errors:** `number`

### Trading Decision
- **Can Trade:** `boolean`

## FSM Transitions
| From | To | Reason | Timestamp | Incident ID | Owner |
|------|----|----|-----------|-------------|-------|
| ... | ... | ... | ... | ... | ... |

## Recent Logs
| Timestamp | Level | Message |
|-----------|-------|---------|
| ... | ... | ... |

---
**Note:** This is a read-only observation. No verdicts are computed.
**Observer:** External process, no control actions performed.
```

## Правила обработки данных

1. **Null для недоступных данных**: Если данные недоступны, записывается `null` (не интерпретируется)
2. **Только факты**: Записываются только факты, без интерпретации
3. **Строгое соответствие схемам**: Все выходные данные строго соответствуют схемам
4. **Без вердиктов**: Никаких вычислений PASS/FAIL или оценок

## Примеры

### Пример JSON (минимальный)

```json
{
  "rso_version": "1.0",
  "timestamp": "2024-01-01T12:00:00+00:00",
  "observation_type": "read_only",
  "fsm_state": null,
  "system_state": null,
  "recent_logs": [],
  "fsm_transitions": [],
  "metadata": {
    "observer_mode": "external",
    "read_only": true,
    "no_verdict": true
  }
}
```

### Пример JSON (полный)

См. `rso.py` для полного примера с заполненными данными.

