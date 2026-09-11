"""
Второе мнение ИИ по сигналу: контекст, запрос и разбор ответа.

В контекст идут только числа и собственные данные системы — никаких внешних
текстов, через которые в модель можно было бы внедрить чужие указания.
Каждая часть контекста собирается отдельно: сбой одной (нет свечей, нет фандинга)
не лишает модель остальных.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DECISIONS = ("approve", "reduce", "reject")


@dataclass
class Opinion:
    decision: str
    size_multiplier: float
    confidence: float
    reasons: List[str] = field(default_factory=list)
    key_risk: str = ""


def parse_opinion(text: Optional[str]) -> Optional[Opinion]:
    """Мнение из ответа модели — или None, если ответ не по схеме."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    decision = str(data.get("decision", "")).strip().lower()
    if decision not in DECISIONS:
        return None
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError):
        return None
    if not 0.0 <= confidence <= 1.0:
        return None
    if decision == "approve":
        multiplier = 1.0
    elif decision == "reject":
        multiplier = 0.0
    else:
        try:
            multiplier = float(data.get("size_multiplier"))
        except (TypeError, ValueError):
            return None
        if not 0.0 < multiplier < 1.0:
            return None
    raw_reasons = data.get("reasons") or []
    if not isinstance(raw_reasons, list):
        raw_reasons = [raw_reasons]
    reasons = [str(r).strip()[:300] for r in raw_reasons if str(r).strip()][:5]
    key_risk = str(data.get("key_risk") or "").strip()[:300]
    return Opinion(decision, round(multiplier, 3), round(confidence, 3), reasons, key_risk)


def _num(value, digits=6):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _enum_value(value):
    return getattr(value, "value", value)


def signal_context(symbol: str, signal_data: Dict, snapshot) -> Dict:
    entry = _num(signal_data.get("entry"))
    atr = _num(signal_data.get("atr"))
    ctx = {
        "symbol": symbol,
        "side": signal_data.get("side"),
        "entry": entry,
        "stop": _num(signal_data.get("stop")),
        "target": _num(signal_data.get("target")),
        "rr_ratio": _num(signal_data.get("rr_ratio"), 2),
        "atr_15m": atr,
        "atr_pct_of_price": _num(atr / entry * 100, 3) if atr and entry else None,
        "volatility_pct": _num(signal_data.get("volatility_pct"), 3),
        "leverage": _num(signal_data.get("leverage"), 2),
        "position_size_usd": _num(signal_data.get("position_size"), 2),
        "score": signal_data.get("score"),
        "market_mode": signal_data.get("mode"),
        "system_risk_level": signal_data.get("risk"),
    }
    if snapshot is not None:
        ctx["timeframe_states"] = {tf: _enum_value(s) for tf, s in (getattr(snapshot, "states", None) or {}).items()}
        ctx["timeframe_directions"] = dict(getattr(snapshot, "directions", None) or {})
        ctx["system_confidence"] = _num(getattr(snapshot, "confidence", None), 3)
        ctx["system_entropy"] = _num(getattr(snapshot, "entropy", None), 3)
        regime = getattr(snapshot, "market_regime", None)
        if regime is not None:
            ctx["market_regime"] = {k: _enum_value(getattr(regime, k, None))
                                    for k in ("trend_type", "volatility_level", "risk_sentiment", "confidence")}
    return ctx


def _candles_summary(symbol: str) -> Dict:
    from data_loader import get_candles
    out = {}
    for label, interval, count in (("4h", "240", 12), ("15m", "15", 16)):
        rows = get_candles(symbol, interval, count) or []
        # [close, high, low] — достаточно для структуры движения, компактно для токенов
        out[label] = [[_num(r[4]), _num(r[2]), _num(r[3])] for r in rows[-count:]]
    return out


def _crowd_positioning(symbol: str) -> Dict:
    from market_data.bybit_market_data import get_funding_rate, get_open_interest
    funding = [_num(f.get("fundingRate")) for f in (get_funding_rate(symbol, limit=3) or [])]
    oi = [float(x["openInterest"]) for x in (get_open_interest(symbol, interval="1h", limit=24) or [])
          if x.get("openInterest")]
    oi_change = _num((oi[0] - oi[-1]) / oi[-1] * 100, 2) if len(oi) >= 2 and oi[-1] else None
    return {"funding_rates_recent": funding, "open_interest_change_24h_pct": oi_change}


def portfolio_context(symbol: Optional[str] = None) -> Dict:
    import capital
    import database
    from market_data.correlation_groups import get_groups
    trades = database.get_open_trades()
    groups = get_groups()
    ctx = {
        "equity_usd": _num(capital.get_current_balance(), 2),
        "available_usd": _num(capital.get_available_capital(), 2),
        "open_positions": [
            {"symbol": t.get("symbol"), "side": t.get("side"), "notional_usd": _num(database.open_notional(t), 2),
             "entry": _num(t.get("entry")), "stop": _num(t.get("stop"))}
            for t in trades
        ],
    }
    if symbol:
        group = next(((name, members) for name, members in groups.items() if symbol in members), None)
        ctx["correlation_group"] = {"name": group[0], "members": group[1]} if group else None
    return ctx


def track_record(days: int = 30) -> Dict:
    import database
    return {"system_signal_outcomes_30d": database.get_signal_outcome_counts(days),
            "ai_shadow_opinions_30d": database.get_ai_opinion_stats(days)}


def _safe(part: str, build):
    try:
        return build()
    except Exception as exc:
        logger.debug("ai_trader: часть контекста «%s» не собрана: %s", part, exc)
        return None


def build_context(symbol: str, signal_data: Dict, snapshot) -> Dict:
    return {
        "signal": signal_context(symbol, signal_data, snapshot),
        "candles": _safe("свечи", lambda: _candles_summary(symbol)),
        "crowd_positioning": _safe("фандинг и открытый интерес", lambda: _crowd_positioning(symbol)),
        "portfolio": _safe("портфель", lambda: portfolio_context(symbol)),
        "track_record": _safe("статистика", track_record),
    }


def render(context: Dict) -> str:
    return ("Сигнал на оценку. Данные (JSON):\n"
            + json.dumps(context, ensure_ascii=False, separators=(",", ":"), default=str)
            + "\nОтветь строго JSON по схеме из инструкции.")
