"""
Поправка учёта при внешнем движении средств (portfolio/adjust.py): start_equity, снимки до момента, peak_equity,
событие, защита от повтора; та же команда для базы И13 (carry.store). Кейс 15–16.09: проба +1 000 USDT на И18.
"""
from carry.store import Store as CarryStore
from portfolio import adjust
from portfolio.store import Store

T0 = 1_789_344_000_000
H = 3_600_000


def filled(path):
    s = Store(path)
    s.set("start_equity", 100_000.0)
    s.set("peak_equity", 100_050.0)
    for i, eq in enumerate((100_000.0, 100_050.0, 99_975.0, 100_962.0, 100_947.0)):
        s.snapshot(T0 + i * H, eq, {})
    return s


def test_flow_now_shifts_start_all_snapshots_and_peak(tmp_path):
    s = filled(str(tmp_path / "p.db"))
    r = adjust.external_flow(s.conn, 1000.0, "проба демо-средств", T0 + 10 * H)
    assert r == {"applied": True, "snapshots": 5, "start_equity": 101_000.0, "peak_equity": 101_050.0}
    assert float(s.get("start_equity")) == 101_000.0 and float(s.get("peak_equity")) == 101_050.0
    assert [r[0] for r in s.conn.execute("SELECT equity FROM snapshots ORDER BY ts")] == [101_000.0, 101_050.0, 100_975.0, 101_962.0, 101_947.0]
    events = s.conn.execute("SELECT kind, detail FROM events").fetchall()
    assert len(events) == 1 and events[0][0] == "adjust" and "[проба демо-средств]" in events[0][1]


def test_late_flow_shifts_only_snapshots_before_it_and_leaves_peak(tmp_path):
    s = filled(str(tmp_path / "p.db"))
    r = adjust.external_flow(s.conn, 1000.0, "поздно", T0 + 10 * H, at_ms=T0 + 3 * H)
    assert r["snapshots"] == 3 and r["peak_equity"] is None
    assert float(s.get("peak_equity")) == 100_050.0, "движение в прошлом — пик не трогаем"
    assert [r[0] for r in s.conn.execute("SELECT equity FROM snapshots ORDER BY ts")] == [101_000.0, 101_050.0, 100_975.0, 100_962.0, 100_947.0]


def test_same_reason_is_not_applied_twice(tmp_path):
    s = filled(str(tmp_path / "p.db"))
    assert adjust.external_flow(s.conn, 1000.0, "x", T0)["applied"]
    assert not adjust.external_flow(s.conn, 1000.0, "x", T0 + H)["applied"]
    assert float(s.get("start_equity")) == 101_000.0
    assert adjust.main(["--db", str(tmp_path / "p.db"), "--amount", "1000", "--reason", "x"]) == 1
    assert adjust.main(["--db", str(tmp_path / "p.db"), "--amount", "-250", "--reason", "y"]) == 0
    assert float(s.get("start_equity")) == 100_750.0


def test_works_on_carry_store(tmp_path):
    c = CarryStore(str(tmp_path / "c.db"))
    c.set("start_equity", 40_000.0)
    c.snapshot(T0, 40_010.0, 0.004, {}, {})
    r = adjust.external_flow(c.conn, 500.0, "перевод", T0 + H)
    assert r["applied"] and r["snapshots"] == 1 and r["start_equity"] == 40_500.0 and r["peak_equity"] is None
    assert c.conn.execute("SELECT equity FROM snapshots").fetchone()[0] == 40_510.0
