"""
Каждый импорт проекта разрешается — включая импорты внутри функций (пункт 3 плана
отложенного, docs/DEFERRED_PLAN.md).

В коде много ленивых импортов в обработчиках сбоев (execution/gatekeeper.py,
execution/kill_switch.py и др.): ошибка в таком импорте проявляется только в той
ветке, где функция выполняется, — чаще всего в худший момент. Обычные тесты
этих веток не проходят. Здесь каждый `import x` и `from x import y` находится
разбором ast и проверяется: модуль x находится, имя y в нём есть.

Первый прогон 11.09.2026 нашёл core/drift_detector.py, который с момента
создания не импортировался: он брал из core.signal_snapshot_store класс
SignalSnapshotRecord, которого там никогда не было.

Не проверяются импорты, которые код сам сделал необязательными: внутри
`try/except ImportError` и под `if TYPE_CHECKING`. Модуль, объявленный в
requirements, но не установленный локально, ошибкой не считается: в CI
зависимости установлены, там проверка полная. Остальные исключения — только
списком ALLOWED с причиной.
"""
import ast
import importlib
import importlib.util
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKIP_DIRS = {"archive", "venv", ".venv", "tests", "miniapp", "node_modules", "__pycache__", ".git"}

# "модуль" или "модуль.имя" → причина. Пусто — хорошо.
ALLOWED = {}

_OPTIONAL_ERRORS = {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}


def _declared_requirements():
    names = set()
    for requirements in ("requirements.txt", "requirements-dev.txt"):
        path = ROOT / requirements
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line and not line.startswith("-"):
                name = re.split(r"[<>=!~;\[ ]", line, maxsplit=1)[0].lower().replace("-", "_")
                names.add(name.removesuffix("_binary"))
    return names


REQUIREMENTS = _declared_requirements()


def _declared(module: str) -> bool:
    return module.split(".")[0].lower() in REQUIREMENTS


def _spec_exists(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        # find_spec("os.x") бросает, если os — не пакет: подмодуля нет
        return False


def _has_name(module, name: str) -> bool:
    try:
        return hasattr(module, name)
    except Exception:
        # Модульный __getattr__ отдаёт имя лениво и может требовать окружения
        # (токен в telegram_bot): имя есть, окружения в тесте нет.
        return "__getattr__" in vars(module)


def project_files():
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if not SKIP_DIRS.intersection(rel.parts):
            yield path


def _module_name(path: pathlib.Path) -> str:
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_optional(node, parents) -> bool:
    """Импорт внутри try с обработчиком ImportError (или шире) либо под TYPE_CHECKING."""
    child = node
    parent = parents.get(child)
    while parent is not None:
        if isinstance(parent, ast.Try) and child in parent.body:
            for handler in parent.handlers:
                if handler.type is None:
                    return True
                names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
                if any(isinstance(n, ast.Name) and n.id in _OPTIONAL_ERRORS for n in names):
                    return True
        if isinstance(parent, ast.If) and "TYPE_CHECKING" in ast.unparse(parent.test):
            return True
        child, parent = parent, parents.get(parent)
    return False


def _absolute(module: str, level: int, current: str, is_package: bool) -> str:
    if level == 0:
        return module
    base = current.split(".") if is_package else current.split(".")[:-1]
    if level > 1:
        base = base[: len(base) - (level - 1)]
    return ".".join(base + ([module] if module else []))


def check_source(source: str, current: str = "snippet", is_package: bool = False):
    """Проблемные импорты исходника: [(строка, текст проблемы)]."""
    tree = ast.parse(source)
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    problems = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)) or _is_optional(node, parents):
            continue
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in ALLOWED or _spec_exists(alias.name) or _declared(alias.name):
                    continue
                problems.append((node.lineno, f"import {alias.name}: модуль не найден"))
            continue
        module = _absolute(node.module or "", node.level, current, is_package)
        if module in ALLOWED:
            continue
        try:
            target = importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if not _declared(module):
                problems.append((node.lineno, f"from {module}: {exc}"))
            continue
        except Exception as exc:
            problems.append((node.lineno, f"from {module}: {type(exc).__name__}: {exc}"))
            continue
        for alias in node.names:
            name = alias.name
            if name == "*" or f"{module}.{name}" in ALLOWED or _has_name(target, name):
                continue
            if not _spec_exists(f"{module}.{name}"):
                problems.append((node.lineno, f"from {module} import {name}: имени нет"))
    return problems


def test_the_checker_catches_a_broken_lazy_import():
    source = "def on_failure():\n    from no_such_module_for_test import helper\n    import another_missing_module\n"
    assert [line for line, _ in check_source(source)] == [2, 3]


def test_the_checker_catches_a_missing_name():
    assert check_source("def f():\n    from os import no_such_function_for_test\n")


def test_optional_imports_are_not_checked():
    source = (
        "try:\n    import no_such_optional_module\nexcept ImportError:\n    pass\n"
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import no_such_typing_module\n"
    )
    assert check_source(source) == []


@pytest.mark.parametrize("path", list(project_files()), ids=lambda p: str(p.relative_to(ROOT)))
def test_every_import_in_the_project_resolves(path):
    source = path.read_text(encoding="utf-8")
    problems = check_source(source, _module_name(path), path.name == "__init__.py")
    assert not problems, "\n".join(f"{path.relative_to(ROOT)}:{line} {text}" for line, text in problems)
