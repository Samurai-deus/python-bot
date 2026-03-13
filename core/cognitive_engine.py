"""
Cognitive Engine - когнитивный слой системы.

Измеряет мета-оценки мышления системы:
- Confidence: насколько система уверена в сигнале
- Entropy: насколько рынок структурирован или хаотичен

Архитектурный принцип:
Confidence ≠ вероятность
Entropy ≠ волатильность

Это мета-оценки мышления системы, а не market indicators.
"""
from typing import Dict, Optional
from core.signal_snapshot import SignalSnapshot, SignalDecision, RiskLevel, VolatilityLevel
from core.market_state import MarketState


def calculate_confidence(snapshot: SignalSnapshot) -> float:
    """
    Вычисляет степень уверенности системы в сигнале.
    
    Показывает степень согласованности всех подсистем.
    
    Args:
        snapshot: SignalSnapshot для анализа
    
    Returns:
        float: Confidence ∈ [0.0, 1.0]
        - 0.0 = полная неуверенность
        - 1.0 = полная уверенность
    
    Факторы (взвешенно):
    1. Согласованность MarketState по таймфреймам (30%)
    2. Отношение score / score_max (25%)
    3. Совпадение decision и risk_level (20%)
    4. Отсутствие конфликтов (15%)
    5. Режим рынка и волатильность (10%)
    """
    confidence = 0.0
    
    # ========== 1. СОГЛАСОВАННОСТЬ MARKETSTATE ПО ТАЙМФРЕЙМАМ (30%) ==========
    state_consistency = _calculate_state_consistency(snapshot.states)
    confidence += state_consistency * 0.30
    
    # ========== 2. ОТНОШЕНИЕ SCORE / SCORE_MAX (25%) ==========
    score_ratio = snapshot.score_pct / 100.0  # Уже в [0, 1]
    confidence += score_ratio * 0.25
    
    # ========== 3. СОВПАДЕНИЕ DECISION И RISK_LEVEL (20%) ==========
    decision_risk_alignment = _calculate_decision_risk_alignment(snapshot)
    confidence += decision_risk_alignment * 0.20
    
    # ========== 4. ОТСУТСТВИЕ КОНФЛИКТОВ (15%) ==========
    conflict_penalty = _calculate_conflict_penalty(snapshot)
    confidence += conflict_penalty * 0.15
    
    # ========== 5. РЕЖИМ РЫНКА И ВОЛАТИЛЬНОСТЬ (10%) ==========
    regime_volatility_boost = _calculate_regime_volatility_boost(snapshot)
    confidence += regime_volatility_boost * 0.10
    
    # Нормализуем в [0, 1]
    return max(0.0, min(1.0, confidence))


def calculate_entropy(snapshot: SignalSnapshot) -> float:
    """
    Вычисляет когнитивную неопределённость рынка.
    
    Измеряет, насколько рынок структурирован или хаотичен.
    
    Args:
        snapshot: SignalSnapshot для анализа
    
    Returns:
        float: Entropy ∈ [0.0, 1.0]
        - 0.0 = полностью структурирован
        - 1.0 = хаотичен
    
    Факторы:
    1. Разброс MarketState по таймфреймам (40%)
    2. Конфликт score vs decision (30%)
    3. Высокая волатильность (20%)
    4. Неопределённость режима (10%)
    """
    entropy = 0.0
    
    # ========== 1. РАЗБРОС MARKETSTATE ПО ТАЙМФРЕЙМАМ (40%) ==========
    state_dispersion = _calculate_state_dispersion(snapshot.states)
    entropy += state_dispersion * 0.40
    
    # ========== 2. КОНФЛИКТ SCORE VS DECISION (30%) ==========
    score_decision_conflict = _calculate_score_decision_conflict(snapshot)
    entropy += score_decision_conflict * 0.30
    
    # ========== 3. ВЫСОКАЯ ВОЛАТИЛЬНОСТЬ (20%) ==========
    volatility_entropy = _calculate_volatility_entropy(snapshot)
    entropy += volatility_entropy * 0.20
    
    # ========== 4. НЕОПРЕДЕЛЁННОСТЬ РЕЖИМА (10%) ==========
    regime_uncertainty = _calculate_regime_uncertainty(snapshot)
    entropy += regime_uncertainty * 0.10
    
    # Нормализуем в [0, 1]
    return max(0.0, min(1.0, entropy))


# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def _calculate_state_consistency(states: Dict[str, Optional[MarketState]]) -> float:
    """
    Вычисляет согласованность MarketState по таймфреймам.
    
    Returns:
        float: [0.0, 1.0]
        - 1.0 = все состояния одинаковые
        - 0.0 = все состояния разные или None
    """
    # Фильтруем None
    valid_states = [state for state in states.values() if state is not None]
    
    if len(valid_states) == 0:
        return 0.0  # Нет данных
    
    if len(valid_states) == 1:
        return 1.0  # Одно состояние = полная согласованность
    
    # Подсчитываем уникальные состояния
    unique_states = set(valid_states)
    
    if len(unique_states) == 1:
        return 1.0  # Все одинаковые
    
    # Чем больше уникальных состояний, тем меньше согласованность
    # Максимум 4 разных состояния (A, B, C, D)
    consistency = 1.0 - (len(unique_states) - 1) / 3.0
    
    return max(0.0, consistency)


def _calculate_decision_risk_alignment(snapshot: SignalSnapshot) -> float:
    """
    Вычисляет совпадение decision и risk_level.
    
    Returns:
        float: [0.0, 1.0]
        - 1.0 = идеальное совпадение (ENTER + LOW risk)
        - 0.0 = конфликт (ENTER + HIGH risk)
    """
    # Идеальные комбинации
    if snapshot.decision == SignalDecision.ENTER and snapshot.risk_level == RiskLevel.LOW:
        return 1.0
    if snapshot.decision == SignalDecision.ENTER and snapshot.risk_level == RiskLevel.MEDIUM:
        return 0.7
    if snapshot.decision == SignalDecision.ENTER and snapshot.risk_level == RiskLevel.HIGH:
        return 0.2  # Конфликт
    
    # OBSERVE - средняя уверенность
    if snapshot.decision == SignalDecision.OBSERVE:
        if snapshot.risk_level == RiskLevel.LOW:
            return 0.6
        if snapshot.risk_level == RiskLevel.MEDIUM:
            return 0.5
        if snapshot.risk_level == RiskLevel.HIGH:
            return 0.3
    
    # SKIP / BLOCK - низкая уверенность
    if snapshot.decision in [SignalDecision.SKIP, SignalDecision.BLOCK]:
        return 0.3
    
    return 0.5  # По умолчанию


def _calculate_conflict_penalty(snapshot: SignalSnapshot) -> float:
    """
    Вычисляет штраф за конфликты в сигнале.
    
    Returns:
        float: [0.0, 1.0]
        - 1.0 = нет конфликтов
        - 0.0 = много конфликтов
    """
    penalty = 1.0
    
    # Конфликт 1: Высокий score + высокий risk
    if snapshot.score_pct >= 70 and snapshot.risk_level == RiskLevel.HIGH:
        penalty -= 0.4  # Сильный конфликт
    
    # Конфликт 2: Высокий score + SKIP/BLOCK
    if snapshot.score_pct >= 70 and snapshot.decision in [SignalDecision.SKIP, SignalDecision.BLOCK]:
        penalty -= 0.3
    
    # Конфликт 3: Низкий score + ENTER
    if snapshot.score_pct < 40 and snapshot.decision == SignalDecision.ENTER:
        penalty -= 0.3
    
    # Конфликт 4: Нет entry zone + ENTER
    if not snapshot.has_entry_zone and snapshot.decision == SignalDecision.ENTER:
        penalty -= 0.2
    
    return max(0.0, penalty)


