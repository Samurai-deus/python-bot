"""
Trade Learner -- feedback loop that adjusts confidence based on historical outcomes.

Uses data from AccuracyAnalyzer (signal_outcomes table) to:
- Penalize symbols with low win rates
- Reward symbols with high win rates
- Calibrate overconfident/underconfident signals
- Hard-block symbols with extremely poor track records

NON-CRITICAL module: all public functions fail-open (return neutral values).
Cache is refreshed every 30 minutes to avoid DB queries on every tick.
"""
import logging
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Cache settings ──
_CACHE_TTL_SECONDS = 1800  # 30 minutes
_cache: Dict = {}
_cache_ts: float = 0.0

# ── Thresholds ──
MIN_OUTCOMES_FOR_ADJUSTMENT = 5
MIN_OUTCOMES_FOR_BLOCK = 10
BLOCK_WIN_RATE = 0.20  # < 20% win rate with 10+ outcomes -> hard block


def _refresh_cache() -> None:
    """Reload accuracy data from DB if cache is stale."""
    global _cache, _cache_ts

    now = time.monotonic()
    if _cache and (now - _cache_ts) < _CACHE_TTL_SECONDS:
        return

    try:
        from database import get_outcomes_for_analysis

        rows = get_outcomes_for_analysis(days=60)
        if not rows:
            logger.debug("[Learning] No outcome data available")
            _cache = {}
            _cache_ts = now
            return

        # ── By symbol ──
        by_symbol: Dict[str, Dict] = {}
        for r in rows:
            sym = r["symbol"]
            if sym not in by_symbol:
                by_symbol[sym] = {"wins": 0, "losses": 0, "total": 0}
            by_symbol[sym]["total"] += 1
            if r["outcome"] == "WIN":
                by_symbol[sym]["wins"] += 1
            elif r["outcome"] == "LOSS":
                by_symbol[sym]["losses"] += 1

        # ── By symbol + direction ──
        by_symbol_dir: Dict[str, Dict] = {}
        for r in rows:
            key = f"{r['symbol']}_{r.get('direction', 'UNKNOWN')}"
            if key not in by_symbol_dir:
                by_symbol_dir[key] = {"wins": 0, "losses": 0, "total": 0}
            by_symbol_dir[key]["total"] += 1
            if r["outcome"] == "WIN":
                by_symbol_dir[key]["wins"] += 1
            elif r["outcome"] == "LOSS":
                by_symbol_dir[key]["losses"] += 1

        # ── By confidence bucket ──
        buckets = {
            "low": {"range": (0.0, 0.5), "wins": 0, "losses": 0},
            "high": {"range": (0.7, 1.01), "wins": 0, "losses": 0},
        }
        for r in rows:
            conf = r.get("confidence")
            if conf is None:
                continue
            for bname, bdata in buckets.items():
                lo, hi = bdata["range"]
                if lo <= conf < hi:
                    if r["outcome"] == "WIN":
                        bdata["wins"] += 1
                    elif r["outcome"] == "LOSS":
                        bdata["losses"] += 1

        _cache = {
            "by_symbol": by_symbol,
            "by_symbol_dir": by_symbol_dir,
            "confidence_buckets": buckets,
        }
        _cache_ts = now
        logger.info(
            "[Learning] Cache refreshed: %d outcomes, %d symbols",
            len(rows), len(by_symbol),
        )

    except Exception as e:
        logger.warning("[Learning] Failed to refresh cache: %s", e)
        # Keep stale cache if available; if empty, stay empty
        _cache_ts = now  # Don't retry immediately


def _win_rate(wins: int, losses: int) -> float:
    total = wins + losses
    return wins / total if total > 0 else 0.5  # default 50% if no resolved data


