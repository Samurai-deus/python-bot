"""
Режим торговли: один источник истины на весь путь до биржи.

Регрессия, которую закрывают эти тесты (аудит 10.09.2026, блокер B-2):
одни и те же переменные окружения разбирались тремя разными способами.

    trading_mode.py                value.lower() in ("true","1","yes","on")
    exchange/bybit_client.py:109   value.lower() == "true"
    execution/order_executor.py:27 value.lower() == "true"

При BYBIT_TESTNET=1 это давало режим TESTNET в резолвере, пометку [TESTNET]
в сообщении Telegram — и базовый URL https://api.bybit.com в клиенте, потому что
строка "1" не равна строке "true". Реальный ордер на mainnet под видом теста.

Тесты ниже проверяют не «функция возвращает enum», а согласованность трёх ответов
для каждой комбинации флагов: какой режим, уходит ли ордер, на какой хост.
"""
import importlib

import pytest

import trading_mode
from trading_mode import TradingMode
from utils.env import env_flag


MODE_VARS = ("LIVE_TRADING", "PAPER_TRADING", "BYBIT_TESTNET", "DRY_RUN")


@pytest.fixture(autouse=True)
def clean_mode_env(monkeypatch):
    """Каждый тест стартует с пустым окружением по флагам режима."""
    for name in MODE_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


def _set(monkeypatch, **flags):
    for name, value in flags.items():
        monkeypatch.setenv(name, value)


# ---------------------------------------------------------------------------
# Разбор булевых значений
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["true", "TRUE", "True", "1", "yes", "on", "y", "t", " true "])
def test_env_flag_truthy_forms(monkeypatch, raw):
    monkeypatch.setenv("SOME_FLAG", raw)
    assert env_flag("SOME_FLAG") is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "0", "no", "off", "n", "f", " false "])
def test_env_flag_falsy_forms(monkeypatch, raw):
    monkeypatch.setenv("SOME_FLAG", raw)
    assert env_flag("SOME_FLAG") is False


def test_env_flag_unset_returns_default(monkeypatch):
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert env_flag("SOME_FLAG", default=False) is False
    assert env_flag("SOME_FLAG", default=True) is True


def test_env_flag_empty_value_is_treated_as_unset(monkeypatch):
    """
    `DRY_RUN=` в .env — это «объявили, значение не задали». Молча считать это
    ложью опасно: пустой LIVE_TRADING= означал бы не то же, что пустой DRY_RUN=.
    """
    monkeypatch.setenv("SOME_FLAG", "")
    assert env_flag("SOME_FLAG", default=True) is True
    assert env_flag("SOME_FLAG", default=False) is False


def test_env_flag_garbage_raises_instead_of_defaulting_to_false(monkeypatch):
    """
    Опечатка не должна тихо приводиться к False: `LIVE_TRADING=yse` не имеет
    права означать «не live» так же, как `LIVE_TRADING=no`.
    """
    monkeypatch.setenv("SOME_FLAG", "yse")
    with pytest.raises(ValueError, match="SOME_FLAG"):
        env_flag("SOME_FLAG")


# ---------------------------------------------------------------------------
# Резолвер режима
# ---------------------------------------------------------------------------

def test_empty_env_is_dry_run(monkeypatch):
    """Безопасный дефолт: без единого флага ордера не уходят никуда."""
    assert trading_mode.get_trading_mode() == TradingMode.DRY_RUN
    assert trading_mode.sends_real_orders() is False


@pytest.mark.parametrize("truthy", ["true", "1", "yes", "on"])
def test_live_wins_over_everything(monkeypatch, truthy):
    _set(monkeypatch, LIVE_TRADING=truthy, PAPER_TRADING="true", BYBIT_TESTNET="true", DRY_RUN="true")
    assert trading_mode.get_trading_mode() == TradingMode.LIVE


@pytest.mark.parametrize("truthy", ["true", "1", "yes", "on"])
def test_paper_wins_over_testnet(monkeypatch, truthy):
    _set(monkeypatch, PAPER_TRADING=truthy, BYBIT_TESTNET="true")
    assert trading_mode.get_trading_mode() == TradingMode.PAPER_TRADING
    assert trading_mode.sends_real_orders() is False


@pytest.mark.parametrize("truthy", ["true", "1", "yes", "on"])
def test_testnet_requires_dry_run_off(monkeypatch, truthy):
    _set(monkeypatch, BYBIT_TESTNET=truthy, DRY_RUN="false")
    assert trading_mode.get_trading_mode() == TradingMode.TESTNET


@pytest.mark.parametrize("truthy", ["true", "1", "yes", "on"])
def test_dry_run_blocks_testnet(monkeypatch, truthy):
    _set(monkeypatch, BYBIT_TESTNET="true", DRY_RUN=truthy)
    assert trading_mode.get_trading_mode() == TradingMode.DRY_RUN
    assert trading_mode.sends_real_orders() is False


# ---------------------------------------------------------------------------
# Согласованность трёх ответов — собственно регрессия B-2
# ---------------------------------------------------------------------------

