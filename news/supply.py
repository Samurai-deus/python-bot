"""
Еженедельная запись предложения монет с CoinGecko (бесплатный API, без ключа): circulating / total / max
supply, капитализация, цена — таблица coin_supply базы бота. Данные под механизм И18 «альты истекают
к BTC» (docs/TRADER_PLAN.md, техдолг п. 9): через 16 недель сбора можно будет сравнить недельные
результаты корзины с ростом предложения. Правила по этим данным нет — только сбор.

Неделя — с понедельника 00:00 UTC; запись одна на неделю: PAGES страниц /coins/markets по капитализации
(по 250 монет) плюс публичный список USDT-perp Bybit — чтобы в журнале было видно, сколько крипто-контрактов
покрыто. Символ CoinGecko — верхним регистром; база контракта — без множителя 1000/10000 (1000PEPE → PEPE);
тёзки по тикеру — берётся первая по капитализации. Сбой — повтор не раньше чем через RETRY_MS (лимит
бесплатного API — десятки запросов в минуту), а не каждые 2 минуты опроса новостей.
"""
import logging
import time
from typing import Dict, List, Optional, Set

from backtest.wide_search import base_of, is_crypto_instrument

logger = logging.getLogger(__name__)

MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"
BYBIT_INSTRUMENTS_URL = "https://api.bybit.com/v5/market/instruments-info"
PAGES = 4                   # 4 × 250 = top-1000 по капитализации
PER_PAGE = 250
PAGE_PAUSE_S = 2.0
WEEK_MS = 7 * 86_400_000
RETRY_MS = 60 * 60 * 1000
_next_try_ms = 0


def week_start_ms(now_ms: int) -> int:
    """Понедельник 00:00 UTC недели, в которую попадает now_ms (1970-01-01 — четверг, поэтому сдвиг на 4 дня)."""
    return (now_ms - 4 * 86_400_000) // WEEK_MS * WEEK_MS + 4 * 86_400_000


def _num(v) -> Optional[float]:
    return None if v in (None, "") else float(v)


def fetch_markets(http, pages: int = PAGES, pause_s: float = PAGE_PAUSE_S) -> List[dict]:
    """Монеты по убыванию капитализации; короткая страница — конец списка."""
    out: List[dict] = []
    for page in range(1, pages + 1):
        if page > 1 and pause_s:
            time.sleep(pause_s)
        r = http.get(MARKETS_URL, params={"vs_currency": "usd", "order": "market_cap_desc", "per_page": PER_PAGE,
                                          "page": page, "sparkline": "false"})
        r.raise_for_status()
        rows = r.json()
        for i, c in enumerate(rows):
            out.append({"cg_id": str(c["id"]), "symbol": str(c.get("symbol") or "").upper(), "name": c.get("name"),
                        "rank": c.get("market_cap_rank") or (page - 1) * PER_PAGE + i + 1, "price": _num(c.get("current_price")),
                        "market_cap": _num(c.get("market_cap")), "circulating": _num(c.get("circulating_supply")),
                        "total_supply": _num(c.get("total_supply")), "max_supply": _num(c.get("max_supply"))})
        if len(rows) < PER_PAGE:
            break
    return out


def fetch_bybit_bases(http) -> Set[str]:
    """Базы крипто-контрактов USDT-perp Bybit (публичный список, страницами; признак биржи + имена)."""
    bases, cursor = set(), ""
    for _ in range(10):
        params = {"category": "linear", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        r = http.get(BYBIT_INSTRUMENTS_URL, params=params)
        r.raise_for_status()
        result = r.json().get("result") or {}
        for i in result.get("list") or []:
            if (i.get("quoteCoin") == "USDT" and i.get("contractType") == "LinearPerpetual" and i.get("status") == "Trading"
                    and is_crypto_instrument(i)):
                bases.add(base_of(i["symbol"]))
        cursor = result.get("nextPageCursor") or ""
        if not cursor:
            break
    return bases


def match(rows: List[dict], bases: Set[str]) -> Dict[str, str]:
    """{база Bybit: cg_id} — первая монета с таким тикером по капитализации (тёзки ниже не в счёт)."""
    out: Dict[str, str] = {}
    for c in rows:
        if c["symbol"] in bases and c["symbol"] not in out:
            out[c["symbol"]] = c["cg_id"]
    return out


def record(http, now_ms: int) -> dict:
    """Записать неделю: все монеты страниц, у покрытых контрактов — bybit_base. Возвращает счётчики."""
    import database
    rows = fetch_markets(http)
    bases = fetch_bybit_bases(http)
    matched = match(rows, bases)
    by_id = {cg_id: base for base, cg_id in matched.items()}
    for c in rows:
        c["bybit_base"] = by_id.get(c["cg_id"])
    week = week_start_ms(now_ms)
    saved = database.save_coin_supply(week, now_ms, rows)
    return {"week_ms": week, "coins": len(rows), "saved": saved, "bases": len(bases), "matched": len(matched)}


def maybe_record(http, now_ms: int) -> Optional[dict]:
    """Раз в неделю; после сбоя — не раньше чем через RETRY_MS. None — ничего не делалось."""
    global _next_try_ms
    import database
    if now_ms < _next_try_ms or database.coin_supply_recorded(week_start_ms(now_ms)):
        return None
    try:
        return record(http, now_ms)
    except Exception:  # сеть, лимит API, формат — повтор через час, сборщик новостей не страдает
        _next_try_ms = now_ms + RETRY_MS
        logger.warning("supply: запись предложения монет не удалась, повтор через час", exc_info=True)
        return None
