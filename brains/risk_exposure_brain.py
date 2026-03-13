"""
Risk & Exposure Brain - управление риском и экспозицией

Самый важный модуль. Он знает:
- суммарный риск
- корреляции между позициями
- плечи
- перегрузки
"""
from typing import Dict, List, Optional
from core.decision_core import RiskExposure
from capital import get_current_balance
from trade_manager import get_open_trades
from correlation_analysis import analyze_market_correlations
from config import INITIAL_BALANCE
import logging

logger = logging.getLogger(__name__)


class RiskExposureBrain:
    """
    Отслеживает риск и экспозицию портфеля.
    
    НЕ даёт сигналов, только анализирует риск.
    """
    
    def __init__(self):
        self.max_total_risk_pct = 10.0  # Максимальный суммарный риск
        self.max_exposure_pct = 50.0  # Максимальная экспозиция
        self.max_correlation = 0.8  # Максимальная корреляция
        # Состояние теперь хранится в SystemState, не здесь
    
    def reset(self):
        """
        Сбрасывает состояние brain (теперь не нужно - состояние в SystemState).
        Оставлено для обратной совместимости.
        """
        pass
    
    def analyze(self, symbols: List[str], 
               candles_map: Dict[str, Dict[str, List]], 
               system_state=None) -> RiskExposure:
        """
        Анализирует текущий риск и экспозицию.
        
        Args:
            symbols: Список символов
            candles_map: Словарь свечей для расчета корреляций
            
        Returns:
            RiskExposure: Текущее состояние риска
        """
        # 1. Получаем открытые позиции
        open_trades = get_open_trades()
        
        # 2. Рассчитываем суммарный риск
        total_risk_pct = self._calculate_total_risk(open_trades)
        
        # 3. Рассчитываем корреляции
        max_correlation = self._calculate_max_correlation(
            open_trades, symbols, candles_map
        )
        
        # 4. Рассчитываем суммарное плечо
        total_leverage = self._calculate_total_leverage(open_trades)
        
        # 5. Рассчитываем экспозицию
        exposure_pct = self._calculate_exposure(open_trades)
        
        # 6. Проверяем перегрузку
        is_overloaded = self._check_overload(
            total_risk_pct, exposure_pct, max_correlation, len(open_trades)
        )
        
        risk_exposure = RiskExposure(
            total_risk_pct=total_risk_pct,
            max_correlation=max_correlation,
            total_leverage=total_leverage,
            active_positions=len(open_trades),
            exposure_pct=exposure_pct,
            is_overloaded=is_overloaded
        )
        
        # Обновляем состояние в SystemState (если передан)
        if system_state is not None:
            system_state.update_risk_state(risk_exposure)
        
        return risk_exposure
    
    def _calculate_total_risk(self, open_trades: List[Dict]) -> float:
        """
        Рассчитывает суммарный риск в процентах от баланса.
        
        Риск = (entry - stop) / entry * position_size / balance * 100
        """
        if not open_trades:
            return 0.0
        
        balance = get_current_balance()
        if balance <= 0:
            balance = INITIAL_BALANCE
        
        total_risk_usd = 0.0
        
        for trade in open_trades:
            entry = float(trade.get("entry", 0))
            stop = float(trade.get("stop", 0))
            position_size_usd = float(trade.get("position_size", 0))
            side = trade.get("side", "LONG")
            
            if entry == 0 or position_size_usd == 0:
                continue
            
            # Рассчитываем риск на позицию
            if side == "LONG":
                risk_per_unit = entry - stop
            else:  # SHORT
                risk_per_unit = stop - entry
            
            if risk_per_unit <= 0:
                continue
            
            risk_pct_per_unit = (risk_per_unit / entry) * 100
            risk_usd = (risk_pct_per_unit / 100) * position_size_usd
            total_risk_usd += risk_usd
        
        # Конвертируем в проценты от баланса
        total_risk_pct = (total_risk_usd / balance) * 100 if balance > 0 else 0
        
        return total_risk_pct
    
    def _calculate_max_correlation(self, open_trades: List[Dict],
                                  symbols: List[str],
                                  candles_map: Dict[str, Dict[str, List]]) -> float:
        """
        Рассчитывает максимальную корреляцию между открытыми позициями.
        """
        if len(open_trades) < 2:
            logger.debug("Risk Exposure: менее 2 открытых позиций, корреляция = 0")
            return 0.0
        
        # Получаем символы открытых позиций
        open_symbols = [trade.get("symbol") for trade in open_trades if trade.get("symbol")]
        
        if len(open_symbols) < 2:
            logger.debug("Risk Exposure: менее 2 символов в открытых позициях, корреляция = 0")
            return 0.0
        
        logger.debug(f"Risk Exposure: анализ корреляций для {len(open_symbols)} открытых позиций: {open_symbols}")
        
        # Анализируем корреляции
        try:
            correlations = analyze_market_correlations(symbols, candles_map, timeframe="15m")
            logger.debug(f"Risk Exposure: получено корреляций для {len(correlations)} символов")
        except Exception as e:
            logger.error(f"Ошибка анализа корреляций в Risk Exposure Brain: {type(e).__name__}: {e}", exc_info=True)
            return 0.0
        
        max_corr = 0.0
        found_correlations = []
        
        # Проверяем корреляции между открытыми позициями
        for i, sym1 in enumerate(open_symbols):
            for sym2 in open_symbols[i+1:]:
                pair_corr = 0.0
                
                if sym1 in correlations:
                    # Проверяем strong_correlations (список кортежей [(symbol, corr), ...])
                    strong_corrs = correlations[sym1].get("strong_correlations", [])
                    for corr_symbol, corr_value in strong_corrs:
                        if corr_symbol == sym2:
                            pair_corr = abs(corr_value)
                            max_corr = max(max_corr, pair_corr)
                            found_correlations.append((sym1, sym2, corr_value, "strong"))
                            logger.debug(f"Risk Exposure: сильная корреляция {sym1}-{sym2}: {corr_value:.3f}")
                            break
                    
                    # Также проверяем weak_correlations
                    if pair_corr == 0:
                        weak_corrs = correlations[sym1].get("weak_correlations", [])
                        for corr_symbol, corr_value in weak_corrs:
                            if corr_symbol == sym2:
                                pair_corr = abs(corr_value)
                                max_corr = max(max_corr, pair_corr)
                                found_correlations.append((sym1, sym2, corr_value, "weak"))
                                logger.debug(f"Risk Exposure: слабая корреляция {sym1}-{sym2}: {corr_value:.3f}")
                                break
                
                # Проверяем обратную корреляцию (sym2 -> sym1)
                if pair_corr == 0 and sym2 in correlations:
                    strong_corrs = correlations[sym2].get("strong_correlations", [])
                    for corr_symbol, corr_value in strong_corrs:
                        if corr_symbol == sym1:
                            pair_corr = abs(corr_value)
                            max_corr = max(max_corr, pair_corr)
                            found_correlations.append((sym2, sym1, corr_value, "strong"))
                            logger.debug(f"Risk Exposure: сильная корреляция {sym2}-{sym1}: {corr_value:.3f}")
                            break
        
        if found_correlations:
            logger.info(f"Risk Exposure: найдено {len(found_correlations)} корреляций между открытыми позициями, максимум: {max_corr:.3f}")
        else:
            logger.debug(f"Risk Exposure: корреляций между открытыми позициями не найдено")
        
        return max_corr
    
    def _calculate_total_leverage(self, open_trades: List[Dict]) -> float:
        """
        Рассчитывает суммарное взвешенное плечо.
        """
        if not open_trades:
            return 0.0
        
        total_leverage_weighted = 0.0
        total_size = 0.0
        
        for trade in open_trades:
            leverage = float(trade.get("leverage", 1.0))
            position_size = float(trade.get("position_size", 0))
            
            total_leverage_weighted += leverage * position_size
            total_size += position_size
        
        if total_size == 0:
            return 0.0
        
        return total_leverage_weighted / total_size
    
    def _calculate_exposure(self, open_trades: List[Dict]) -> float:
        """
        Рассчитывает экспозицию в процентах от баланса.
        """
        if not open_trades:
            return 0.0
        
        balance = get_current_balance()
        if balance <= 0:
            balance = INITIAL_BALANCE
        
        total_exposure = sum(
            float(trade.get("position_size", 0)) 
            for trade in open_trades
        )
        
        exposure_pct = (total_exposure / balance) * 100 if balance > 0 else 0
        
        return exposure_pct
    
    def _check_overload(self, total_risk_pct: float, exposure_pct: float,
                       max_correlation: float, active_positions: int) -> bool:
        """
        Проверяет, не перегружен ли портфель.
        """
        # Перегрузка по риску
        if total_risk_pct > self.max_total_risk_pct:
            return True
        
        # Перегрузка по экспозиции
        if exposure_pct > self.max_exposure_pct:
            return True
        
        # Перегрузка по корреляции
        if max_correlation > self.max_correlation:
            return True
        
        # Перегрузка по количеству позиций (более 10)
        if active_positions > 10:
            return True
        
        return False


# Глобальный экземпляр
_risk_exposure_brain = None

def get_risk_exposure_brain() -> RiskExposureBrain:
    """Получить глобальный экземпляр Risk & Exposure Brain"""
    global _risk_exposure_brain
    if _risk_exposure_brain is None:
        _risk_exposure_brain = RiskExposureBrain()
    return _risk_exposure_brain

