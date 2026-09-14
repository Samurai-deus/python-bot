"""
Клиент И14 поверх BybitClient бота (тот же демо-счёт и ключ из окружения): свечи 4h в форме
данных бэктестов, подписанные позиции, стоимость счёта, начисления фандинга.
"""
from typing import Dict, List, Sequence

from backtest import momentum_xs as mx
from exchange.bybit_client import BybitClient

KLINES = 200                        # 4h × 200 = 33 дня: И4 нужны 31 день, И3 — 29


class PortfolioClient(BybitClient):
    def market_data(self, symbols: Sequence[str], t: int) -> Dict[str, dict]:
        """
        {символ: {"c4": закрытия по ts, "o4": открытия по ts, "o1": {t: open}}} — как load() в
        бэктестах. Бар, открывшийся в t, ещё идёт: его close в c4 не кладётся (сигналы берут только
        закрытые), open — в o4[t] и o1[t] (цена входа; 1h-бар 00:00 открывается по той же цене).
        """
        out = {}
        for s in symbols:
            rows = self.get_klines(s, "240", KLINES)
            c4, o4 = {}, {}
            for r in rows:
                ts, o, c = int(r[0]), float(r[1]), float(r[4])
                o4[ts] = o
                if ts + mx.H4_MS <= t:
                    c4[ts] = c
            if t not in o4:
                continue
            out[s] = {"c4": c4, "o4": o4, "o1": {t: o4[t]}}
        return out

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

    def total_equity(self) -> float:
        return float(self.wallet().get("totalEquity") or 0)

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
