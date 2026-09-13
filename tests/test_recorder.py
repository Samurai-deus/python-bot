"""
Запись стакана и сделок Bybit (recorder): стакан из снимка и изменений в формате живого потока,
разрыв последовательности, лучшие уровни, часовые файлы и их смена, предел места, разбор
сообщений, снимки только достоверных стаканов, проверка здоровья по пульсу.
"""
import gzip
import json
from datetime import UTC, datetime

from recorder import health
from recorder.__main__ import Recorder, chunks, topics
from recorder.book import GAP, IGNORED, OK, OrderBook
from recorder.storage import HourlyWriter, enforce_cap

SNAP = {"s": "BTCUSDT", "b": [["77274.30", "2.988"], ["77274.20", "0.001"], ["77270.00", "1.5"]],
        "a": [["77274.40", "0.5"], ["77275.00", "1.0"]], "u": 143415978, "seq": 809383116207}


def ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp() * 1000)


def test_snapshot_then_deltas_add_change_and_delete_levels():
    book = OrderBook()
    assert book.apply("snapshot", SNAP) == OK
    assert book.apply("delta", {"b": [["77274.20", "0"], ["77272.40", "0.001"]], "a": [["77274.40", "0.7"]],
                                "u": 143415979}) == OK
    bids, asks = book.top(20)
    assert bids == [["77274.30", "2.988"], ["77272.40", "0.001"], ["77270.00", "1.5"]], "покупки по убыванию, «0» удаляет"
    assert asks == [["77274.40", "0.7"], ["77275.00", "1.0"]], "продажи по возрастанию, объём заменён"
    assert book.top(1) == ([["77274.30", "2.988"]], [["77274.40", "0.7"]])


def test_a_delta_before_the_snapshot_is_ignored_and_a_skipped_update_is_a_gap():
    book = OrderBook()
    assert book.apply("delta", {"b": [["1", "1"]], "a": [], "u": 5}) == IGNORED and not book.ready
    book.apply("snapshot", SNAP)
    assert book.apply("delta", {"b": [], "a": [], "u": SNAP["u"] + 2}) == GAP
    assert not book.ready and book.bids == {}, "после разрыва стакан недостоверен до нового снимка"


def test_update_id_one_is_a_restart_snapshot():
    book = OrderBook()
    book.apply("snapshot", SNAP)
    assert book.apply("delta", {"b": [["10", "1"]], "a": [["11", "1"]], "u": 1}) == OK
    assert book.top(5) == ([["10", "1"]], [["11", "1"]])


def read_lines(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_hourly_files_rotate_at_the_hour_and_reopen_by_appending(tmp_path):
    w = HourlyWriter(tmp_path)
    w.write("trades", "BTCUSDT", ms("2026-09-13T10:59:59"), {"n": 1})
    w.write("trades", "BTCUSDT", ms("2026-09-13T11:00:00"), {"n": 2})
    w.close()
    first = tmp_path / "trades" / "BTCUSDT" / "2026-09-13" / "10.jsonl.gz"
    second = tmp_path / "trades" / "BTCUSDT" / "2026-09-13" / "11.jsonl.gz"
    assert read_lines(first) == [{"n": 1}] and read_lines(second) == [{"n": 2}]
    w2 = HourlyWriter(tmp_path)                       # перезапуск в тот же час
    w2.write("trades", "BTCUSDT", ms("2026-09-13T11:30:00"), {"n": 3})
    w2.close()
    assert read_lines(second) == [{"n": 2}, {"n": 3}]


def test_the_cap_removes_the_oldest_hours_first_but_never_open_files(tmp_path):
    w = HourlyWriter(tmp_path)
    for hour in ("2026-09-12T23:00:00", "2026-09-13T00:00:00", "2026-09-13T01:00:00"):
        for kind in ("book", "trades"):
            w.write(kind, "BTCUSDT", ms(hour), {"pad": "x" * 2000})
    w.flush()
    open_now = w.open_paths()
    w.close()
    files = sorted(tmp_path.glob("*/*/*/*.jsonl.gz"))
    one = max(p.stat().st_size for p in files)
    removed = enforce_cap(tmp_path, cap_bytes=len(files) * one - 3 * one, keep=open_now)
    names = sorted(f"{p.parent.name}/{p.name}" for p in removed)
    assert names[:2] == ["2026-09-12/23.jsonl.gz", "2026-09-12/23.jsonl.gz"], "сначала самый старый час"
    assert not (tmp_path / "book" / "BTCUSDT" / "2026-09-12").exists(), "пустой день убран"
    assert all(p not in removed for p in open_now)
    assert enforce_cap(tmp_path, cap_bytes=0, keep=open_now) and all(p.exists() for p in open_now)


class FakeWriter:
    def __init__(self):
        self.rows = []

    def write(self, kind, symbol, t_ms, record):
        self.rows.append((kind, symbol, t_ms, record))


def test_trades_are_written_with_their_own_time_and_fields():
    w = FakeWriter()
    rec = Recorder(["BTCUSDT"], w, clock=lambda: 100.0)
    msg = {"topic": "publicTrade.BTCUSDT", "type": "snapshot", "ts": 1789280051875,
           "data": [{"T": 1789280051874, "s": "BTCUSDT", "S": "Buy", "v": "0.433", "p": "77274.40",
                     "L": "ZeroMinusTick", "i": "86f7e3ff", "BT": False, "RPI": False, "seq": 1}]}
    assert rec.handle(msg) and rec.last_message == 100.0
    assert w.rows == [("trades", "BTCUSDT", 1789280051874,
                       {"T": 1789280051874, "S": "Buy", "p": "77274.40", "v": "0.433", "i": "86f7e3ff"})]


def test_a_book_gap_asks_for_a_reconnect_and_only_reliable_books_are_sampled():
    w = FakeWriter()
    rec = Recorder(["BTCUSDT", "XAUUSDT"], w)
    rec.handle({"topic": "orderbook.50.BTCUSDT", "type": "snapshot", "data": SNAP})
    rec.sample(1000)
    assert [r[1] for r in w.rows] == ["BTCUSDT"], "у XAUUSDT снимка ещё нет — не пишем"
    assert w.rows[0][3]["b"][0] == ["77274.30", "2.988"] and w.rows[0][3]["u"] == SNAP["u"]
    assert rec.handle({"topic": "orderbook.50.BTCUSDT", "type": "delta", "data": {"b": [], "a": [], "u": SNAP["u"] + 5}}) is False
    w.rows.clear()
    rec.sample(2000)
    assert w.rows == [], "после разрыва стакан не пишется до нового снимка"
    before = rec.last_message
    assert rec.handle({"op": "subscribe", "success": True}) and rec.last_message == before, "ответ на подписку — не данные"


def test_topics_are_subscribed_in_chunks_of_ten():
    t = topics(["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "BNBUSDT", "XAUUSDT"])
    assert t[:2] == ["orderbook.50.BTCUSDT", "publicTrade.BTCUSDT"] and len(t) == 14
    assert [len(c) for c in chunks(t)] == [10, 4]


def test_health_is_the_age_of_the_last_exchange_message(tmp_path):
    (tmp_path / "heartbeat").write_text("1000\n", encoding="utf-8")
    assert health.check(tmp_path, now=1100)
    assert not health.check(tmp_path, now=1200)
    assert not health.check(tmp_path / "missing", now=1000)
