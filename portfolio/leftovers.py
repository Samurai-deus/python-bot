"""
Закрытие хвостов после ребалансировки (И14, И18): позиции по монетам исполнителя, которых нет в целях
последнего прогона. Раз в час, после шага ребалансировки. Случай 21.09.2026: у И14 после ребалансировки
висели 8 выбывших монет — закрывающие ордера считались по цене входа и не закрывали позицию целиком.
"""
import logging
from typing import Callable, List

from portfolio import engine
from portfolio.store import Store

logger = logging.getLogger(__name__)


def close_leftovers(cli, store: Store, now: int, notify: Callable[[str], bool], name: str) -> List[str]:
    last = store.last_weights()
    if last is None:
        return []
    orders = engine.leftover_orders(cli.positions_qty(), last, store.traded_symbols())
    closed, failed = [], []
    for o in orders:
        try:
            cli.place_order(o.symbol, o.side, o.qty, reduce_only=True)
            closed.append(o.symbol)
        except Exception as exc:   # не закрылся — повтор следующим часом
            failed.append(f"{o.symbol}: {type(exc).__name__}: {exc}"[:120])
    if closed or failed:
        store.event(now, "leftover", f"закрыто {len(closed)}: {', '.join(closed)}" + (f"; не закрыто: {'; '.join(failed)}" if failed else ""))
        notify(f"🧹 {name}: закрыты позиции вне целей ({len(closed)}): {', '.join(s[:-4] for s in closed)}"
               + (f"; НЕ ЗАКРЫТО {len(failed)} — повтор через час" if failed else ""))
    return closed