# (флаги окружения, ожидаемый режим, уходит ли ордер, testnet-хост)
CONSISTENCY_TABLE = [
    ({},                                              TradingMode.DRY_RUN,       False, False),
    ({"DRY_RUN": "true"},                             TradingMode.DRY_RUN,       False, False),
    ({"DRY_RUN": "1"},                                TradingMode.DRY_RUN,       False, False),
    ({"PAPER_TRADING": "true"},                       TradingMode.PAPER_TRADING, False, False),
    ({"PAPER_TRADING": "1"},                          TradingMode.PAPER_TRADING, False, False),
    ({"BYBIT_TESTNET": "true", "DRY_RUN": "false"},   TradingMode.TESTNET,       True,  True),
    ({"BYBIT_TESTNET": "1", "DRY_RUN": "0"},          TradingMode.TESTNET,       True,  True),
    ({"BYBIT_TESTNET": "yes", "DRY_RUN": "no"},       TradingMode.TESTNET,       True,  True),
    ({"BYBIT_TESTNET": "on", "DRY_RUN": "off"},       TradingMode.TESTNET,       True,  True),
    ({"LIVE_TRADING": "true"},                        TradingMode.LIVE,          True,  False),
    ({"LIVE_TRADING": "1"},                           TradingMode.LIVE,          True,  False),
]


@pytest.mark.parametrize("flags,mode,real_orders,testnet_host", CONSISTENCY_TABLE)
def test_mode_order_and_host_agree(monkeypatch, flags, mode, real_orders, testnet_host):
    _set(monkeypatch, **flags)
    assert trading_mode.get_trading_mode() == mode
    assert trading_mode.sends_real_orders() is real_orders
    assert trading_mode.uses_testnet_endpoint() is testnet_host


@pytest.mark.parametrize("flags,mode,real_orders,testnet_host", CONSISTENCY_TABLE)
def test_executor_agrees_with_resolver(monkeypatch, flags, mode, real_orders, testnet_host):
    """
    Исполнитель обязан молчать ровно тогда, когда резолвер говорит «ордера не идут».
    До правки `DRY_RUN=1` давал здесь боевой режим: собственный разбор
    `os.environ.get("DRY_RUN","true").lower()=="true"` не признавал "1" за истину.
    """
    from execution import order_executor
    _set(monkeypatch, **flags)
    assert order_executor._is_dry_run() is (not real_orders)


@pytest.mark.parametrize("flags,mode,real_orders,testnet_host", CONSISTENCY_TABLE)
def test_client_host_agrees_with_resolver(monkeypatch, flags, mode, real_orders, testnet_host):
    """
    Хост биржи обязан соответствовать режиму. Именно здесь жил блокер:
    BYBIT_TESTNET=1 → режим TESTNET, а базовый URL — боевой api.bybit.com.
    """
    from exchange import bybit_client
    _set(monkeypatch, **flags)
    client = bybit_client.BybitClient(api_key="k", api_secret="s")
    expected = bybit_client._TESTNET_BASE if testnet_host else bybit_client._MAINNET_BASE
    assert client._base_url == expected, (
        f"режим {mode.value}: ожидался хост {expected}, получен {client._base_url}"
    )


def test_no_module_reads_mode_flags_directly():
    """
    Гейт «единый парсер»: кроме trading_mode.py никто не читает флаги режима
    из окружения напрямую. Дублирующий разбор — это ровно тот способ, которым
    режим разъезжается между слоями.
    """
    import io
    import pathlib
    import tokenize

    root = pathlib.Path(__file__).resolve().parent.parent
    flags = {"LIVE_TRADING", "PAPER_TRADING", "BYBIT_TESTNET", "DRY_RUN"}
    allowed = {root / "trading_mode.py", root / "utils" / "env.py"}
    skip_dirs = {"venv", ".venv", "archive", "node_modules", ".git", "miniapp"}

    def reads_flag_in_code(path):
        """
        Ищем по токенам, а не по тексту: иначе гейт ловит собственные комментарии,
        объясняющие, как было раньше. Комментарии и строковые литералы отбрасываем,
        остаётся настоящий код.
        """
        try:
            source = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            return []
        try:
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except (tokenize.TokenError, IndentationError, SyntaxError):
            return []

        code = [t for t in tokens if t.type not in (tokenize.COMMENT, tokenize.NL)]
        hits = []
        for i, tok in enumerate(code):
            # Ищем связку: имя environ/getenv ... затем строковый литерал с именем флага
            if tok.type != tokenize.NAME or tok.string not in ("environ", "getenv"):
                continue
            for nxt in code[i + 1: i + 5]:
                if nxt.type != tokenize.STRING:
                    continue
                literal = nxt.string.strip("\"'")
                if literal in flags:
                    hits.append((tok.start[0], literal))
                    break
        return hits

    offenders = []
    for path in root.rglob("*.py"):
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.resolve() in allowed:
            continue
        for lineno, flag in reads_flag_in_code(path):
            offenders.append(f"{path.relative_to(root)}:{lineno}: читает {flag} напрямую")

    assert not offenders, (
        "Флаги режима читаются мимо trading_mode.py:\n  " + "\n  ".join(offenders)
    )