def _calculate_regime_volatility_boost(snapshot: SignalSnapshot) -> float:
    """
    Вычисляет boost от режима рынка и волатильности.
    
    Returns:
        float: [0.0, 1.0]
    """
    boost = 0.5  # Базовое значение
    
    # Волатильность
    if snapshot.volatility_level == VolatilityLevel.NORMAL:
        boost += 0.3  # Оптимальная волатильность
    elif snapshot.volatility_level == VolatilityLevel.LOW:
        boost += 0.1  # Низкая волатильность
    elif snapshot.volatility_level == VolatilityLevel.HIGH:
        boost -= 0.1  # Высокая волатильность
    elif snapshot.volatility_level == VolatilityLevel.EXTREME:
        boost -= 0.3  # Экстремальная волатильность
    
    # Режим рынка (если доступен)
    if snapshot.market_regime:
        # TREND + RISK_ON = выше уверенность
        if snapshot.market_regime.trend_type == "TREND" and snapshot.market_regime.risk_sentiment == "RISK_ON":
            boost += 0.2
        # RANGE + RISK_OFF = ниже уверенность
        elif snapshot.market_regime.trend_type == "RANGE" and snapshot.market_regime.risk_sentiment == "RISK_OFF":
            boost -= 0.2
    
    return max(0.0, min(1.0, boost))


def _calculate_state_dispersion(states: Dict[str, Optional[MarketState]]) -> float:
    """
    Вычисляет разброс MarketState по таймфреймам.
    
    Returns:
        float: [0.0, 1.0]
        - 0.0 = все состояния одинаковые
        - 1.0 = максимальный разброс
    """
    # Фильтруем None
    valid_states = [state for state in states.values() if state is not None]
    
    if len(valid_states) == 0:
        return 1.0  # Нет данных = максимальная неопределённость
    
    if len(valid_states) == 1:
        return 0.0  # Одно состояние = нет разброса
    
    # Подсчитываем уникальные состояния
    unique_states = set(valid_states)
    
    if len(unique_states) == 1:
        return 0.0  # Все одинаковые
    
    # Чем больше уникальных состояний, тем больше разброс
    # Максимум 4 разных состояния (A, B, C, D)
    dispersion = (len(unique_states) - 1) / 3.0
    
    return min(1.0, dispersion)


def _calculate_score_decision_conflict(snapshot: SignalSnapshot) -> float:
    """
    Вычисляет конфликт между score и decision.
    
    Returns:
        float: [0.0, 1.0]
        - 0.0 = нет конфликта (высокий score + ENTER)
        - 1.0 = сильный конфликт (высокий score + BLOCK)
    """
    # Идеальное совпадение
    if snapshot.score_pct >= 70 and snapshot.decision == SignalDecision.ENTER:
        return 0.0  # Нет конфликта
    
    # Сильный конфликт
    if snapshot.score_pct >= 70 and snapshot.decision == SignalDecision.BLOCK:
        return 1.0  # Максимальный конфликт
    
    if snapshot.score_pct < 40 and snapshot.decision == SignalDecision.ENTER:
        return 0.8  # Высокий конфликт
    
    # Средний конфликт
    if snapshot.score_pct >= 50 and snapshot.decision == SignalDecision.SKIP:
        return 0.5
    
    # Низкий конфликт
    if snapshot.score_pct < 50 and snapshot.decision in [SignalDecision.SKIP, SignalDecision.BLOCK]:
        return 0.2
    
    return 0.3  # По умолчанию


def _calculate_volatility_entropy(snapshot: SignalSnapshot) -> float:
    """
    Вычисляет вклад волатильности в энтропию.
    
    Returns:
        float: [0.0, 1.0]
        - 0.0 = низкая волатильность (структурирован)
        - 1.0 = экстремальная волатильность (хаотичен)
    """
    if snapshot.volatility_level == VolatilityLevel.EXTREME:
        return 1.0
    elif snapshot.volatility_level == VolatilityLevel.HIGH:
        return 0.7
    elif snapshot.volatility_level == VolatilityLevel.NORMAL:
        return 0.3
    elif snapshot.volatility_level == VolatilityLevel.LOW:
        return 0.1
    else:
        return 0.5  # UNKNOWN


def _calculate_regime_uncertainty(snapshot: SignalSnapshot) -> float:
    """
    Вычисляет неопределённость режима рынка.
    
    Returns:
        float: [0.0, 1.0]
        - 0.0 = чёткий режим
        - 1.0 = неопределённый режим
    """
    if not snapshot.market_regime:
        return 0.5  # Нет данных = средняя неопределённость
    
    # Низкая уверенность в режиме = высокая неопределённость
    confidence = snapshot.market_regime.confidence
    
    # Инвертируем: низкая confidence = высокая uncertainty
    uncertainty = 1.0 - confidence
    
    return max(0.0, min(1.0, uncertainty))

