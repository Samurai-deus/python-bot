"""
Accuracy Analyzer — анализирует точность предсказаний бота.

Читает таблицу signal_outcomes и вычисляет:
- Общий win rate (WIN / (WIN + LOSS))
- По символу: топ и аутсайдеры
- По confidence bucket: 0-0.3, 0.3-0.5, 0.5-0.7, 0.7-1.0
- По направлению: LONG vs SHORT
- По состоянию рынка 15m: A/B/C/D
- Calibration error: насколько confidence предсказывает реальный win rate

Не влияет на торговую логику. Только читает.
"""
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Dict, List, Optional
import logging

logger = logging.getLogger(__name__)

MIN_RESOLVED = 3  # Минимум WIN+LOSS для включения группы в статистику


@dataclass
class SymbolAccuracy:
    symbol: str
    total: int
    wins: int
    losses: int
    neutrals: int
    win_rate: float          # wins / (wins + losses), исключая NEUTRAL
    avg_favorable_pct: float
    avg_adverse_pct: float


@dataclass
class ConfidenceBucket:
    label: str               # "0.0-0.3"
    min_conf: float
    max_conf: float
    total: int               # включая NEUTRAL
    wins: int
    losses: int
    win_rate: float          # wins / (wins + losses)
    expected_rate: float     # середина bucket (что обещает confidence)
    calibration_error: float # abs(win_rate - expected_rate), 0 если мало данных


@dataclass
class AccuracyReport:
    generated_at: datetime
    period_days: int
    total_outcomes: int
    total_wins: int
    total_losses: int
    total_neutrals: int
    overall_win_rate: float
    long_win_rate: float
    long_total: int
    short_win_rate: float
    short_total: int
    by_symbol: List[SymbolAccuracy]
    by_confidence: List[ConfidenceBucket]
    by_state_15m: Dict[str, dict]
    mean_calibration_error: float
    top_symbols: List[SymbolAccuracy]    # топ-5 по win_rate (min 3 resolved)
    bottom_symbols: List[SymbolAccuracy] # худшие-5 (min 3 resolved)
    avg_max_favorable_pct: float
    avg_max_adverse_pct: float


def _win_rate(wins: int, losses: int) -> float:
    total = wins + losses
    return round(wins / total, 4) if total > 0 else 0.0


