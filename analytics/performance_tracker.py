"""
PerformanceTracker — агрегированная аналитика P&L.

Назначение:
    - Агрегирует P&L по символу, режиму рынка, времени суток
    - Строит equity curve
    - Возвращает полный отчёт с финансовыми метриками

Принципы:
    - Работает только с данными из БД (pnl_records)
    - Не взаимодействует с live-рынком
    - Не меняет состояние системы
    - Все методы могут быть вызваны офлайн
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Dict, List, Optional

import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SymbolReport:
    symbol: str
    trades: int = 0
    wins: int = 0
    losses: int = 0
    gross_pnl: float = 0.0
    commission: float = 0.0
    net_pnl: float = 0.0
    profit_factor: Optional[float] = None

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades > 0 else 0.0


@dataclass
class RegimeReport:
    regime: str
    trades: int = 0
    wins: int = 0
    net_pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades > 0 else 0.0


@dataclass
class TimeOfDayReport:
    period: str          # "morning", "afternoon", "evening", "night"
    trades: int = 0
    wins: int = 0
    net_pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        return (self.wins / self.trades * 100) if self.trades > 0 else 0.0


@dataclass
class FullPerformanceReport:
    """Полный P&L отчёт за период."""
    period_days: int
    total_trades: int
    total_net_pnl: float
    total_commission: float
    win_rate: float

    # Финансовые метрики
    sharpe_ratio: Optional[float]
    max_drawdown_abs: float
    max_drawdown_pct: float
    profit_factor: Optional[float]
    recovery_factor: Optional[float]
    expectancy: Optional[float]

    # Текущий streak
    current_streak: int  # > 0 выигрышный, < 0 убыточный

    # Breakdown
    by_symbol: Dict[str, SymbolReport] = field(default_factory=dict)
    by_regime: Dict[str, RegimeReport] = field(default_factory=dict)
    by_timeofday: Dict[str, TimeOfDayReport] = field(default_factory=dict)

    # Топ/Худшие символы
    top_symbols: List[str] = field(default_factory=list)      # топ-5 по net_pnl
    worst_symbols: List[str] = field(default_factory=list)    # худшие-5 по net_pnl

    # Equity curve
    equity_curve: List[float] = field(default_factory=list)

    generated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


# ---------------------------------------------------------------------------
# PerformanceTracker
# ---------------------------------------------------------------------------

class PerformanceTracker:
    """
    Агрегатор P&L аналитики.

    Все методы читают данные из pnl_records через database.py.
    Не имеет состояния между вызовами — методы идемпотентны.
    """

    def track_closed_trade(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
        gross_pnl: float,
        net_pnl: float,
        commission: float = 0.0,
        market_regime: Optional[str] = None,
        hold_duration_seconds: Optional[int] = None,
        signal_confidence: Optional[float] = None,
        signal_entropy: Optional[float] = None,
        balance_after: Optional[float] = None,
    ) -> Optional[int]:
        """
        Записывает закрытую сделку в pnl_records.

        Returns:
            ID записи или None при ошибке.
        """
        from database import insert_pnl_record
        return insert_pnl_record(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            exit_price=exit_price,
            quantity=quantity,
            gross_pnl=gross_pnl,
            net_pnl=net_pnl,
            commission=commission,
            market_regime=market_regime,
            hold_duration_seconds=hold_duration_seconds,
            signal_confidence=signal_confidence,
            signal_entropy=signal_entropy,
            balance_after=balance_after,
        )

    def get_pnl_by_symbol(self, days: int = 30, trades: Optional[list] = None) -> Dict[str, SymbolReport]:
        """P&L breakdown по символу. Accepts pre-fetched trades to avoid duplicate queries."""
        if trades is None:
            from database import get_closed_trades
            trades = get_closed_trades(days)
        from bot_statistics import calculate_profit_factor

        result: Dict[str, SymbolReport] = {}
        trades_by_sym: Dict[str, list] = {}

        for t in trades:
            sym = t['symbol']
            if sym not in result:
                result[sym] = SymbolReport(symbol=sym)
                trades_by_sym[sym] = []
            trades_by_sym[sym].append(t)
            r = result[sym]
            r.trades += 1
            r.net_pnl += t.get('net_pnl', 0.0)
            r.gross_pnl += t.get('gross_pnl', 0.0)
            r.commission += t.get('commission', 0.0)
            if t.get('net_pnl', 0) > 0:
                r.wins += 1
            elif t.get('net_pnl', 0) < 0:
                r.losses += 1

        for sym, r in result.items():
            r.profit_factor = calculate_profit_factor(trades_by_sym[sym])

        return result

    def get_pnl_by_regime(self, days: int = 30, trades: Optional[list] = None) -> Dict[str, RegimeReport]:
        """P&L breakdown по режиму рынка (A/B/C/D). Accepts pre-fetched trades."""
        if trades is None:
            from database import get_closed_trades
            trades = get_closed_trades(days)
        result: Dict[str, RegimeReport] = {}

        for t in trades:
            regime = t.get('market_regime') or 'unknown'
            if regime not in result:
                result[regime] = RegimeReport(regime=regime)
            r = result[regime]
            r.trades += 1
            r.net_pnl += t.get('net_pnl', 0.0)
            if t.get('net_pnl', 0) > 0:
                r.wins += 1

        return result

    def get_pnl_by_timeofday(self, days: int = 30, trades: Optional[list] = None) -> Dict[str, TimeOfDayReport]:
        """
        P&L breakdown по времени суток (UTC). Accepts pre-fetched trades.

        Периоды:
            morning   — 06:00–11:59
            afternoon — 12:00–17:59
            evening   — 18:00–23:59
            night     — 00:00–05:59
        """
        if trades is None:
            from database import get_closed_trades
            trades = get_closed_trades(days)
        result: Dict[str, TimeOfDayReport] = {}

        for t in trades:
            try:
                closed_at = t.get('closed_at', '')
                hour = datetime.fromisoformat(closed_at).hour
            except Exception:
                hour = 0

            if 6 <= hour < 12:
                period = 'morning'
            elif 12 <= hour < 18:
                period = 'afternoon'
            elif 18 <= hour < 24:
                period = 'evening'
            else:
                period = 'night'

            if period not in result:
                result[period] = TimeOfDayReport(period=period)
            r = result[period]
            r.trades += 1
            r.net_pnl += t.get('net_pnl', 0.0)
            if t.get('net_pnl', 0) > 0:
                r.wins += 1

        return result

    def get_equity_curve(self, days: int = 30) -> List[float]:
        """Возвращает equity curve (список балансов) за период."""
        from database import get_equity_curve_points
        points = get_equity_curve_points(days)
        return [p['balance'] for p in points]

    def _calculate_streak(self, trades: list) -> int:
        """
        Текущий streak: положительное число = серия побед,
        отрицательное = серия поражений.
        """
        if not trades:
            return 0
        streak = 0
        is_win = trades[-1].get('net_pnl', 0) > 0
        for t in reversed(trades):
            trade_win = t.get('net_pnl', 0) > 0
            if trade_win == is_win:
                streak += 1 if is_win else -1
            else:
                break
        return streak

    def get_full_report(self, days: int = 30) -> Optional[FullPerformanceReport]:
        """
        Полный P&L отчёт за период.

        Returns:
            FullPerformanceReport или None при отсутствии данных.
        """
        from database import get_closed_trades
        from bot_statistics import (
            calculate_sharpe_ratio,
            calculate_max_drawdown,
            calculate_profit_factor,
            calculate_recovery_factor,
            calculate_expectancy,
        )

        trades = get_closed_trades(days)
        if not trades:
            return None

        equity_curve = self.get_equity_curve(days)
        by_symbol = self.get_pnl_by_symbol(days, trades=trades)
        by_regime = self.get_pnl_by_regime(days, trades=trades)
        by_timeofday = self.get_pnl_by_timeofday(days, trades=trades)

        total_trades = len(trades)
        wins = sum(1 for t in trades if t.get('net_pnl', 0) > 0)
        total_net_pnl = sum(t.get('net_pnl', 0.0) for t in trades)
        total_commission = sum(t.get('commission', 0.0) for t in trades)
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

        sharpe = calculate_sharpe_ratio(equity_curve) if len(equity_curve) >= 2 else None
        dd_abs, dd_pct = calculate_max_drawdown(equity_curve) if equity_curve else (0.0, 0.0)
        pf = calculate_profit_factor(trades)
        rf = calculate_recovery_factor(trades, equity_curve) if equity_curve else None
        exp = calculate_expectancy(trades)
        streak = self._calculate_streak(trades)

        # Топ/Худшие символы по net_pnl
        sorted_syms = sorted(by_symbol.items(), key=lambda x: x[1].net_pnl, reverse=True)
        top_symbols = [s for s, _ in sorted_syms[:5]]
        worst_symbols = [s for s, _ in sorted_syms[-5:]]

        return FullPerformanceReport(
            period_days=days,
            total_trades=total_trades,
            total_net_pnl=round(total_net_pnl, 4),
            total_commission=round(total_commission, 4),
            win_rate=round(win_rate, 2),
            sharpe_ratio=sharpe,
            max_drawdown_abs=dd_abs,
            max_drawdown_pct=round(dd_pct * 100, 2),
            profit_factor=pf,
            recovery_factor=rf,
            expectancy=exp,
            current_streak=streak,
            by_symbol=by_symbol,
            by_regime=by_regime,
            by_timeofday=by_timeofday,
            top_symbols=top_symbols,
            worst_symbols=worst_symbols,
            equity_curve=equity_curve,
        )