def get_symbol_adjustment(symbol: str, side: str) -> float:
    """
    Returns confidence adjustment for a symbol based on historical win rate.

    Range: -0.15 to +0.10.
    Fail-open: returns 0.0 on any error.
    """
    try:
        _refresh_cache()
        if not _cache:
            return 0.0

        adjustment = 0.0

        # ── Symbol-level adjustment ──
        sym_data = _cache.get("by_symbol", {}).get(symbol)
        if sym_data:
            resolved = sym_data["wins"] + sym_data["losses"]
            if resolved >= MIN_OUTCOMES_FOR_ADJUSTMENT:
                wr = _win_rate(sym_data["wins"], sym_data["losses"])
                if wr < 0.30:
                    adjustment = -0.15
                elif wr < 0.40:
                    adjustment = -0.10
                elif wr > 0.75:
                    adjustment = +0.10
                elif wr > 0.65:
                    adjustment = +0.05

        # ── Direction-level penalty (additive, only negative) ──
        direction = "LONG" if side.upper() in ("LONG", "BUY") else "SHORT"
        dir_key = f"{symbol}_{direction}"
        dir_data = _cache.get("by_symbol_dir", {}).get(dir_key)
        if dir_data:
            resolved = dir_data["wins"] + dir_data["losses"]
            if resolved >= MIN_OUTCOMES_FOR_ADJUSTMENT:
                wr = _win_rate(dir_data["wins"], dir_data["losses"])
                if wr < 0.25:
                    adjustment -= 0.10

        # Clamp to symmetric range (was asymmetric: -0.15/+0.10)
        adjustment = max(-0.15, min(0.15, adjustment))
        return adjustment

    except Exception as e:
        logger.warning("[Learning] get_symbol_adjustment error: %s", e)
        return 0.0


def get_confidence_calibration(confidence: float) -> float:
    """
    Returns calibration adjustment based on confidence bucket accuracy.

    High confidence but low real win rate -> penalize (overconfident).
    Low confidence but high real win rate -> reward (underconfident).

    Range: -0.10 to +0.05.
    Fail-open: returns 0.0 on any error.
    """
    try:
        _refresh_cache()
        if not _cache:
            return 0.0

        buckets = _cache.get("confidence_buckets", {})

        # High confidence (> 0.7) but poor real win rate
        if confidence > 0.7:
            high = buckets.get("high", {})
            resolved = high.get("wins", 0) + high.get("losses", 0)
            if resolved >= MIN_OUTCOMES_FOR_ADJUSTMENT:
                wr = _win_rate(high["wins"], high["losses"])
                if wr < 0.40:
                    logger.debug(
                        "[Learning] Overconfident calibration: conf=%.2f real_wr=%.2f -> -0.10",
                        confidence, wr,
                    )
                    return -0.10

        # Low confidence (0.3-0.5) but good real win rate
        if 0.3 <= confidence <= 0.5:
            low = buckets.get("low", {})
            resolved = low.get("wins", 0) + low.get("losses", 0)
            if resolved >= MIN_OUTCOMES_FOR_ADJUSTMENT:
                wr = _win_rate(low["wins"], low["losses"])
                if wr > 0.60:
                    logger.debug(
                        "[Learning] Underconfident calibration: conf=%.2f real_wr=%.2f -> +0.05",
                        confidence, wr,
                    )
                    return +0.05

        return 0.0

    except Exception as e:
        logger.warning("[Learning] get_confidence_calibration error: %s", e)
        return 0.0


def should_skip_symbol(symbol: str) -> bool:
    """
    Returns True if symbol has extremely poor track record and should be skipped.

    Requires >= 10 resolved outcomes AND win_rate < 20%.
    Fail-open: returns False on any error.
    """
    try:
        _refresh_cache()
        if not _cache:
            return False

        sym_data = _cache.get("by_symbol", {}).get(symbol)
        if not sym_data:
            return False

        resolved = sym_data["wins"] + sym_data["losses"]
        if resolved < MIN_OUTCOMES_FOR_BLOCK:
            return False

        wr = _win_rate(sym_data["wins"], sym_data["losses"])
        if wr < BLOCK_WIN_RATE:
            logger.info(
                "[Learning] Blocking %s: win_rate=%.1f%% (%d/%d resolved)",
                symbol, wr * 100, sym_data["wins"], resolved,
            )
            return True

        return False

    except Exception as e:
        logger.warning("[Learning] should_skip_symbol error: %s", e)
        return False
