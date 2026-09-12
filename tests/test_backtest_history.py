"""
Загрузчик истории Bybit (Ф1 плана трейдера): подменная биржа отдаёт синтетический ряд
так, как это делает Bybit — от новых к старым, не больше limit, вперёд от start.
"""
from datetime import UTC, datetime

import pytest

from backtest import history

STEP = 300_000  # 5m
T0 = 1_725_000_000_000


class FakeBybit:
    """Свечи 5m от T0 до now_ms (последняя незакрытая), фандинг раз в 8 ч, OI по курсору."""

    def __init__(self, count, rate_limited_first=0, listing_ms=T0, funding_hours=8):
        self.funding_step = funding_hours * 3_600_000
        self.series = [listing_ms + i * STEP for i in range(count)]
        self.calls = []
        self.rate_limited = rate_limited_first

    def get(self, url, params, timeout):
        self.calls.append((url.replace(history.BASE_URL, ""), dict(params)))
        if self.rate_limited:
            self.rate_limited -= 1
            return Response({"retCode": 10006, "retMsg": "Too many visits"})
        path = url.replace(history.BASE_URL, "")
        if path == "/v5/market/kline":
            rows = [t for t in self.series if t >= params["start"]][:params["limit"]]
            return Response(ok({"list": [[str(t), "1", "2", "0.5", "1.5", "10", "15"] for t in reversed(rows)]}))
        if path == "/v5/market/funding/history":
            stamps = range(T0, T0 + 90 * 86_400_000, self.funding_step)
            rows = [t for t in stamps if params.get("startTime", 0) <= t <= params["endTime"]][-params["limit"]:]
            return Response(ok({"list": [{"fundingRateTimestamp": str(t), "fundingRate": "0.0001"}
                                         for t in reversed(rows)]}))
        if path == "/v5/market/open-interest":
            stamps = [T0 + i * STEP for i in range(6) if params["startTime"] <= T0 + i * STEP <= params["endTime"]]
            page = int(params.get("cursor") or 0)
            rows = [{"timestamp": str(t), "openInterest": "100"} for t in stamps[page * 2:page * 2 + 2]]
            return Response(ok({"list": rows, "nextPageCursor": str(page + 1) if (page + 1) * 2 < len(stamps) else ""}))
        raise AssertionError(path)


class Response:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def ok(result):
    return {"retCode": 0, "retMsg": "OK", "result": result}


def api_over(fake):
    return history.BybitHistory(session=fake, sleep=lambda s: None, clock=lambda: 0.0)


@pytest.fixture
def conn(tmp_path):
    connection = history.connect(tmp_path / "history.db")
    yield connection
    connection.close()


def test_candles_are_paged_forward_stored_ascending_and_only_closed(conn, monkeypatch):
    monkeypatch.setattr(history, "KLINE_LIMIT", 100)
    fake = FakeBybit(count=250)
    end_ms = T0 + 249 * STEP + STEP // 2  # последняя свеча ещё не закрыта
    assert history.sync_candles(conn, api_over(fake), "ADAUSDT", "5m", T0, end_ms) == 249
    stamps = [row[0] for row in history.load_candles(conn, "ADAUSDT", "5m", 0, end_ms)]
    assert stamps == sorted(stamps) and stamps[0] == T0 and stamps[-1] == T0 + 248 * STEP
    assert history.find_gaps(conn, "ADAUSDT", "5m") == []


def test_a_second_sync_only_fetches_what_is_missing(conn, monkeypatch):
    monkeypatch.setattr(history, "KLINE_LIMIT", 100)
    fake = FakeBybit(count=250)
    end_ms = T0 + 250 * STEP
    history.sync_candles(conn, api_over(fake), "ADAUSDT", "5m", T0, end_ms)
    fake.calls.clear()
    assert history.sync_candles(conn, api_over(fake), "ADAUSDT", "5m", T0, end_ms) == 0
    assert all(params["start"] >= T0 + 250 * STEP for _, params in fake.calls)


def test_a_rate_limit_is_retried(conn):
    fake = FakeBybit(count=10, rate_limited_first=2)
    assert history.sync_candles(conn, api_over(fake), "ADAUSDT", "5m", T0, T0 + 10 * STEP) == 10
    assert len(fake.calls) >= 3


def test_an_api_error_is_not_swallowed(conn):
    class Broken(FakeBybit):
        def get(self, url, params, timeout):
            return Response({"retCode": 10001, "retMsg": "params error"})
    with pytest.raises(RuntimeError, match="10001"):
        history.sync_candles(conn, api_over(Broken(count=1)), "ADAUSDT", "5m", T0, T0 + STEP)


def test_a_symbol_listed_later_starts_from_its_listing(conn):
    fake = FakeBybit(count=5, listing_ms=T0 + 100 * STEP)
    assert history.sync_candles(conn, api_over(fake), "NEWUSDT", "5m", T0, T0 + 105 * STEP) == 5


