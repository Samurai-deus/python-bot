"""
Прогон портфеля по истории (Ф1 плана трейдера, шаг 3, фаза B).

На вход — сетапы фазы A (backtest.replay) и свечи 5m для исполнения. Порядок на каждом
закрытии 5m — как у живого бота:
1. выходы открытых позиций по бару, который только что закрылся (гэп через стоп — по
   open; касание стопа и цели в одном баре — стоп; иначе по уровню);
2. сетапы этого момента по символам в порядке SYMBOLS: размер (риск RISK_PERCENT
   капитала, пределы открытого риска, номинала и числа позиций — как capital.position_size),
   блок микроструктуры, предел новых позиций за оборот, новизна сигнала (как
   SystemState.is_new_signal — «съедается» до гейткипера), одна позиция на символ,
   пауза Risk Core после серии убытков в событиях;
3. вход — по open следующего бара.

Издержки: проскальзывание на входе и выходе, комиссия taker с обеих сторон, фандинг по
историческим ставкам в 00/08/16 UTC. Не моделируется в v1: риск группы коррелированных
символов (группы бот считает раз в сутки по данным), фильтры гейткипера (мета-мозг,
Decision Core, PortfolioBrain) — их вклад оценивается в Ф3.
"""
import bisect
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

TAKER_FEE = 0.00055
SLIPPAGE = 0.0005
TREND_SIGNAL_COOLDOWN_MS = 4 * 3_600_000
FIVE_MS = 300_000


@dataclass
class Limits:
    """Пределы — те же, что у Risk Core и capital.position_size (config.py)."""
    risk_pct: float = 1.0
    max_positions: int = 6
    max_open_risk_pct: float = 6.0
    max_single_position_pct: float = 100.0
    max_aggregate_pct: float = 300.0
    max_new_per_turn: int = 3
    headroom: float = 0.99
    loss_event_window_min: float = 15.0

    @classmethod
    def from_config(cls):
        import config
        from capital import RISK_LIMIT_HEADROOM
        return cls(risk_pct=config.RISK_PERCENT, max_positions=config.RISK_MAX_OPEN_POSITIONS,
                   max_open_risk_pct=config.RISK_MAX_OPEN_RISK_PCT,
                   max_single_position_pct=config.RISK_MAX_SINGLE_POSITION_PCT,
                   max_aggregate_pct=config.RISK_MAX_AGGREGATE_EXPOSURE_PCT,
                   max_new_per_turn=config.MAX_NEW_POSITIONS_PER_TURN, headroom=RISK_LIMIT_HEADROOM,
                   loss_event_window_min=config.RISK_LOSS_EVENT_WINDOW_MINUTES)


@dataclass
class Position:
    symbol: str
    strategy: str
    side: str
    signal_t: int
    entry_t: int
    entry: float
    stop: float
    target: float
    qty: float
    fees: float = 0.0
    funding: float = 0.0

    @property
    def notional(self) -> float:
        return self.qty * self.entry

    @property
    def risk_usd(self) -> float:
        return self.qty * abs(self.entry - self.stop)


@dataclass
class Trade:
    symbol: str
    strategy: str
    side: str
    signal_t: int
    entry_t: int
    exit_t: int
    entry: float
    exit: float
    stop: float
    target: float
    qty: float
    reason: str
    fees: float
    funding: float
    pnl: float
    r: float


@dataclass
class Result:
    trades: List[Trade] = field(default_factory=list)
    equity_curve: List[Tuple[int, float]] = field(default_factory=list)
    refused: Dict[str, int] = field(default_factory=dict)
    final_equity: float = 0.0


def _slip(price: float, side: str, entering: bool) -> float:
    worse_up = (side == "LONG") == entering  # LONG вход и SHORT выход — дороже
    return price * (1 + SLIPPAGE) if worse_up else price * (1 - SLIPPAGE)


def exit_in_bar(pos: Position, o: float, h: float, low: float) -> Optional[Tuple[float, str]]:
    """Выход позиции внутри бара (o, h, low) — консервативно: стоп раньше цели."""
    if pos.side == "LONG":
        if o <= pos.stop:
            return o, "SL_GAP"
        if o >= pos.target:
            return o, "TP_GAP"
        if low <= pos.stop:
            return pos.stop, "SL"
        if h >= pos.target:
            return pos.target, "TP"
    else:
        if o >= pos.stop:
            return o, "SL_GAP"
        if o <= pos.target:
            return o, "TP_GAP"
        if h >= pos.stop:
            return pos.stop, "SL"
        if low <= pos.target:
            return pos.target, "TP"
    return None


def _cooldown_minutes(events: int) -> float:
    if events < 3:
        return 0.0
    return 30.0 if events == 3 else 45.0 if events == 4 else 60.0


