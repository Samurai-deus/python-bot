"""
Гигиена кода и базы (6.3, 6.4 — 10.09.2026).

6.3: широкий except, в котором только pass, прячет сбой без следа — в логе
нечего искать, когда что-то «просто не работает». Такие места заменены логом.
6.4: трассы решений писались на каждый сигнал (~5 тыс. строк в сутки на проде),
а очистка существовала, но не вызывалась нигде.
"""
import ast
import pathlib
import sqlite3
from datetime import datetime, timedelta, UTC

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP = ("archive", "venv", "tests", "node_modules", ".git")
BROAD = {None, "Exception", "BaseException"}


def _python_sources():
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts and rel.parts[0] in SKIP:
            continue
        yield rel, path


def _handler_name(handler):
    if handler.type is None:
        return None
    if isinstance(handler.type, ast.Name):
        return handler.type.id
    return "other"


def test_no_broad_except_that_only_passes():
    offenders = []
    for rel, path in _python_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _handler_name(node) in BROAD:
                if len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
                    offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"широкий except без следа: {offenders}"


def _trace_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return [r[0] for r in conn.execute("SELECT symbol FROM decision_trace ORDER BY symbol")]
    finally:
        conn.close()


def test_old_decision_traces_are_pruned(tmp_path):
    from core.decision_trace import DecisionTrace, prune_decision_trace
    db_path = str(tmp_path / "trace.db")
    DecisionTrace(db_path)  # создаёт таблицу
    now = datetime.now(UTC)
    conn = sqlite3.connect(db_path)
    try:
        for symbol, when in (("OLDUSDT", now - timedelta(days=120)), ("NEWUSDT", now - timedelta(days=5))):
            conn.execute(
                "INSERT INTO decision_trace (timestamp, symbol, decision_source, allow_trading, block_level, reason, context_snapshot)"
                " VALUES (?, ?, 'Gatekeeper', 0, 'NONE', 'test', '{}')",
                (when.isoformat(), symbol),
            )
        conn.commit()
    finally:
        conn.close()

    assert prune_decision_trace(90, db_path=db_path) == 1
    assert _trace_rows(db_path) == ["NEWUSDT"]


def test_analysis_cycle_prunes_decision_traces():
    text = (ROOT / "loops" / "market_analysis.py").read_text(encoding="utf-8")
    node = next(n for n in ast.walk(ast.parse(text))
                if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_market_analysis")
    assert "await asyncio.to_thread(prune_decision_trace, 90)" in ast.get_source_segment(text, node)


def test_signals_is_the_module_not_a_shadowing_package():
    """
    До 11.09.2026 рядом с signals.py лежал пакет signals/, который через importlib
    загружал тот же signals.py второй копией под другим именем: подмена в тесте
    действовала только на одну из копий.
    """
    import pathlib
    import signals
    assert pathlib.Path(signals.__file__).name == "signals.py"
    assert callable(signals.build_signal)


# Корневые модули на 11.09.2026 — наследие. Новый код — в пакетах; модуль в корне
# добавляется (или вычёркивается после удаления) только правкой этого списка.
ROOT_MODULES = frozenset({
    "adaptive_rr",
    "bot_statistics",
    "candle_analysis",
    "capital",
    "chaos_engine",
    "config",
    "context_engine",
    "correlation_analysis",
    "daily_report",
    "data_loader",
    "database",
    "demo_trades",
    "error_alert",
    "health_monitor",
    "indicators",
    "journal",
    "leverage",
    "monitor_log",
    "paper_fills",
    "price_cache",
    "risk",
    "run_api",
    "runner",
    "scoring",
    "signal_generator",
    "signals",
    "spike_alert",
    "states",
    "system_state",
    "system_state_machine",
    "systemd_integration",
    "task_dump",
    "telegram_bot",
    "telegram_commands",
    "time_filter",
    "trade_manager",
    "trade_reporter",
    "trading_mode",
    "volatility_filter",
})


def test_no_new_modules_in_the_project_root():
    """Пункт 5 плана отложенного (docs/DEFERRED_PLAN.md): корень не растёт незаметно."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    actual = {path.stem for path in root.glob("*.py")}
    assert actual - ROOT_MODULES == set(), "новый модуль в корне — место ему в пакете"
    assert ROOT_MODULES - actual == set(), "модуль удалён — вычеркните его из ROOT_MODULES"
