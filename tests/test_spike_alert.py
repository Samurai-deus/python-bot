"""
Сообщение о резком движении: отправка видна в журнале (23.09.2026 отправка не писала ни строки, и
«за 7 дней ни одного» нельзя было проверить) и не повторяется чаще, чем раз в ALERT_COOLDOWN по символу.
"""
import logging

import spike_alert


def analysis(pct=3.0, tf="15m"):
    return {"has_spike": True, "spike_direction": "UP", "spike_pct": pct, "timeframe": tf,
            "has_visible_reason": False, "possible_reasons": [], "should_alert": True}


def test_sent_alert_is_logged(monkeypatch, caplog):
    sent = []
    monkeypatch.setattr(spike_alert, "send_message", sent.append)
    monkeypatch.setattr(spike_alert, "_last_alerts", {})
    with caplog.at_level(logging.INFO, logger="spike_alert"):
        spike_alert.send_spike_alert("SOLUSDT", analysis())
    assert len(sent) == 1 and "SOLUSDT" in sent[0]
    assert "Резкое движение отправлено" in caplog.text and "SOLUSDT" in caplog.text and "3.00" in caplog.text


def test_same_symbol_is_not_repeated_within_cooldown(monkeypatch):
    sent = []
    monkeypatch.setattr(spike_alert, "send_message", sent.append)
    monkeypatch.setattr(spike_alert, "_last_alerts", {})
    spike_alert.send_spike_alert("SOLUSDT", analysis())
    spike_alert.send_spike_alert("SOLUSDT", analysis(pct=4.0))
    assert len(sent) == 1, "тот же символ и таймфрейм — не чаще раза в окно"
    spike_alert.send_spike_alert("SOLUSDT", analysis(pct=4.0, tf="30m"))
    assert len(sent) == 2, "другой таймфрейм — отдельное окно"


def test_alert_threshold_and_cooldown_come_from_the_environment(monkeypatch):
    """Пороги и окно повтора — настройки, а не числа в коде (23.09.2026)."""
    sent = []
    monkeypatch.setattr(spike_alert, "send_message", sent.append)
    monkeypatch.setattr(spike_alert, "_last_alerts", {})
    monkeypatch.setenv("SPIKE_ALERT_COOLDOWN_SEC", "0")
    spike_alert.send_spike_alert("SOLUSDT", analysis())
    spike_alert.send_spike_alert("SOLUSDT", analysis())
    assert len(sent) == 2, "окно 0 — повтор разрешён"
    monkeypatch.setenv("SPIKE_THRESHOLD_PCT", "4.0")
    monkeypatch.setenv("SPIKE_ALERT_MIN_PCT", "5.0")
    assert spike_alert.spike_threshold_pct() == 4.0 and spike_alert.spike_alert_min_pct() == 5.0
    candles = [[0, 100.0, 100.5, 99.5, 100.0, 10.0] for _ in range(30)]
    candles.append([0, 100.0, 103.0, 100.0, 103.0, 10.0])          # движение 3 % — ниже нового порога
    assert spike_alert.analyze_spike_with_context("SOLUSDT", candles) is None