def analyze_accuracy(days: int = 30) -> Optional[AccuracyReport]:
    """
    Анализирует точность предсказаний за последние N дней.

    Args:
        days: Период анализа в днях

    Returns:
        AccuracyReport или None если данных меньше MIN_RESOLVED
    """
    from database import get_outcomes_for_analysis

    rows = get_outcomes_for_analysis(days=days)

    resolved = [r for r in rows if r["outcome"] in ("WIN", "LOSS")]
    if len(resolved) < MIN_RESOLVED:
        logger.info(
            "[AccuracyAnalyzer] Not enough resolved outcomes (%d) for analysis",
            len(resolved),
        )
        return None

    total_wins = sum(1 for r in rows if r["outcome"] == "WIN")
    total_losses = sum(1 for r in rows if r["outcome"] == "LOSS")
    total_neutrals = sum(1 for r in rows if r["outcome"] == "NEUTRAL")
    overall_wr = _win_rate(total_wins, total_losses)

    # --- By direction ---
    long_rows = [r for r in rows if r["direction"] == "LONG"]
    short_rows = [r for r in rows if r["direction"] == "SHORT"]
    long_wr = _win_rate(
        sum(1 for r in long_rows if r["outcome"] == "WIN"),
        sum(1 for r in long_rows if r["outcome"] == "LOSS"),
    )
    short_wr = _win_rate(
        sum(1 for r in short_rows if r["outcome"] == "WIN"),
        sum(1 for r in short_rows if r["outcome"] == "LOSS"),
    )

    # --- By symbol ---
    sym_map: Dict[str, dict] = {}
    for r in rows:
        sym = r["symbol"]
        if sym not in sym_map:
            sym_map[sym] = {"wins": 0, "losses": 0, "neutrals": 0, "fav": [], "adv": []}
        d = sym_map[sym]
        if r["outcome"] == "WIN":
            d["wins"] += 1
        elif r["outcome"] == "LOSS":
            d["losses"] += 1
        else:
            d["neutrals"] += 1
        if r.get("max_favorable_pct") is not None:
            d["fav"].append(r["max_favorable_pct"])
        if r.get("max_adverse_pct") is not None:
            d["adv"].append(r["max_adverse_pct"])

    symbol_list = []
    for sym, d in sym_map.items():
        wr = _win_rate(d["wins"], d["losses"])
        symbol_list.append(SymbolAccuracy(
            symbol=sym,
            total=d["wins"] + d["losses"] + d["neutrals"],
            wins=d["wins"],
            losses=d["losses"],
            neutrals=d["neutrals"],
            win_rate=wr,
            avg_favorable_pct=round(sum(d["fav"]) / len(d["fav"]), 2) if d["fav"] else 0.0,
            avg_adverse_pct=round(sum(d["adv"]) / len(d["adv"]), 2) if d["adv"] else 0.0,
        ))

    ranked = sorted(
        [s for s in symbol_list if s.wins + s.losses >= MIN_RESOLVED],
        key=lambda x: x.win_rate,
        reverse=True,
    )

    # --- By confidence bucket ---
    buckets_def = [
        ("0.0-0.3", 0.0, 0.3),
        ("0.3-0.5", 0.3, 0.5),
        ("0.5-0.7", 0.5, 0.7),
        ("0.7-1.0", 0.7, 1.01),  # 1.01 чтобы включить 1.0
    ]
    bucket_list = []
    for label, lo, hi in buckets_def:
        b_rows = [
            r for r in rows
            if r.get("confidence") is not None and lo <= r["confidence"] < hi
        ]
        b_wins = sum(1 for r in b_rows if r["outcome"] == "WIN")
        b_losses = sum(1 for r in b_rows if r["outcome"] == "LOSS")
        b_wr = _win_rate(b_wins, b_losses)
        expected = round((lo + min(hi, 1.0)) / 2, 2)
        cal_err = round(abs(b_wr - expected), 4) if (b_wins + b_losses) >= MIN_RESOLVED else 0.0
        bucket_list.append(ConfidenceBucket(
            label=label,
            min_conf=lo,
            max_conf=min(hi, 1.0),
            total=len(b_rows),
            wins=b_wins,
            losses=b_losses,
            win_rate=b_wr,
            expected_rate=expected,
            calibration_error=cal_err,
        ))

    cal_buckets = [b for b in bucket_list if b.wins + b.losses >= MIN_RESOLVED]
    mean_cal = (
        round(sum(b.calibration_error for b in cal_buckets) / len(cal_buckets), 4)
        if cal_buckets else 0.0
    )

    # --- By state_15m ---
    state_map: Dict[str, dict] = {}
    for r in rows:
        state = r.get("state_15m") or "UNKNOWN"
        if state not in state_map:
            state_map[state] = {"wins": 0, "losses": 0, "neutrals": 0}
        if r["outcome"] == "WIN":
            state_map[state]["wins"] += 1
        elif r["outcome"] == "LOSS":
            state_map[state]["losses"] += 1
        else:
            state_map[state]["neutrals"] += 1
    by_state = {}
    for state, d in state_map.items():
        wr = _win_rate(d["wins"], d["losses"])
        by_state[state] = {
            "wins": d["wins"],
            "losses": d["losses"],
            "neutrals": d["neutrals"],
            "win_rate": wr,
            "total": d["wins"] + d["losses"] + d["neutrals"],
        }

    # --- Avg favorable / adverse ---
    fav_vals = [r["max_favorable_pct"] for r in rows if r.get("max_favorable_pct") is not None]
    adv_vals = [r["max_adverse_pct"] for r in rows if r.get("max_adverse_pct") is not None]

    return AccuracyReport(
        generated_at=datetime.now(UTC),
        period_days=days,
        total_outcomes=len(rows),
        total_wins=total_wins,
        total_losses=total_losses,
        total_neutrals=total_neutrals,
        overall_win_rate=overall_wr,
        long_win_rate=long_wr,
        long_total=len(long_rows),
        short_win_rate=short_wr,
        short_total=len(short_rows),
        by_symbol=symbol_list,
        by_confidence=bucket_list,
        by_state_15m=by_state,
        mean_calibration_error=mean_cal,
        top_symbols=ranked[:5],
        bottom_symbols=list(reversed(ranked))[:5] if len(ranked) > 1 else [],
        avg_max_favorable_pct=round(sum(fav_vals) / len(fav_vals), 2) if fav_vals else 0.0,
        avg_max_adverse_pct=round(sum(adv_vals) / len(adv_vals), 2) if adv_vals else 0.0,
    )
