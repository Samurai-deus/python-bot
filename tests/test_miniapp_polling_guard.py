"""
Каждый запрос Mini App выключается, когда сессия истекла (5.2, 10.09.2026).

После ответа 401 фронт ставит флаг authExpired и показывает баннер «откройте
снова из Telegram». Проверка на проде показала: аналитика замолчала, а
здоровье системы продолжало опрашиваться каждые 30 с — у главного экрана был
свой useQuery в обход хуков. Этот тест не даёт новому запросу снова обойти флаг.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "miniapp" / "src"


def _query_blocks(text):
    """Тела вызовов useQuery({...}) — по балансу фигурных скобок."""
    for match in re.finditer(r"useQuery\(\{", text):
        depth, i = 0, match.end() - 1
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    yield text[match.start():i + 1]
                    break
            i += 1


def test_every_query_is_switched_off_by_auth_expiry():
    offenders = []
    blocks = 0
    for path in SRC.rglob("*.ts*"):
        if ".test." in path.name:
            continue
        text = path.read_text(encoding="utf-8")
        for block in _query_blocks(text):
            blocks += 1
            if not re.search(r"enabled:\s*!authExpired", block):
                offenders.append(str(path.relative_to(ROOT)))
    assert blocks >= 8, "не нашёл запросов — тест смотрит не туда"
    assert offenders == [], f"запрос не выключается при истёкшей сессии: {offenders}"
