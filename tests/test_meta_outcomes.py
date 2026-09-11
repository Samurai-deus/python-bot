"""
Мета-мозг без исходов сделок (11.09.2026).

С 14:30 до 15:41 11.09 мета-мозг отказывал каждому сигналу «Recent outcomes show 7/10
negative results»: gatekeeper передавал ему 10 сделок за 7 дней — бумажные вперемешку с
биржевыми, без окна. Пока сделок нет, исходы не меняются, и блок держится днями. Серии
убытков сдерживает Risk Core (серия в событиях, пауза не дольше 60 минут).
"""
import pathlib

from brains.meta_decision_brain import MetaDecisionBrain

ROOT = pathlib.Path(__file__).resolve().parent.parent


def meta_call_block():
    source = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    return source[source.index("recent_outcomes = None"):source.index("recent_outcomes=recent_outcomes")]


def test_the_gatekeeper_gives_the_meta_brain_no_trade_outcomes():
    block = meta_call_block()
    assert "get_closed_trades" not in block and "recent_outcomes.append" not in block


def test_without_outcomes_the_meta_brain_does_not_block_on_them():
    result = MetaDecisionBrain().evaluate(recent_outcomes=None)
    assert "Recent outcomes" not in (result.reason or "")


def test_loss_streaks_are_still_held_by_the_risk_core():
    source = (ROOT / "execution" / "gatekeeper.py").read_text(encoding="utf-8")
    assert "loss_streak(" in source and "get_recent_closes(on_exchange=sends_real_orders())" in source
