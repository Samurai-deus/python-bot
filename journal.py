import csv
import os
from datetime import datetime, UTC, timedelta
from typing import List, Dict, Optional
from core.market_state import MarketState, state_to_string
from core.signal_snapshot import SignalSnapshot, SignalDecision

def log_signal(symbol, states, risk):
    """
    УСТАРЕВШАЯ функция - используйте log_signal_snapshot().
    
    Оставлена для обратной совместимости.
    """
    log_signal_snapshot_from_legacy(symbol, states, risk)


def log_signal_snapshot(snapshot: SignalSnapshot):
    """
    Логирует SignalSnapshot в CSV.
    
    Это IO-операция: преобразует domain-объект в строки для записи.
    
    Args:
        snapshot: SignalSnapshot для логирования
    """
    file_exists = os.path.exists("signals_log.csv")
    with open("signals_log.csv", "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        
        # Если файл новый, записываем заголовки
        if not file_exists:
            writer.writerow([
                "timestamp",
                "symbol",
                "state_1h",
                "state_30m",
                "state_15m",
                "state_5m",
                "risk",
                "entry",
                "exit",
                "r"
            ])
        
        # Преобразуем domain-объект в строки (IO-граница)
        timestamp = snapshot.timestamp.isoformat()
        state_1h = state_to_string(snapshot.states.get("1h"))
        state_30m = state_to_string(snapshot.states.get("30m"))
        state_15m = state_to_string(snapshot.states.get("15m"))
        state_5m = state_to_string(snapshot.states.get("5m"))
        risk_str = snapshot.risk_level.value if snapshot.risk_level else ""
        
        # Entry/Exit из snapshot
        entry_str = f"{snapshot.entry:.4f}" if snapshot.entry else "NO_ENTRY"
        exit_str = f"{snapshot.tp:.4f}" if snapshot.tp else "NO_EXIT"
        
        # R-ratio из snapshot
        rr_str = f"R={snapshot.rr_ratio:.2f}" if snapshot.rr_ratio else "R=0"
        
        writer.writerow([
            timestamp,
            snapshot.symbol,
            state_1h,
            state_30m,
            state_15m,
            state_5m,
            risk_str,
            entry_str,
            exit_str,
            rr_str
        ])


def log_signal_snapshot_from_legacy(symbol: str, states: Dict[str, Optional[MarketState]], risk: str):
    """
    Создаёт минимальный SignalSnapshot из legacy параметров и логирует его.
    
    Используется для обратной совместимости со старым кодом.
    
    Args:
        symbol: Торговая пара
        states: Словарь состояний
        risk: Уровень риска (строка)
    """
    from core.signal_snapshot import risk_string_to_enum
    from core.market_state import normalize_states_dict
    
    normalized_states = normalize_states_dict(states)
    risk_enum = risk_string_to_enum(risk)
    
    snapshot = SignalSnapshot(
        timestamp=datetime.now(UTC),
        symbol=symbol,
        timeframe_anchor="15m",
        states=normalized_states,
        risk_level=risk_enum,
        decision=SignalDecision.SKIP,  # Неизвестно из legacy данных
        decision_reason="Legacy signal",
        confidence=0.0,  # Не вычисляется для legacy сигналов
        entropy=0.0       # Не вычисляется для legacy сигналов
    )
    
    # Логируем через основной метод
    log_signal_snapshot(snapshot)

def get_recent_signals(since: Optional[datetime] = None) -> List[Dict]:
    """
    Получает недавние сигналы.
    
    Args:
        since: Временная метка начала периода (опционально)
        
    Returns:
        list: Список сигналов
    """
    signals = []
    
    try:
        with open("signals_log.csv", "r", newline="") as f:
            reader = csv.reader(f)
            for row in reader:
                if len(row) < 2:
                    continue
                
                try:
                    # Парсим время и нормализуем к UTC (offset-aware)
                    time_str = str(row[0]).strip()
                    
                    # Обрабатываем разные форматы
                    if 'Z' in time_str:
                        time_str = time_str.replace('Z', '+00:00')
                    
                    # Пробуем парсить ISO формат
                    try:
                        signal_time = datetime.fromisoformat(time_str)
                    except ValueError:
                        # Если не получилось, пробуем другие форматы
                        # Может быть старый формат без timezone
                        try:
                            signal_time = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S.%f')
                        except ValueError:
                            try:
                                signal_time = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                            except ValueError:
                                # Пропускаем строку, если не удалось распарсить
                                continue
                    
                    # Если datetime без timezone, добавляем UTC
                    if signal_time.tzinfo is None:
                        signal_time = signal_time.replace(tzinfo=UTC)
                    # Нормализуем к UTC для сравнения
                    signal_time = signal_time.astimezone(UTC)
                    
                    # Нормализуем since к UTC для сравнения
                    if since:
                        if since.tzinfo is None:
                            since_normalized = since.replace(tzinfo=UTC)
                        else:
                            since_normalized = since.astimezone(UTC)
                        if signal_time < since_normalized:
                            continue
                    
                    signals.append({
                        "timestamp": signal_time,
                        "symbol": row[1] if len(row) > 1 else "",
                        "states": {
                            "1h": row[2] if len(row) > 2 else None,
                            "30m": row[3] if len(row) > 3 else None,
                            "15m": row[4] if len(row) > 4 else None,
                            "5m": row[5] if len(row) > 5 else None,
                        },
                        "risk": row[6] if len(row) > 6 else None
                    })
                except (ValueError, IndexError):
                    continue
    except FileNotFoundError:
        pass
    
    return signals
