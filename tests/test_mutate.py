"""
tools.mutate: замена только при ровно одном вхождении (аудит 29.09.2026: проверка была выключена
строкой `if False:`, и мутант, не нашедший места или попавший в несколько, считался применённым).
"""
from tools import mutate


def test_replaces_exactly_one_occurrence(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("a = 1\nb = 2\n", encoding="utf-8")
    assert mutate.apply(str(f), "a = 1", "a = 9") == 0
    assert f.read_text(encoding="utf-8") == "a = 9\nb = 2\n"


def test_refuses_missing_or_ambiguous_occurrence(tmp_path):
    f = tmp_path / "x.py"
    f.write_text("x = 1\nx = 1\n", encoding="utf-8")
    assert mutate.apply(str(f), "x = 1", "x = 2") == 2, "два вхождения — отказ"
    assert mutate.apply(str(f), "y = 1", "y = 2") == 2, "ни одного — отказ"
    assert f.read_text(encoding="utf-8") == "x = 1\nx = 1\n", "файл не тронут"
    assert mutate.main(["--check", str(f), "x = 1"]) == 2
