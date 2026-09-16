"""
Клиент И14 поверх BybitClient бота (тот же демо-счёт и ключ из окружения): свечи 4h в форме
данных бэктестов, подписанные позиции, стоимость счёта, начисления фандинга.
"""
import logging
import time
from typing import Dict, List, Optional, Sequence

from backtest import momentum_xs as mx
from backtest.wide_search import is_crypto, is_crypto_instrument, unknown_types
from exchange.bybit_client import BybitClient

logger = logging.getLogger(__name__)

KLINES = 200                        # 4h × 200 = 33 дня: И4 нужны 31 день, И3 — 29, обороту И17а — 30
TURNOVER_D = 30
CANDIDATES = 80                     # кандидаты И17а: крупнейшие по обороту за 24 ч, из них top30 по обороту за 30 дней


class PortfolioClient(BybitClient):
    def market_data(self, symbols: Sequence[str], t: int, ages: Optional[Dict[str, int]] = None) -> Dict[str, dict]:
        """
        {символ: {"c4": закрытия по ts, "o4": открытия по ts, "o1": {t: open}}} — как load() в
        бэктестах. Бар, открывшийся в t, ещё идёт: его close в c4 не кладётся (сигналы берут только
        закрытые), open — в o4[t] и o1[t] (цена входа; 1h-бар 00:00 открывается по той же цене).
        """
        out = {}
        ages = ages or {}   # возраст контрактов — параметр, чтобы данные не ходили в сеть сверх свечей (тесты, CI без Bybit)
        for s in symbols:
            rows = self.get_klines(s, "240", KLINES)
            c4, o4 = {}, {}
            turnover = 0.0
            for r in rows:
                ts, o, c = int(r[0]), float(r[1]), float(r[4])
                o4[ts] = o
                if ts + mx.H4_MS <= t:
                    c4[ts] = c
                    if ts >= t - TURNOVER_D * mx.DAY_MS:
                        turnover += float(r[6]) if len(r) > 6 and r[6] not in (None, "") else 0.0
            if t not in o4:
                continue
            out[s] = {"c4": c4, "o4": o4, "o1": {t: o4[t]}, "turn30": turnover / TURNOVER_D, "age_d": ages.get(s, 0)}
        return out

    def launch_ages_d(self) -> Dict[str, int]:
        """
        Возраст крипто-контрактов в днях по launchTime (публичный список инструментов, страницами).
        Акции, ETF, сырьё и валюты по признаку биржи (symbolType/marketRegion) сюда не попадают — а без
        возраста контракт не кандидат (continuation_candidates, market_data). Так 110126 не доходит до ордера.
        """
        out, cursor, now_ms, odd = {}, "", int(time.time() * 1000), {}
        for _ in range(10):
            params = {"category": "linear", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/v5/market/instruments-info", params=params, signed=False)
            items = data.get("list") or []
            for t, n in unknown_types(items).items():
                odd[t] = odd.get(t, 0) + n
            for i in items:
                if i.get("launchTime") and is_crypto_instrument(i):
                    out[i["symbol"]] = int((now_ms - int(i["launchTime"])) / mx.DAY_MS)
            cursor = data.get("nextPageCursor") or ""
            if not cursor:
                break
        if odd:
            logger.warning("instruments-info: неизвестный symbolType, контракты исключены из кандидатов: %s "
                           "(если это крипта — добавить в CRYPTO_TYPES)", odd)
        return out

    def continuation_candidates(self, ages: Dict[str, int]) -> List[str]:
        """Кандидаты И17а: крипто-контракты USDT старше 100 дней (ages — из launch_ages_d), крупнейшие по обороту за 24 ч."""
        from portfolio import engine
        rows = self._get("/v5/market/tickers", params={"category": "linear"}, signed=False).get("list") or []
        ok = [(float(r.get("turnover24h") or 0), r["symbol"]) for r in rows
              if r["symbol"].endswith("USDT") and is_crypto(r["symbol"]) and ages.get(r["symbol"], 0) >= engine.CONT_MIN_AGE_D]
        ok.sort(reverse=True)
        return [s for _, s in ok[:CANDIDATES]]

    def positions_usdt(self) -> Dict[str, float]:
        """Подписанный номинал открытых позиций по цене входа: + лонг, − шорт."""
        out = {}
        for p in self.get_positions():
            if p.size > 0:
                sign = 1 if str(p.side).lower() in ("buy", "long") else -1
                out[p.symbol] = sign * p.size * p.entry_price
        return out

    def positions_qty(self) -> Dict[str, float]:
        out = {}
        for p in self.get_positions():
            if p.size > 0:
                out[p.symbol] = (1 if str(p.side).lower() in ("buy", "long") else -1) * p.size
        return out

    def wallet(self) -> dict:
        data = self._get("/v5/account/wallet-balance", params={"accountType": "UNIFIED"}, signed=True)
        return (data.get("list") or [{}])[0]

    def usdt_equity(self) -> float:
        """
        USDT-часть счёта: баланс USDT + нереализованный результат USDT-контрактов (поле equity монеты
        USDT в UTA). Весь кошелёк (totalEquity) не годится: на демо-счёте лежат стартовые BTC и ETH,
        их переоценка — тысячи USDT в минуту (14.09: «просадка 3953 USDT» при позициях на 1356).
        """
        for coin in self.wallet().get("coin") or []:
            if coin.get("coin") == "USDT":
                if coin.get("equity") not in (None, ""):
                    return float(coin["equity"])
                return float(coin.get("walletBalance") or 0) + float(coin.get("unrealisedPnl") or 0)
        return 0.0

    def settlements(self, start_ms: int) -> List[dict]:
        out, cursor = [], ""
        for _ in range(20):
            params = {"accountType": "UNIFIED", "category": "linear", "type": "SETTLEMENT",
                      "startTime": int(start_ms), "limit": 50}
            if cursor:
                params["cursor"] = cursor
            data = self._get("/v5/account/transaction-log", params=params, signed=True)
            out += data.get("list") or []
            cursor = data.get("nextPageCursor") or ""
            if not cursor:
                break
        return out
