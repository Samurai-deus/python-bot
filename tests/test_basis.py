"""
И20 (backtest/basis.py): база контракта по имени, зафиксированный доход по формуле правила, круг до поставки по
фактической цене поставки, выбор лучшего контракта, порог 3 % годовых, граница отложенного конца, загрузка. Без сети.
"""
import pytest

from backtest import basis as b

D = b.DAY_MS
T0 = b.day("2024-01-01")


def test_base_of_contract_names():
    assert [b.base_of(s) for s in ("BTC-24MAR23", "BTCUSDT-25DEC26", "ETH-26SEP25", "SOLUSDT-25DEC26", "BTCUSDT")] == ["BTC", "BTC", "ETH", None, None]


def test_locked_and_realized_yield_follow_the_rule_formula():
    r, ann = b.locked_yield(F=102.0, S=100.0, days=73)
    net = 102 * (1 - b.FUT_COST) - 100 * (1 + b.SPOT_COST) - 102 * (b.DELIVERY_FEE + b.SPOT_COST)
    assert r == pytest.approx(net / (100 + 102 / 3)) and ann == pytest.approx(r * 5)
    assert b.realized(102.0, 100.0, 102.0) == pytest.approx(r), "поставка по цене входа — ровно зафиксированное"
    assert b.realized(102.0, 100.0, 150.0) == pytest.approx((net - 48 * (b.DELIVERY_FEE + b.SPOT_COST)) / (100 + 34)), \
        "рост цены к поставке меняет только издержки на продажу спота и поставку"


def market(prem_by_contract, spot=100.0, days=400):
    """Контракты: {символ: (дата поставки, надбавка к споту, цена поставки)}; цены ровные."""
    cs, px = [], {"SPOT:BTC": {T0 + i * D: spot for i in range(days)}}
    for sym, (deliv, prem, dp) in prem_by_contract.items():
        cs.append(b.Contract(sym, "BTC", deliv, dp))
        px[sym] = {T0 + i * D: spot * (1 + prem) for i in range(days) if T0 + i * D <= deliv}
    return cs, px


def test_round_is_held_to_delivery_and_rolled_into_the_best_contract():
    cs, px = market({"BTC-A": (T0 + 60 * D, 0.005, 100.0), "BTC-B": (T0 + 150 * D, 0.03, 100.0), "BTC-C": (T0 + 240 * D, 0.10, 100.0)})
    assert b.locked_yield(100.5, 100.0, 60)[1] < b.MIN_YIELD < b.locked_yield(103.0, 100.0, 150)[1]
    res = b.simulate(cs, px, T0, T0 + 300 * D)
    syms = [r["symbol"] for r in res["rounds"]]
    assert syms[0] == "BTC-B", "C дальше 200 дней, A ниже 3 % годовых"
    assert res["rounds"][0]["exit"] == T0 + 150 * D and res["rounds"][0]["delivered"]
    assert syms[1] == "BTC-C", "в день поставки — вход в лучший доступный"
    assert res["capital"] == pytest.approx((1 + res["rounds"][0]["realized"]) * (1 + res["rounds"][1]["realized"]))


def test_no_entry_below_three_percent_a_year():
    cs, px = market({"BTC-A": (T0 + 100 * D, 0.012, 100.0)})
    assert b.locked_yield(101.2, 100.0, 100)[1] < b.MIN_YIELD, "1,2 % за 100 дней после издержек — ~2 % годовых"
    res = b.simulate(cs, px, T0, T0 + 120 * D)
    first = res["rounds"][0]
    assert first["entry"] > T0 and b.locked_yield(101.2, 100.0, (T0 + 100 * D - first["entry"]) / D)[1] >= b.MIN_YIELD, \
        "та же надбавка на меньшем сроке даёт больше годовых — вход, когда перешло 3 %"
    assert b.locked_yield(101.2, 100.0, (T0 + 100 * D - first["entry"] + D) / D)[1] < b.MIN_YIELD, "а днём раньше — нет"
    assert res["cash_days"] > 0


def test_entry_until_keeps_the_started_round_to_delivery():
    cs, px = market({"BTC-A": (T0 + 60 * D, 0.005, 100.0), "BTC-B": (T0 + 150 * D, 0.03, 100.0)})
    res = b.simulate(cs, px, T0, T0 + 300 * D, entry_until=T0 + 10 * D)
    assert len(res["rounds"]) == 1 and res["rounds"][0]["exit"] == T0 + 150 * D, "круг видимой части доживает до поставки"


def test_undelivered_round_at_data_end_is_closed_at_day_prices():
    cs, px = market({"BTCUSDT-X": (T0 + 150 * D, 0.03, None)}, days=100)
    res = b.simulate(cs, px, T0, T0 + 100 * D)
    r = res["rounds"][-1]
    assert r["delivered"] is False
    assert r["realized"] == pytest.approx(b.closed_early(103.0, 100.0, 103.0, 100.0)) and r["realized"] < 0, "закрыли без схождения — только издержки"


def test_verdict_checks():
    good = {"rounds": [{"realized": 0.01}] * 6, "annual": 0.06}
    assert b.verdict(good, {"annual": 0.035})["passed"]
    assert not b.verdict({**good, "rounds": [{"realized": -0.01}] * 6}, {"annual": 0.04})["passed"], "худший круг −1 %"


def test_download_collects_btc_eth_contracts_and_spot(tmp_path):
    now = T0 + 500 * D
    calls = []

    def get(url, params):
        calls.append((url.rsplit("/", 1)[-1], params.get("category"), params.get("symbol")))
        if url.endswith("delivery-price"):
            return {"result": {"nextPageCursor": "", "list": [{"symbol": "BTC-26SEP25", "deliveryTime": str(T0 + 100 * D), "deliveryPrice": "60000"},
                                                             {"symbol": "SOL-26SEP25", "deliveryTime": str(T0 + 100 * D), "deliveryPrice": "150"}]}}
        if url.endswith("instruments-info"):
            return {"result": {"list": [{"symbol": "ETHUSDT-25DEC26", "contractType": "LinearFutures", "deliveryTime": str(now + 90 * D)},
                                        {"symbol": "BTCUSDT", "contractType": "LinearPerpetual", "deliveryTime": "0"}]}}
        return {"result": {"list": [[str(T0 + i * D), "1", "1", "1", "100"] for i in range(3)]}}
    conn = b.connect(tmp_path / "b.db")
    st = b.download(conn, get=get, now_ms=now)
    assert st["contracts"] == 2
    assert {r[0] for r in conn.execute("SELECT symbol FROM contracts")} == {"BTC-26SEP25", "ETHUSDT-25DEC26"}
    assert conn.execute("SELECT delivery_price FROM contracts WHERE symbol = 'ETHUSDT-25DEC26'").fetchone()[0] is None
    assert ("kline", "spot", "BTCUSDT") in calls and ("kline", "spot", "ETHUSDT") in calls
    cs, px = b.load(conn)
    assert min(px["SPOT:BTC"]) == T0 + D, "закрытие свечи дня d−1 — цена на 00:00 дня d"
