"""
Стакан одного символа из потока Bybit orderbook.{depth}.{symbol}: снимок + изменения.

Формат (проверен на живом потоке 13.09.2026): data = {"s", "b": [[цена, объём], ...], "a": [...],
"u": номер обновления, "seq"}; объём "0" — уровень удалён; u идёт подряд; u = 1 — биржа
перезапустила поток и это снимок. Цены — строки как пришли (ключи без округления).
"""
from typing import Dict, List, Tuple

OK, IGNORED, GAP = "ok", "ignored", "gap"


class OrderBook:
    def __init__(self) -> None:
        self.bids: Dict[str, str] = {}
        self.asks: Dict[str, str] = {}
        self.ready = False
        self.update_id = None

    def reset(self) -> None:
        self.bids, self.asks, self.ready, self.update_id = {}, {}, False, None

    def apply(self, kind: str, data: dict) -> str:
        """OK — применено; IGNORED — изменение до снимка; GAP — пропуск номера, стакан недостоверен."""
        u = data.get("u")
        if kind == "snapshot" or u == 1:
            self.bids = {p: s for p, s in data.get("b", [])}
            self.asks = {p: s for p, s in data.get("a", [])}
            self.ready, self.update_id = True, u
            return OK
        if not self.ready:
            return IGNORED
        if self.update_id is not None and u is not None and u != self.update_id + 1:
            self.reset()
            return GAP
        for side, levels in ((self.bids, data.get("b", [])), (self.asks, data.get("a", []))):
            for price, size in levels:
                if float(size) == 0:
                    side.pop(price, None)
                else:
                    side[price] = size
        self.update_id = u
        return OK

    def top(self, n: int) -> Tuple[List[List[str]], List[List[str]]]:
        """Лучшие n уровней: покупки по убыванию цены, продажи по возрастанию."""
        bids = sorted(self.bids.items(), key=lambda kv: -float(kv[0]))[:n]
        asks = sorted(self.asks.items(), key=lambda kv: float(kv[0]))[:n]
        return [[p, s] for p, s in bids], [[p, s] for p, s in asks]
