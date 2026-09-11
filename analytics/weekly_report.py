"""
Еженедельный отчёт по стратегиям (шаг 4 плана обучения, 11.09.2026).

Два среза за неделю:
1. Сделки текущего режима — по стратегиям: число, доля прибыльных, PnL и средний
   результат в R (PnL, делённый на риск при входе: номинал × |вход − исходный стоп| / вход).
   R сравнивает стратегии независимо от размера позиции.
2. Сигналы из журнала (шаг 3) — по судьбам и причинам, с исходами по свечам: сколько
   сигналов каждого фильтра дошло бы до цели и какое ожидание в R у группы
   (цель — +R:R сигнала, стоп — −1, без касаний — 0). Фильтр, отсекающий сигналы
   с положительным ожиданием, — кандидат на пересмотр.

Исход сигнала размечается с задержкой — до суток после сигнала, такие сигналы
показаны как «ждут разметки».
"""
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

REPORT_WEEKDAY = 0      # понедельник
REPORT_HOUR_UTC = 6     # 09:00 по Москве
# Флаг «отсекает прибыльные»: хватает разметки и ожидание заметно выше нуля.
FLAG_MIN_DECIDED = 10
FLAG_MIN_EXPECTANCY_R = 0.2

_FATE_LABELS = {"SENT": "взятые"}


def seconds_until_next_report(now: datetime) -> float:
    """Секунды до ближайшего понедельника 06:00 UTC строго после now."""
    days_ahead = (REPORT_WEEKDAY - now.weekday()) % 7
    moment = (now + timedelta(days=days_ahead)).replace(hour=REPORT_HOUR_UTC, minute=0, second=0, microsecond=0)
    if moment <= now:
        moment += timedelta(days=7)
    return (moment - now).total_seconds()


def trade_r(trade: Dict) -> Optional[float]:
    """Результат сделки в R: PnL / риск при входе. None — риск не определить."""
    entry = trade.get("entry") or 0.0
    stop = trade.get("original_stop") or trade.get("stop") or 0.0
    size = trade.get("position_size") or 0.0
    if entry <= 0 or stop <= 0 or size <= 0 or entry == stop:
        return None
    risk_usd = size * abs(entry - stop) / entry
    return (trade.get("pnl") or 0.0) / risk_usd


def signal_r(fate: Dict) -> Optional[float]:
    """Результат сигнала по свечам в R; None — исхода ещё нет."""
    outcome = fate.get("outcome")
    if outcome == "WIN":
        return fate.get("rr_ratio") or 0.0
    if outcome == "LOSS":
        return -1.0
    if outcome == "NEUTRAL":
        return 0.0
    return None


def _money(value: float) -> str:
    return f"{value:+.2f} $".replace(".", ",")


def _r(value: float) -> str:
    return f"{value:+.2f} R".replace(".", ",")


def trades_section(trades: Iterable[Dict], capital: Optional[float]) -> List[str]:
    by_strategy: Dict[str, List[Dict]] = defaultdict(list)
    for trade in trades:
        by_strategy[trade.get("strategy_name") or "без стратегии"].append(trade)
    if not by_strategy:
        return ["Сделок за неделю не было."]
    lines = ["Сделки по стратегиям:"]
    total_pnl = 0.0
    total = 0
    for name, group in sorted(by_strategy.items(), key=lambda item: -len(item[1])):
        pnl = sum(t.get("pnl") or 0.0 for t in group)
        wins = sum(1 for t in group if (t.get("pnl") or 0.0) > 0)
        rs = [r for r in (trade_r(t) for t in group) if r is not None]
        average_r = f", в среднем {_r(sum(rs) / len(rs))}" if rs else ""
        lines.append(f"• {name}: {len(group)} сд., прибыльных {100 * wins / len(group):.0f} %, "
                     f"PnL {_money(pnl)}{average_r}")
        total_pnl += pnl
        total += len(group)
    share = f" ({100 * total_pnl / capital:+.1f} % счёта)".replace(".", ",") if capital else ""
    lines.append(f"Итого: {total} сд., PnL {_money(total_pnl)}{share}")
    return lines


def _fate_line(label: str, group: List[Dict], flag: bool) -> str:
    outcomes = [f.get("outcome") for f in group]
    rs = [r for r in (signal_r(f) for f in group) if r is not None]
    text = (f"• {label}: {len(group)} — цель {outcomes.count('WIN')}, стоп {outcomes.count('LOSS')}, "
            f"без касаний {outcomes.count('NEUTRAL')}")
    if rs:
        expectancy = sum(rs) / len(rs)
        text += f", ожидание {_r(expectancy)}"
        if flag and len(rs) >= FLAG_MIN_DECIDED and expectancy >= FLAG_MIN_EXPECTANCY_R:
            text += " ⚠️ отсекает прибыльные"
    return text


def signals_section(fates: Iterable[Dict]) -> List[str]:
    fates = list(fates)
    if not fates:
        return ["Сигналов в журнале за неделю нет."]
    by_fate: Dict[str, List[Dict]] = defaultdict(list)
    by_strategy: Dict[str, List[Dict]] = defaultdict(list)
    for fate in fates:
        status = fate.get("status") or ""
        by_fate[_FATE_LABELS.get(status) or fate.get("reason_code") or status.lower()].append(fate)
        by_strategy[fate.get("strategy") or "без стратегии"].append(fate)
    counts = defaultdict(int)
    for fate in fates:
        counts[fate.get("status")] += 1
    pending = sum(1 for f in fates if f.get("outcome") is None)
    lines = [f"Сигналы: {len(fates)} — взято {counts['SENT']}, отказ гейткипера {counts['BLOCKED']}, "
             f"отсев генератора {counts['SKIPPED']}; ждут разметки {pending}.",
             "Исходы по свечам — по судьбе:"]
    for label, group in sorted(by_fate.items(), key=lambda item: (item[0] != "взятые", -len(item[1]))):
        lines.append(_fate_line(label, group, flag=label != "взятые"))
    lines.append("Качество сетапов по стратегиям (все судьбы):")
    for name, group in sorted(by_strategy.items(), key=lambda item: -len(item[1])):
        lines.append(_fate_line(name, group, flag=False))
    return lines


def build_weekly_report(now: Optional[datetime] = None) -> str:
    """Текст отчёта за 7 дней до now по сделкам текущего режима и журналу сигналов."""
    import database
    from capital import get_current_balance
    from trading_mode import sends_real_orders

    now = now or datetime.now(UTC)
    since = now - timedelta(days=7)
    on_exchange = sends_real_orders()
    trades = database.get_closed_trades_since(since.isoformat(), on_exchange=on_exchange)
    fates = database.get_signal_fates(since.isoformat())
    try:
        capital = get_current_balance()
    except Exception:
        capital = None
    header = (f"📈 Неделя {since:%d.%m}–{now:%d.%m} (UTC), "
              f"{'сделки на бирже' if on_exchange else 'бумажные сделки'}")
    return "\n".join([header, ""] + trades_section(trades, capital) + [""] + signals_section(fates))


def send_weekly_report() -> None:
    from telegram_bot import send_message
    send_message(build_weekly_report())