def simulate(setups: Iterable, bars_5m: Dict[str, list], funding: Dict[str, List[Tuple[int, float]]],
             symbols_order: List[str], equity: float = 1000.0, limits: Optional[Limits] = None) -> Result:
    """
    setups — события фазы A (t_ms, symbol, side, entry, stop, target, strategy, state_15m,
    block_long, block_short); bars_5m — {symbol: [[start, open, high, low, close, ...], ...]}
    по возрастанию; funding — {symbol: [(ts, rate), ...]} по возрастанию.
    """
    from core.risk_core import loss_streak
    limits = limits or Limits()
    result = Result()
    order = {s: i for i, s in enumerate(symbols_order)}
    by_t: Dict[int, list] = {}
    for s in setups:
        by_t.setdefault(s.t_ms, []).append(s)
    starts = {sym: [int(b[0]) for b in rows] for sym, rows in bars_5m.items()}
    fund_ts = {sym: [ts for ts, _ in rows] for sym, rows in funding.items()}

    open_pos: Dict[str, Position] = {}
    closes: List[Dict] = []  # для серии убытков: новые первыми
    last_state: Dict[str, Optional[str]] = {}
    last_trend_signal: Dict[str, int] = {}
    pending: List[Tuple[object, float]] = []  # (сетап, размер номинала) — вход на следующем баре

    if not by_t:
        result.final_equity = equity
        return result
    # итерация t обрабатывает сетапы момента t + 5m — начинаем на бар раньше первого сетапа
    t = min(by_t) - min(by_t) % FIVE_MS - FIVE_MS
    end =max(max(r[-1][0] for r in bars_5m.values() if r) + FIVE_MS, max(by_t) + FIVE_MS)

    def refuse(code):
        result.refused[code] = result.refused.get(code, 0) + 1

    def bar(symbol, start):
        i = bisect.bisect_left(starts.get(symbol, []), start)
        rows = bars_5m.get(symbol, [])
        return rows[i] if i < len(rows) and int(rows[i][0]) == start else None

    while t <= end:
        # 1) входы, назначенные на бар, который открывается в t
        for setup, notional in pending:
            b = bar(setup.symbol, t)
            if b is None or setup.symbol in open_pos:
                refuse("no_bar")
                continue
            fill = _slip(float(b[1]), setup.side, entering=True)
            qty = notional / setup.entry
            pos = Position(setup.symbol, setup.strategy, setup.side, setup.t_ms, t, fill, setup.stop, setup.target, qty)
            pos.fees += qty * fill * TAKER_FEE
            open_pos[setup.symbol] = pos
        pending = []

        # 2) бар [t, t+5m): выходы и фандинг (результат известен к t+5m)
        for symbol in list(open_pos):
            pos = open_pos[symbol]
            b = bar(symbol, t)
            if b is None:
                continue
            o, h, low = float(b[1]), float(b[2]), float(b[3])
            for i in range(bisect.bisect_left(fund_ts.get(symbol, []), t), len(fund_ts.get(symbol, []))):
                ts = fund_ts[symbol][i]
                if ts >= t + FIVE_MS:
                    break
                rate = funding[symbol][i][1]
                pos.funding += pos.qty * o * rate * (1 if pos.side == "LONG" else -1)
            hit = exit_in_bar(pos, o, h, low)
            if hit is None:
                continue
            price, reason = hit
            exit_fill = _slip(price, pos.side, entering=False)
            pos.fees += pos.qty * exit_fill * TAKER_FEE
            gross = pos.qty * (exit_fill - pos.entry) * (1 if pos.side == "LONG" else -1)
            pnl = gross - pos.fees - pos.funding
            risk = pos.risk_usd
            result.trades.append(Trade(symbol, pos.strategy, pos.side, pos.signal_t, pos.entry_t, t + FIVE_MS,
                                       pos.entry, exit_fill, pos.stop, pos.target, pos.qty, reason, pos.fees,
                                       pos.funding, pnl, pnl / risk if risk else 0.0))
            equity += pnl
            closes.insert(0, {"pnl": pnl, "updated_at": _iso(t + FIVE_MS)})
            del open_pos[symbol]
        now = t + FIVE_MS
        result.equity_curve.append((now, equity))

        # 3) сетапы на закрытии этого бара (момент now) — вход на следующем баре
        sent = 0
        for setup in sorted(by_t.get(now, []), key=lambda s: order.get(s.symbol, 99)):
            open_risk = sum(p.risk_usd for p in open_pos.values()) + sum(n * abs(s.entry - s.stop) / s.entry
                                                                         for s, n in pending)
            open_notional = sum(p.notional for p in open_pos.values()) + sum(n for _, n in pending)
            taken = len(open_pos) + len(pending)
            if taken >= limits.max_positions:
                refuse("no_room")
                continue
            risk_usd = min(equity * limits.risk_pct / 100,
                           equity * limits.max_open_risk_pct / 100 * limits.headroom - open_risk)
            dist = abs(setup.entry - setup.stop) / setup.entry
            if risk_usd <= 0 or dist <= 0:
                refuse("no_room")
                continue
            notional = min(risk_usd / dist, equity * limits.max_single_position_pct / 100,
                           equity * limits.max_aggregate_pct / 100 - open_notional)
            if notional <= 0:
                refuse("no_room")
                continue
            if (setup.side == "LONG" and setup.block_long) or (setup.side == "SHORT" and setup.block_short):
                refuse("funding")
                continue
            if sent >= limits.max_new_per_turn:
                refuse("turn_limit")
                continue
            # новизна — как SystemState.is_new_signal: «съедается» до гейткипера
            if setup.state_15m is None:
                if now - last_trend_signal.get(setup.symbol, -10 ** 18) < TREND_SIGNAL_COOLDOWN_MS:
                    refuse("not_new")
                    continue
                last_trend_signal[setup.symbol] = now
            else:
                if last_state.get(setup.symbol) == setup.state_15m:
                    refuse("not_new")
                    continue
                last_state[setup.symbol] = setup.state_15m
            if setup.symbol in open_pos or any(s.symbol == setup.symbol for s, _ in pending):
                refuse("position_open")
                continue
            events, last_loss = loss_streak(closes, limits.loss_event_window_min)
            cooldown = _cooldown_minutes(events)
            if cooldown and last_loss is not None and (now - int(last_loss.timestamp() * 1000)) < cooldown * 60_000:
                refuse("loss_cooldown")
                continue
            pending.append((setup, notional))
            sent += 1
        t += FIVE_MS

    result.final_equity = equity
    return result


def _iso(ms: int) -> str:
    from datetime import UTC, datetime
    return datetime.fromtimestamp(ms / 1000, UTC).isoformat()
