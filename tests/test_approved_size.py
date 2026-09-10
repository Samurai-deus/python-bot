"""
Бумажная сделка открывается по размеру, одобренному гейткипером (10.09.2026).

Гейткипер урезает размер в своей копии сигнала (ALLOW_LIMITED ×0,5, портфель,
PositionSizer, finalize_position_size), а бумажную сделку открывает
signal_generator по своему словарю. До исправления сделка открывалась по
исходному размеру — больше одобренного и показанного владельцу в сообщении.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def method_source(path, name):
    text = (ROOT / path).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"{path}: нет функции {name}")


def test_gatekeeper_publishes_the_final_size_just_before_approval():
    src = method_source("execution/gatekeeper.py", "send_signal")
    publish = 'caller_signal_data["approved_position_size"] = signal_data.get("position_size")'
    assert publish in src
    assert src.index("finalize_position_size(") < src.index(publish), "после всех урезаний размера"
    assert src.index(publish) < src.rindex("return True"), "только на пути одобрения"
    assert src.index('caller_signal_data.pop("approved_position_size", None)') < src.index("dict(signal_data)"), \
        "одобрение прошлого вызова не должно пережить отказ в этом"


def test_paper_trade_uses_only_the_approved_size():
    text = (ROOT / "signal_generator.py").read_text(encoding="utf-8")
    assert 'effective_pos_size = signal_data.get("approved_position_size")' in text
    assert 'signal_data.get("position_size", pos_size)' not in text, \
        "исходный размер не прошёл урезаний риска"
