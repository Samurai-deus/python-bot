"""
Предохранитель: можно ли сейчас открывать позиции.

Блокер B-3 из аудита 10.09.2026. Команда /pause с подтверждением кодом отвечала
«Trading paused», ставила флаг — и ничего не останавливала: флаг trading_paused
не читали ни гейткипер, ни генератор сигналов, ни исполнитель ордеров (проверено
grep-ом по всему репозиторию: его читали только отчёты и API). Следующий цикл
анализа находил сигнал, и в режиме LIVE ордер уходил на биржу. Исполнитель вообще
не проверял ничего, кроме размера и режима «сухой/боевой».

Здесь один предикат, и его спрашивают две точки:

  • gatekeeper.send_signal — первым делом, до любой обработки сигнала. Раз сигнал
    не прошёл, не откроется и бумажная сделка: signal_generator открывает её
    только после send_signal() == True. Для бумажной торговли на проде это и есть
    единственный рубеж.
  • OrderExecutor.execute — перед реальным ордером, вторым рубежом: на случай
    вызова исполнителя в обход гейткипера.

Закрытие позиций предохранитель НЕ блокирует и блокировать не должен: остановка
торговли означает «не наращивать риск», а закрытие риск снижает.

Любая ошибка внутри проверки — тоже остановка: если не можем удостовериться,
что торговать можно, считаем, что нельзя.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Состояния Risk Core, при которых новые позиции запрещены. LIMITED — разрешает
# с урезанным размером, это решение самого Risk Core, а не предохранителя.
_BLOCKING_RISK_STATES = frozenset({"LOCKED", "HALTED"})


def trading_halt_reason(system_state=None, state_machine=None, risk_core=None,
                        include_risk_core: bool = True) -> Optional[str]:
    """
    Причина, по которой открывать позиции сейчас нельзя, или None, если можно.

    Аргументы — для тестов; по умолчанию берутся глобальные экземпляры.

    include_risk_core=False — для проверки ДО оценки сигнала: состояние Risk Core
    отражает последнюю оценку, а она могла касаться другого символа. Отказ по
    размеру позиции для BTC не должен блокировать следующий сигнал по ETH — свежий
    сигнал Risk Core оценит сам. Исполнитель вызывается после оценки того же
    сигнала и проверяет Risk Core тоже.
    """
    try:
        from system_state_machine import SystemState as MachineState

        if state_machine is None:
            from system_state_machine import get_state_machine
            state_machine = get_state_machine()

        state = state_machine.state
        if state != MachineState.RUNNING:
            return f"состояние системы {getattr(state, 'value', state)}, торговля разрешена только в RUNNING"
        if state_machine.trading_paused:
            return "торговля приостановлена машиной состояний (SAFE_MODE или FATAL)"

        if system_state is None:
            from system_state import get_system_state
            system_state = get_system_state()
        if system_state is None:
            return "состояние системы недоступно — проверить паузу нечем"
        if system_state.system_health.trading_paused:
            return "торговля приостановлена вручную (/pause) или системой"

        if include_risk_core:
            if risk_core is None:
                from core.risk_core import get_risk_core
                risk_core = get_risk_core()
            risk_state = getattr(risk_core.risk_state, "value", risk_core.risk_state)
            if str(risk_state) in _BLOCKING_RISK_STATES:
                return f"Risk Core в состоянии {risk_state}"
    except Exception as exc:
        logger.error("Предохранитель: проверка не удалась, открытие позиций запрещено", exc_info=True)
        return f"проверка предохранителя не удалась ({type(exc).__name__})"

    return None
