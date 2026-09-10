"""
Метки жизни и healthcheck бота. До 10.09.2026 healthcheck делал только SELECT 1:
бот с зависшим циклом событий или остановившимся циклом анализа считался
здоровым, и сторож хоста не узнал бы о зависании.
"""
import ast
import importlib.util
import os
import pathlib
import sqlite3
import subprocess
import sys
import time

import pytest

from utils import liveness

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEALTHCHECK = ROOT / "scripts" / "healthcheck.py"


def load_healthcheck():
    spec = importlib.util.spec_from_file_location("healthcheck_under_test", HEALTHCHECK)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def liveness_dir(tmp_path, monkeypatch):
    marks = tmp_path / "marks"
    monkeypatch.setenv("LIVENESS_DIR", str(marks))
    monkeypatch.delenv("BOT_INTERVAL", raising=False)
    monkeypatch.delenv("ADAPTIVE_INTERVAL_MAX", raising=False)
    return marks


# ---------------------------------------------------------------------------
# utils.liveness
# ---------------------------------------------------------------------------

def test_mark_then_age():
    liveness.mark("heartbeat", now=1000.0)
    assert liveness.age("heartbeat", now=1012.5) == pytest.approx(12.5)


def test_missing_or_garbage_mark_has_no_age(liveness_dir):
    assert liveness.age("heartbeat") is None
    liveness_dir.mkdir(parents=True)
    (liveness_dir / "heartbeat").write_text("мусор", encoding="utf-8")
    assert liveness.age("heartbeat") is None


def test_mark_failure_does_not_crash_the_bot(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("", encoding="ascii")
    monkeypatch.setenv("LIVENESS_DIR", str(blocker / "sub"))
    liveness.mark("heartbeat")  # каталог под файлом не создать — метки нет, исключения тоже
    assert liveness.age("heartbeat") is None


# ---------------------------------------------------------------------------
# scripts/healthcheck.py
# ---------------------------------------------------------------------------

def fresh_marks(now):
    liveness.mark("heartbeat", now=now - 5)
    liveness.mark("analysis", now=now - 60)


def test_fresh_marks_are_healthy():
    hc = load_healthcheck()
    now = time.time()
    fresh_marks(now)
    hc.check_liveness(now)


def test_stalled_event_loop_is_unhealthy():
    hc = load_healthcheck()
    now = time.time()
    fresh_marks(now)
    liveness.mark("heartbeat", now=now - hc.HEARTBEAT_MAX_AGE - 1)
    with pytest.raises(RuntimeError, match="heartbeat"):
        hc.check_liveness(now)


def test_stalled_analysis_loop_is_unhealthy():
    hc = load_healthcheck()
    now = time.time()
    fresh_marks(now)
    liveness.mark("analysis", now=now - hc.analysis_max_age() - 1)
    with pytest.raises(RuntimeError, match="анализа"):
        hc.check_liveness(now)


@pytest.mark.parametrize("present", ["heartbeat", "analysis"])
def test_missing_mark_is_unhealthy(present):
    hc = load_healthcheck()
    now = time.time()
    liveness.mark(present, now=now)
    with pytest.raises(RuntimeError, match="нет метки"):
        hc.check_liveness(now)


def test_analysis_limit_follows_the_longest_adaptive_interval(monkeypatch):
    hc = load_healthcheck()
    assert hc.analysis_max_age() == 2 * 900 + 120
    monkeypatch.setenv("ADAPTIVE_INTERVAL_MAX", "1800")
    assert hc.analysis_max_age() == 2 * 1800 + 120
    monkeypatch.setenv("BOT_INTERVAL", "3600")
    assert hc.analysis_max_age() == 2 * 3600 + 120


def run_healthcheck_process(tmp_path, liveness_dir):
    db = tmp_path / "bot.db"
    sqlite3.connect(db).close()
    env = dict(os.environ, DB_PATH=str(db), DATABASE_URL="", LIVENESS_DIR=str(liveness_dir))
    # запуск тем же способом, что в docker-compose: python scripts/healthcheck.py из корня
    return subprocess.run([sys.executable, "scripts/healthcheck.py"], cwd=ROOT, env=env,
                          capture_output=True, text=True, encoding="utf-8")


def test_healthcheck_process_exit_codes(tmp_path, liveness_dir):
    now = time.time()
    fresh_marks(now)
    ok = run_healthcheck_process(tmp_path, liveness_dir)
    assert ok.returncode == 0, ok.stderr

    liveness.mark("heartbeat", now=now - 3600)
    stale = run_healthcheck_process(tmp_path, liveness_dir)
    assert stale.returncode == 1
    assert "heartbeat" in stale.stderr, "причина обязана попасть в лог healthcheck — её пересказывает сторож"


# ---------------------------------------------------------------------------
# runner ставит метки там, где их ждёт healthcheck
# ---------------------------------------------------------------------------

def function_source(name):
    text = (ROOT / "runner.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"в runner.py нет async def {name}")


def test_runtime_heartbeat_marks_liveness():
    src = function_source("runtime_heartbeat_loop")
    assert 'liveness.mark("heartbeat")' in src


def test_analysis_loop_marks_on_start_and_after_each_turn():
    src = function_source("market_analysis_loop")
    assert src.count('liveness.mark("analysis")') >= 2
    assert src.index("increment_analysis_cycles()") < src.rindex('liveness.mark("analysis")'), \
        "метка оборота ставится после завершения цикла"