def test_funding_is_complete_and_a_second_sync_adds_nothing(conn):
    fake = FakeBybit(count=0)
    end_ms = T0 + 90 * 86_400_000
    assert history.sync_funding(conn, api_over(fake), "ADAUSDT", T0, end_ms) == 270, "90 суток по 3 ставки"
    assert history.sync_funding(conn, api_over(fake), "ADAUSDT", T0, end_ms) == 0


@pytest.mark.parametrize("hours", [4, 1])
def test_funding_with_a_short_interval_has_no_holes(conn, hours):
    # биржа отдаёт последние 200 записей: окно «под 8 ч» теряло начало каждого окна
    fake = FakeBybit(count=0, funding_hours=hours)
    end_ms = T0 + 90 * 86_400_000
    assert history.sync_funding(conn, api_over(fake), "ADAUSDT", T0, end_ms) == 90 * 24 // hours
    stamps = [r[0] for r in conn.execute("SELECT ts FROM funding ORDER BY ts")]
    assert stamps[0] == T0 and all(b - a == hours * 3_600_000 for a, b in zip(stamps, stamps[1:]))


def test_a_hole_in_cached_funding_is_refilled(conn):
    fake = FakeBybit(count=0, funding_hours=4)
    end_ms = T0 + 90 * 86_400_000
    history.sync_funding(conn, api_over(fake), "ADAUSDT", T0, end_ms)
    conn.execute("DELETE FROM funding WHERE ts BETWEEN ? AND ?", (T0 + 10 * 86_400_000, T0 + 20 * 86_400_000))
    assert history.sync_funding(conn, api_over(fake), "ADAUSDT", T0, end_ms) == 61


def test_open_interest_follows_the_cursor(conn):
    fake = FakeBybit(count=0)
    assert history.sync_open_interest(conn, api_over(fake), "ADAUSDT", "5min", T0, T0 + 10 * STEP) == 6


def test_a_longer_period_backfills_candles_before_the_cache(conn, monkeypatch):
    monkeypatch.setattr(history, "KLINE_LIMIT", 100)
    fake = FakeBybit(count=250)
    end_ms = T0 + 250 * STEP
    assert history.sync_candles(conn, api_over(fake), "ADAUSDT", "5m", T0 + 100 * STEP, end_ms) == 150
    assert history.sync_candles(conn, api_over(fake), "ADAUSDT", "5m", T0, end_ms) == 100, "начало докачано назад"
    stamps = [row[0] for row in history.load_candles(conn, "ADAUSDT", "5m", 0, end_ms)]
    assert stamps[0] == T0 and len(stamps) == 250 and history.find_gaps(conn, "ADAUSDT", "5m") == []
    assert history.sync_candles(conn, api_over(fake), "ADAUSDT", "5m", T0, end_ms) == 0


def test_a_longer_period_backfills_open_interest_before_the_cache(conn):
    fake = FakeBybit(count=0)
    assert history.sync_open_interest(conn, api_over(fake), "ADAUSDT", "5min", T0 + 3 * STEP, T0 + 10 * STEP) == 3
    assert history.sync_open_interest(conn, api_over(fake), "ADAUSDT", "5min", T0, T0 + 10 * STEP) == 3
    stamps = [r[0] for r in conn.execute("SELECT ts FROM open_interest ORDER BY ts")]
    assert stamps == [T0 + i * STEP for i in range(6)]


def test_gaps_are_reported(conn):
    conn.executemany("INSERT INTO candles VALUES ('X', '5m', ?, 1, 1, 1, 1, 1, 1)",
                     [(T0,), (T0 + STEP,), (T0 + 4 * STEP,)])
    assert history.find_gaps(conn, "X", "5m") == [(T0 + STEP, T0 + 4 * STEP)]


def test_the_window_is_months_back_from_now(conn, monkeypatch):
    seen = []
    monkeypatch.setattr(history, "sync_candles", lambda c, a, s, tf, start, end: seen.append((s, tf, start, end)) or 0)
    monkeypatch.setattr(history, "sync_funding", lambda *a: 0)
    now = datetime(2026, 9, 11, tzinfo=UTC)
    history.sync(conn, api_over(FakeBybit(count=0)), ["ADAUSDT"], ["5m", "1h"], months=12, now=now)
    assert [tf for _, tf, _, _ in seen] == ["5m", "1h"]
    assert seen[0][3] == int(now.timestamp() * 1000)
    assert (seen[0][3] - seen[0][2]) == 360 * 86_400_000


def test_the_cache_is_outside_git():
    import pathlib
    gitignore = (pathlib.Path(history.__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert "*.db" in gitignore.splitlines()
