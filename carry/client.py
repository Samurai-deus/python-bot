"""
Клиент демо-субсчёта И13 поверх BybitClient: всегда демо-хост и ТОЛЬКО свой ключ. Пустой ключ —
ошибка, а не тихий переход на ключ основного бота (BybitClient при пустом ключе берёт BYBIT_API_KEY).
Добавлены спот, кошелёк, поддерживающая маржа, исполнения и начисления фандинга.
"""
import os
from typing import Dict, List, Optional

from exchange.bybit_client import BybitClient, fmt_number, new_order_link_id


def keys_from_env() -> tuple:
    return os.environ.get("CARRY_BYBIT_API_KEY", "").strip(), os.environ.get("CARRY_BYBIT_API_SECRET", "").strip()


class CarryClient(BybitClient):
    def __init__(self, api_key: str, api_secret: str):
        if not (api_key or "").strip() or not (api_secret or "").strip():
            raise RuntimeError("CARRY_BYBIT_API_KEY / CARRY_BYBIT_API_SECRET не заданы — "
                               "ключ основного бота для И13 не используется")
        super().__init__(api_key=api_key.strip(), api_secret=api_secret.strip(), demo=True)

    # --- кошелёк ---------------------------------------------------------
    def wallet(self) -> Dict:
        data = self._get("/v5/account/wallet-balance", params={"accountType": "UNIFIED"}, signed=True)
        return (data.get("list") or [{}])[0]

    def coin_balances(self) -> Dict[str, float]:
        return {c["coin"]: float(c.get("walletBalance") or 0) for c in self.wallet().get("coin") or []}

    def total_equity(self) -> float:
        return float(self.wallet().get("totalEquity") or 0)

    def account_mm_rate(self) -> Optional[float]:
        value = self.wallet().get("accountMMRate")
        return float(value) if value not in (None, "") else None

    def apply_demo_usdt(self, amount: float) -> None:
        """Запрос демо-средств (Bybit: не больше 100 000 USDT за раз, не чаще раза в минуту)."""
        self._post("/v5/account/demo-apply-money", body={
            "adjustType": 0, "utaDemoApplyMoney": [{"coin": "USDT", "amountStr": str(int(amount))}]}, signed=True)

    # --- цены и шаги -----------------------------------------------------
    def spot_price(self, symbol: str) -> float:
        data = self._get("/v5/market/tickers", params={"category": "spot", "symbol": symbol}, signed=False)
        return float((data.get("list") or [{}])[0].get("lastPrice") or 0)

    def spot_base_step(self, symbol: str) -> float:
        data = self._get("/v5/market/instruments-info", params={"category": "spot", "symbol": symbol}, signed=False)
        return float(((data.get("list") or [{}])[0].get("lotSizeFilter") or {}).get("basePrecision") or 0)

    # --- ордера ----------------------------------------------------------
    def spot_market(self, symbol: str, side: str, qty_base: float) -> str:
        """Рыночный спот-ордер на объём в монете (marketUnit=baseCoin)."""
        body = {"category": "spot", "symbol": symbol, "side": side, "orderType": "Market",
                "qty": fmt_number(qty_base), "marketUnit": "baseCoin", "orderLinkId": new_order_link_id()}
        data = self._post("/v5/order/create", body=body, signed=True, retry_network=False, retry_server=False)
        return data.get("orderId", "")

    def perp_market(self, symbol: str, side: str, qty: float, reduce_only: bool = False) -> str:
        return self.place_order(symbol, side, qty, reduce_only=reduce_only).order_id

    def short_qty(self, symbol: str) -> float:
        return sum(p.size for p in self.get_positions(symbol) if str(p.side).lower() in ("sell", "short"))

    # --- история ---------------------------------------------------------
    def _pages(self, path: str, params: Dict) -> List[Dict]:
        out, cursor = [], ""
        for _ in range(20):
            data = self._get(path, params=dict(params, **({"cursor": cursor} if cursor else {})), signed=True)
            out += data.get("list") or []
            cursor = data.get("nextPageCursor") or ""
            if not cursor:
                break
        return out

    def executions(self, category: str, start_ms: int) -> List[Dict]:
        return self._pages("/v5/execution/list", {"category": category, "startTime": int(start_ms), "limit": 100})

    def settlements(self, start_ms: int) -> List[Dict]:
        return self._pages("/v5/account/transaction-log", {"accountType": "UNIFIED", "category": "linear",
                                                           "type": "SETTLEMENT", "startTime": int(start_ms),
                                                           "limit": 50})
