"""
Каждая переменная окружения, которую читает код, описана в .env.example или задаётся compose (аудит
29.09.2026: 55 из 110 не были описаны нигде — среди них ключи субсчетов И13/И18 и все пределы Risk Core).
Обратная сторона — test_docs_env_names: имена из документации обязаны читаться кодом.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
READ = re.compile(r"""environ(?:\.get)?\(?\[?["']([A-Z][A-Z0-9_]{3,})["']|env_(?:flag|str|int|float)\(\s*["']([A-Z][A-Z0-9_]{3,})["']""")
# Переменные чужого окружения: их задаёт не владелец, а система / раннер.
EXTERNAL = {"HOME", "PATH", "TERM", "USER", "PYTEST_CURRENT_TEST"}


def read_by_code() -> dict:
    out: dict = {}
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT).as_posix()
        if rel.startswith(("venv/", "archive/", "tests/")):
            continue
        for m in READ.finditer(p.read_text(encoding="utf-8", errors="ignore")):
            out.setdefault(next(g for g in m.groups() if g), set()).add(rel)
    return out


def test_every_env_read_is_documented():
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]{3,})=", example, re.M))
    compose = (ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8")
    documented |= set(re.findall(r"^\s+([A-Z][A-Z0-9_]{3,}):", compose, re.M))
    missing = {k: sorted(v) for k, v in read_by_code().items() if k not in documented and k not in EXTERNAL}
    assert not missing, f"добавьте в .env.example (с умолчанием из кода): {missing}"
