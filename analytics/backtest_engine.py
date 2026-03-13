"""
BacktestEngine — полноценный бэктест поверх исторических данных.

Отличия от ReplayEngine:
    - Работает с историческими OHLCV свечами, не только со snapshot'ами
    - Учитывает slippage и комиссии Bybit (maker/taker)
    - Симулирует исполнение SL/TP по реальным ценам свечи
    - Строит equity curve

Принципы:
    - НЕ торгует в реальном времени
    - НЕ меняет SystemState
    - НЕ пишет в production-логи
    - Только pure logic, никаких side effects
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Комиссии Bybit (актуальны на 2026)
# ---------------------------------------------------------------------------

BYBIT_MAKER_FEE = 0.0002   # 0.02%
BYBIT_TAKER_FEE = 0.00055  # 0.055%


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    """
    Конфигурация бэктеста.

    Attrs:
        initial_balance:   Начальный баланс в USDT.
        risk_pct:          Риск на сделку (доля от баланса, например 0.02 = 2%).
        slippage_pct:      Проскальзывание при входе и выходе (доля цены).
        maker_fee:         Комиссия maker (доля).
        taker_fee:         Комиссия taker (доля). Используется по умолчанию.
        use_taker_fee:     Если True — использовать taker fee для всех сделок.
    """
    initial_balance: float = 10_000.0
    risk_pct: float = 0.02
    slippage_pct: float = 0.0003   # 0.03% — типичное для ликвидных пар
    maker_fee: float = BYBIT_MAKER_FEE
    taker_fee: float = BYBIT_TAKER_FEE
    use_taker_fee: bool = True     # Market ордера — всегда taker


@dataclass
class OHLCVCandle:
    """Одна OHLCV свеча."""
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class BacktestTrade:
    """
    Результат одной симулированной сделки.

    Все цены уже включают slippage. P&L — net (после комиссий).
    """
    symbol: str
    side: str                    # "LONG" | "SHORT"
    entry_candle_ts: str
    exit_candle_ts: str
    entry_price: float           # с учётом slippage
    exit_price: float            # с учётом slippage
    stop_loss: float
    take_profit: Optional[float]
    quantity: float              # в базовой валюте
    position_size_usdt: float    # entry_price * quantity
    gross_pnl: float
    commission: float
    net_pnl: float
    close_reason: str            # "TP" | "SL" | "END_OF_DATA"
    balance_after: float
    signal_confidence: Optional[float] = None
    signal_entropy: Optional[float] = None


@dataclass
class BacktestResult:
    """
    Итог бэктеста.

    Attrs:
        config:         Параметры бэктеста.
        trades:         Список всех симулированных сделок.
        equity_curve:   Баланс после каждой закрытой сделки.
        total_trades:   Количество сделок.
        wins:           Прибыльных сделок.
        losses:         Убыточных сделок.
        total_net_pnl:  Суммарный net P&L.
        total_commission: Суммарная комиссия.
        win_rate:       Win rate (0–100).
        final_balance:  Итоговый баланс.
    """
    config: BacktestConfig
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[float] = field(default_factory=list)

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.net_pnl > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.net_pnl < 0)

    @property
    def total_net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)

    @property
    def total_commission(self) -> float:
        return sum(t.commission for t in self.trades)

    @property
    def win_rate(self) -> float:
        return (self.wins / self.total_trades * 100) if self.total_trades > 0 else 0.0

    @property
    def final_balance(self) -> float:
        return self.equity_curve[-1] if self.equity_curve else self.config.initial_balance

    def summary_dict(self) -> dict:
        """Возвращает краткую сводку в виде dict."""
        return {
            'total_trades': self.total_trades,
            'wins': self.wins,
            'losses': self.losses,
            'win_rate': round(self.win_rate, 2),
            'total_net_pnl': round(self.total_net_pnl, 4),
            'total_commission': round(self.total_commission, 4),
            'initial_balance': self.config.initial_balance,
            'final_balance': round(self.final_balance, 4),
            'return_pct': round(
                (self.final_balance - self.config.initial_balance)
                / self.config.initial_balance * 100, 4
            ) if self.config.initial_balance > 0 else 0.0,
        }


# ---------------------------------------------------------------------------
# BacktestEngine
# ---------------------------------------------------------------------------

class BacktestEngine:
    """
    Симулирует торговлю на исторических данных.

    Использование:
        config = BacktestConfig(initial_balance=10_000, risk_pct=0.02)
        engine = BacktestEngine(config)
        result = engine.run(signals, ohlcv)

    Args:
        config: Конфигурация бэктеста. По умолчанию — стандартные параметры.
    """

    def __init__(self, config: Optional[BacktestConfig] = None):
        self.config = config or BacktestConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        signals: List[dict],
        ohlcv: Dict[str, List[OHLCVCandle]],
    ) -> BacktestResult:
        """
        Запускает бэктест.

        Args:
            signals: Список сигналов. Каждый сигнал — dict с ключами:
                        symbol, side, entry, stop, target (опц.),
                        timestamp, confidence (опц.), entropy (опц.).
            ohlcv:   Исторические свечи: {symbol: [OHLCVCandle, ...]}.
                     Свечи должны быть отсортированы по timestamp ASC.

        Returns:
            BacktestResult с полным отчётом.
        """
        result = BacktestResult(config=self.config)
        balance = self.config.initial_balance

        for signal in signals:
            symbol = signal.get('symbol', '')
            candles = ohlcv.get(symbol, [])
            if not candles:
                continue

            trade = self._simulate_trade(signal, candles, balance)
            if trade is None:
                continue

            balance = trade.balance_after
            result.trades.append(trade)
            result.equity_curve.append(balance)

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _simulate_trade(
        self,
        signal: dict,
        candles: List[OHLCVCandle],
        balance: float,
    ) -> Optional[BacktestTrade]:
        """
        Симулирует одну сделку на заданных свечах.

        Вход: первая свеча после timestamp сигнала (market order — taker).
        Выход: первая свеча, где достигнут SL или TP. Если ни один из них
               не достигнут — позиция закрывается по close последней свечи.
        """
        signal_ts = signal.get('timestamp', '')
        side = signal.get('side', 'LONG').upper()
        sl = signal.get('stop')
        tp = signal.get('target')
        raw_entry = signal.get('entry')

        if not sl or not raw_entry:
            return None

        # Найти первую свечу после timestamp сигнала
        entry_candle = self._find_entry_candle(candles, signal_ts)
        if entry_candle is None:
            return None

        # Применить slippage к цене входа
        entry_price = self._apply_slippage(raw_entry, side, entry=True)

        # Рассчитать размер позиции (по риску на сделку)
        quantity, position_size_usdt = self._calculate_position(
            balance, entry_price, sl, side
        )
        if quantity <= 0:
            return None

        # Найти свечу выхода (SL/TP hit)
        exit_candle, close_reason = self._find_exit_candle(
            candles, entry_candle, side, sl, tp
        )

        # Определить цену выхода
        raw_exit = self._determine_exit_price(
            exit_candle, side, sl, tp, close_reason
        )
        exit_price = self._apply_slippage(raw_exit, side, entry=False)

        # Расчёт P&L
        gross_pnl = self._calculate_gross_pnl(
            side, entry_price, exit_price, quantity
        )
        commission = self._calculate_commission(position_size_usdt, quantity, exit_price)
        net_pnl = gross_pnl - commission
        balance_after = balance + net_pnl

        return BacktestTrade(
            symbol=signal.get('symbol', ''),
            side=side,
            entry_candle_ts=entry_candle.timestamp,
            exit_candle_ts=exit_candle.timestamp,
            entry_price=round(entry_price, 8),
            exit_price=round(exit_price, 8),
            stop_loss=sl,
            take_profit=tp,
            quantity=round(quantity, 8),
            position_size_usdt=round(position_size_usdt, 4),
            gross_pnl=round(gross_pnl, 4),
            commission=round(commission, 4),
            net_pnl=round(net_pnl, 4),
            close_reason=close_reason,
            balance_after=round(balance_after, 4),
            signal_confidence=signal.get('confidence'),
            signal_entropy=signal.get('entropy'),
        )

    def _find_entry_candle(
        self,
        candles: List[OHLCVCandle],
        signal_ts: str,
    ) -> Optional[OHLCVCandle]:
        """Первая свеча с timestamp >= signal_ts."""
        for candle in candles:
            if candle.timestamp >= signal_ts:
                return candle
        return None

    def _find_exit_candle(
        self,
        candles: List[OHLCVCandle],
        entry_candle: OHLCVCandle,
        side: str,
        sl: float,
        tp: Optional[float],
    ) -> Tuple[OHLCVCandle, str]:
        """
        Ищет свечу, на которой был задет SL или TP.

        Returns:
            (exit_candle, close_reason)
        """
        entry_idx = next(
            (i for i, c in enumerate(candles) if c.timestamp == entry_candle.timestamp),
            0
        )
        future_candles = candles[entry_idx + 1:]

        for candle in future_candles:
            if side == 'LONG':
                if candle.low <= sl:
                    return candle, 'SL'
                if tp is not None and candle.high >= tp:
                    return candle, 'TP'
            else:  # SHORT
                if candle.high >= sl:
                    return candle, 'SL'
                if tp is not None and candle.low <= tp:
                    return candle, 'TP'

        # Нет ни SL, ни TP — закрываем по последней доступной свече
        last_candle = future_candles[-1] if future_candles else entry_candle
        return last_candle, 'END_OF_DATA'

    def _determine_exit_price(
        self,
        candle: OHLCVCandle,
        side: str,
        sl: float,
        tp: Optional[float],
        close_reason: str,
    ) -> float:
        """Определяет конкретную цену выхода на свече."""
        if close_reason == 'SL':
            return sl
        if close_reason == 'TP' and tp is not None:
            return tp
        # END_OF_DATA — выходим по close свечи
        return candle.close

    def _apply_slippage(self, price: float, side: str, entry: bool) -> float:
        """
        Применяет slippage к цене.

        При входе в LONG или выходе из SHORT — цена растёт.
        При выходе из LONG или входе в SHORT — цена падает.
        """
        slip = self.config.slippage_pct
        if (side == 'LONG' and entry) or (side == 'SHORT' and not entry):
            return price * (1 + slip)
        else:
            return price * (1 - slip)

    def _calculate_position(
        self,
        balance: float,
        entry_price: float,
        sl: float,
        side: str,
    ) -> Tuple[float, float]:
        """
        Рассчитывает размер позиции по риску на сделку.

        risk_amount = balance * risk_pct
        stop_distance = |entry - sl| / entry
        quantity = risk_amount / (entry * stop_distance)
        """
        risk_amount = balance * self.config.risk_pct
        if entry_price <= 0:
            return 0.0, 0.0

        if side == 'LONG':
            stop_distance = entry_price - sl
        else:
            stop_distance = sl - entry_price

        if stop_distance <= 0:
            return 0.0, 0.0

        quantity = risk_amount / stop_distance
        position_size_usdt = quantity * entry_price
        return quantity, position_size_usdt

    def _calculate_gross_pnl(
        self,
        side: str,
        entry_price: float,
        exit_price: float,
        quantity: float,
    ) -> float:
        """P&L до вычета комиссий."""
        if side == 'LONG':
            return (exit_price - entry_price) * quantity
        else:
            return (entry_price - exit_price) * quantity

    def _calculate_commission(
        self,
        position_size_usdt: float,
        quantity: float,
        exit_price: float,
    ) -> float:
        """
        Комиссия за открытие (taker) + закрытие (taker) позиции.
        commission = position_size * fee * 2  (вход + выход)
        """
        fee = self.config.taker_fee if self.config.use_taker_fee else self.config.maker_fee
        exit_size_usdt = quantity * exit_price
        return (position_size_usdt + exit_size_usdt) * fee
