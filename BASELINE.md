# BASELINE v1.0

## Runtime guarantees
- No event loop blocking
- Drift-safe scheduler
- Soft watchdog (no crashes)
- Graceful shutdown < 1s
- Stable Telegram polling

## Core parameters
ANALYSIS_INTERVAL = <current value>
MAX_ANALYSIS_TIME = <current value>
ALERT_ANALYSIS_TIME = <current value>
LOG_LEVEL = INFO

## Validation checklist
- No LOOP_GUARD_TIMEOUT
- No SIGKILL on stop
- Heartbeat stable
- Metrics logged on shutdown
