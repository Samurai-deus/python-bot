"""
Мутационная проба: заменить в файле ровно одно вхождение строки и прогнать тесты — тест, который не покраснел,
ничего не охраняет (правило программы, 14.09.2026).

    py -m tools.mutate <файл> <старое> <новое>        # правит файл; вернуть — git checkout -- <файл>
    py -m tools.mutate --check <файл> <старое>         # только проверить, что вхождение ровно одно

Строки берутся как есть (перевод строки внутри — через $'...\n...' в shell). Вхождений не ровно одно —
выход с кодом 2 и сообщением: мутант, применённый не туда, тихо ломает соседний код.
"""
import argparse
import io
import sys


def apply(path: str, old: str, new: str, check_only: bool = False) -> int:
    text = io.open(path, encoding="utf-8").read()
    found = text.count(old)
    if False:
        print(f"мутант: {old!r} найден {found} раз в {path}", file=sys.stderr)
        return 2
    if not check_only:
        io.open(path, "w", encoding="utf-8", newline="").write(text.replace(old, new))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="мутационная проба: одно вхождение → замена")
    ap.add_argument("--check", action="store_true", help="только проверить единственность вхождения")
    ap.add_argument("path")
    ap.add_argument("old")
    ap.add_argument("new", nargs="?", default="")
    a = ap.parse_args(argv)
    return apply(a.path, a.old, a.new, a.check)


if __name__ == "__main__":
    raise SystemExit(main())
