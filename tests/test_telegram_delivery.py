"""
Метка доставки heartbeat в Telegram: её читает сторож хоста (deploy/watchdog.sh) и пишет владельцу сам, если
бот жив, но давно ничего не доставил.

19.10–09.11.2026 бот три недели не отправил ни одного сообщения: send_message_async глотал «Pool timeout» и
возвращал None, а heartbeat всё равно ставил свою метку. Здесь проверяется, что метка — только про доставку.
"""
import asyncio
from types import SimpleNamespace

import pytest

import health_monitor
import telegram_bot


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "db" / "market_bot.db"))
    monkeypatch.setattr(health_monitor, "LAST_HEARTBEAT_FILE", str(tmp_path / "last_heartbeat.txt"))
    return tmp_path


def send_returning(result):
    sent = []

    async def fake(text, parse_mode="Markdown"):
        sent.append(text)
        return result

    return fake, sent


def test_mark_lives_next_to_the_database(data_dir):
    """На проде DB_PATH=/data/db/market_bot.db — метка в томе /data, где её находит сторож."""
    assert health_monitor.delivery_mark_path() == data_dir / "db" / "telegram_delivered"


def test_delivered_heartbeat_writes_the_mark(data_dir, monkeypatch, caplog):
    fake, sent = send_returning(SimpleNamespace(message_id=77))
    monkeypatch.setattr(telegram_bot, "send_message_async", fake)
    caplog.set_level("INFO", logger="health_monitor")

    asyncio.run(health_monitor.send_heartbeat_async())

    assert sent, "heartbeat отправлялся"
    stamp = (data_dir / "db" / "telegram_delivered").read_text(encoding="ascii")
    assert stamp.isdigit(), "целое unix-время — его читает sh без плавающей точки"
    assert "heartbeat доставлен (message_id=77)" in caplog.text, "успех виден в логе на уровне INFO"


def test_undelivered_heartbeat_leaves_the_mark_alone(data_dir, monkeypatch, caplog):
    health_monitor.record_delivery(now=1_000)
    fake, sent = send_returning(None)  # так send_message_async сообщает о неудаче
    monkeypatch.setattr(telegram_bot, "send_message_async", fake)

    asyncio.run(health_monitor.send_heartbeat_async())

    assert sent
    assert (data_dir / "db" / "telegram_delivered").read_text(encoding="ascii") == "1000"
    assert not (data_dir / "last_heartbeat.txt").exists(), "/status не показывает «активен» без доставки"
    assert "heartbeat не доставлен" in caplog.text


def test_unwritable_mark_does_not_break_the_bot(tmp_path, monkeypatch, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="ascii")
    monkeypatch.setenv("DB_PATH", str(blocker / "market_bot.db"))  # каталог базы — обычный файл
    health_monitor.record_delivery()
    assert "не удалось записать метку доставки" in caplog.text
