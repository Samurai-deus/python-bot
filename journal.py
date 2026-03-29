import csv
import os
from datetime import datetime, UTC, timedelta
from typing import List, Dict, Optional
from core.market_state import MarketState, state_to_string
from core.signal_snapshot import SignalSnapshot, SignalDecision


def _parse_price(s: str) -> Optional[float]:
    """Парсит цену из строки CSV. Возвращает None если не число."""
    if not s or s in ("NO_ENTRY", "NO_EXIT", ""):
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _parse_rr(s: str) -> Optional[float]:
    """Парсит R-ratio из строки вида 'R=2.50'. Возвращает None если не распарсилось."""
    if not s:
        return None
    try:
        if s.startswith("R="):
            return float(s[2:])
        return float(s)
    except (ValueError, TypeError):
        return None


def _compute_sl_from_row(row: list) -> Optional[float]:
    """
    Возвращает SL из колонки 13 (новый формат) или вычисляет из entry/tp/rr (старый формат).
    """
    # Новый формат: col 13
    if len(row) > 13:
        sl = _parse_price(row[13])
        if sl is not None:
            return sl

    # Старый формат: вычисляем из entry, tp, rr
    entry = _parse_price(row[7]) if len(row) > 7 else None
    tp = _parse_price(row[8]) if len(row) > 8 else None
    rr = _parse_rr(row[9]) if len(row) > 9 else None
    direction = row[12] if len(row) > 12 else ""

    if entry and tp and rr and rr > 0:
        if direction == "LONG" and tp > entry:
            return entry - (tp - entry) / rr
        if direction == "SHORT" and tp < entry:
            return entry + (entry - tp) / rr
    return None


# ========== FAULT INJECTION (для тестирования устойчивости) ==========

FAULT_INJECT_STORAGE_FAILURE = os.environ.get("FAULT_INJECT_STORAGE_FAILURE", "false").lower() == "true"

def log_signal(symbol, states, risk):
    """
    УСТАРЕВШАЯ функция - используйте log_signal_snapshot().
    
    Оставлена для обратной совместимости.
    """
    log_signal_snapshot_from_legacy(symbol, states, risk)


def log_signal_snapshot(snapshot: SignalSnapshot):
    """
    Логирует SignalSnapshot в БД (PG-режим) и CSV (fallback).

    Это IO-операция: преобразует domain-объект в строки для записи.

    Args:
        snapshot: SignalSnapshot для логирования

    Note:
        Fault injection проверяется в SignalSnapshotStore.save() - entry point.
        Эта функция вызывается только после проверки fault injection.
    """
    from database import _PG_MODE, log_signal_to_db

    if _PG_MODE:
        try:
            log_signal_to_db(snapshot)
        except Exception as _db_err:
            import logging as _log
            _log.getLogger(__name__).warning("DB journal write failed: %s", _db_err)

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
                "r",
                "decision",
                "confidence",
                "direction",
                "sl",
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

        # Decision, confidence, direction
        decision_str = snapshot.decision.value if snapshot.decision else ""
        confidence_str = f"{snapshot.confidence:.4f}" if snapshot.confidence is not None else ""
        if snapshot.side:
            direction_str = snapshot.side
        elif snapshot.entry and snapshot.tp:
            direction_str = "LONG" if snapshot.tp > snapshot.entry else "SHORT"
        else:
            direction_str = ""

        sl_str = f"{snapshot.sl:.4f}" if snapshot.sl else ""

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
            rr_str,
            decision_str,
            confidence_str,
            direction_str,
            sl_str,
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
    Получает недавние сигналы из БД (PG-режим) или CSV.

    Args:
        since: Временная метка начала периода (опционально)

    Returns:
        list: Список сигналов
    """
    from database import _PG_MODE, get_signals_from_db

    if _PG_MODE:
        since_iso = since.isoformat() if since else "1970-01-01T00:00:00"
        rows = get_signals_from_db(since_iso, limit=200)
        result = []
        for r in rows:
            try:
                ts_str = r.get("timestamp", "")
                if "Z" in ts_str:
                    ts_str = ts_str.replace("Z", "+00:00")
                signal_time = datetime.fromisoformat(ts_str)
                if signal_time.tzinfo is None:
                    signal_time = signal_time.replace(tzinfo=UTC)
                result.append({
                    "timestamp": signal_time,
                    "symbol": r.get("symbol", ""),
                    "states": {
                        "1h": r.get("state_1h"),
                        "30m": r.get("state_30m"),
                        "15m": r.get("state_15m"),
                        "5m": r.get("state_5m"),
                    },
                    "risk": r.get("risk"),
                    "decision": r.get("decision", ""),
                    "confidence": r.get("confidence"),
                    "direction": r.get("direction", ""),
                    "entry": r.get("entry"),
                    "tp": r.get("tp"),
                    "sl": r.get("sl"),
                })
            except (ValueError, TypeError):
                continue
        return result

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
                        "risk": row[6] if len(row) > 6 else None,
                        "decision": row[10] if len(row) > 10 else "",
                        "confidence": float(row[11]) if len(row) > 11 and row[11] else None,
                        "direction": row[12] if len(row) > 12 else "",
                        "entry": _parse_price(row[7]) if len(row) > 7 else None,
                        "tp": _parse_price(row[8]) if len(row) > 8 else None,
                        "sl": _compute_sl_from_row(row),
                    })
                except (ValueError, IndexError):
                    continue
    except FileNotFoundError:
        pass
    
    return signals
