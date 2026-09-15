"""
Клиент демо-субсчёта И18 поверх клиента И14: всегда демо-хост и ТОЛЬКО свой ключ (пустой — ошибка,
а не тихий переход на ключ бота). Плюс запрос демо-средств и доступный остаток (субсчёт создаётся пустым).
"""
import os

from portfolio.client import PortfolioClient


def keys_from_env() -> tuple:
    return os.environ.get("BTCALTS_BYBIT_API_KEY", "").strip(), os.environ.get("BTCALTS_BYBIT_API_SECRET", "").strip()


class BtcAltsClient(PortfolioClient):
    def __init__(self, api_key: str, api_secret: str):
        if not (api_key or "").strip() or not (api_secret or "").strip():
            raise RuntimeError("BTCALTS_BYBIT_API_KEY / BTCALTS_BYBIT_API_SECRET не заданы — ключ бота для И18 не используется")
        super().__init__(api_key=api_key.strip(), api_secret=api_secret.strip(), demo=True)

    def available_usd(self) -> float:
        return float(self.wallet().get("totalAvailableBalance") or 0)

    def apply_demo_usdt(self, amount: float) -> None:
        """Запрос демо-средств (Bybit: не больше 100 000 USDT за раз, не чаще раза в минуту)."""
        self._post("/v5/account/demo-apply-money", body={
            "adjustType": 0, "utaDemoApplyMoney": [{"coin": "USDT", "amountStr": str(int(amount))}]}, signed=True)
