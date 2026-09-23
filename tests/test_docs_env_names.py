"""
Гигиена документации: каждая переменная окружения, названная в документации, есть в коде или скриптах.
23.09.2026 в README попала ANALYSIS_INTERVAL, которой нет: интервал анализа читается из BOT_INTERVAL —
правка «интервала» на проде ничего не изменила, и это заметили только по логам.
"""
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ("README.md", "docs/TRADER_PLAN.md", "docs/TECH_DEBT.md", "deploy/README.md", "miniapp/README.md")
# Имена в документации, которых в коде и не должно быть: чужие продукты и переменные стороннего окружения.
ALLOWED_OUTSIDE = {"PATH", "HOME", "CI", "DEPLOY_HOST"}
NAME = re.compile(r"`([A-Z][A-Z0-9_]{3,})`")


def haystack() -> str:
    parts = []
    for p in ROOT.rglob("*.py"):
        if "venv" in p.parts or p.name.startswith("test_docs_env_names"):
            continue
        parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    for pattern in ("deploy/*.sh", "*.yml", "deploy/*.yml", "miniapp/*.json", "miniapp/*.ts"):
        for p in ROOT.glob(pattern):
            parts.append(p.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def code() -> str:
    return haystack()


@pytest.mark.parametrize("rel", DOCS)
def test_env_names_in_docs_exist_in_code(rel, code):
    path = ROOT / rel
    if not path.exists():
        pytest.skip(f"{rel} нет в репозитории")
    missing = sorted({m.group(1) for m in NAME.finditer(path.read_text(encoding="utf-8"))
                      if m.group(1) not in code and m.group(1) not in ALLOWED_OUTSIDE})
    assert missing == [], f"{rel}: названы, но в коде не встречаются: {missing}"
