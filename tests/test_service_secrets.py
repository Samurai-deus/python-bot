"""
Секреты по сервисам (аудит 29.09.2026, пакет 8). Раньше каждый контейнер получал весь .env: API, который смотрит в
интернет, держал ключи общего демо-счёта бота и И14 и ключи субсчетов И13 и И18. Карта deploy/service-secrets.conf
говорит, какие секреты видит сервис; deploy/split-env.sh собирает по ней файлы окружения на сервере.
"""
import pathlib
import re
import shutil
import subprocess

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAP = ROOT / "deploy" / "service-secrets.conf"
SECRET = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|PASSPHRASE|DSN)")
# Не имя переменной окружения: telegram_bot.TOKEN — старый атрибут модуля (PEP 562).
NOT_ENV = {"TOKEN"}


def secret_map() -> dict[str, set[str]]:
    out = {}
    for line in MAP.read_text(encoding="utf-8").splitlines():
        if re.match(r"^[a-z][a-z0-9_-]*:", line):
            svc, names = line.split(":", 1)
            out[svc] = set(names.split())
    return out


def is_secret(name: str) -> bool:
    return bool(SECRET.search(name)) or name == "REDIS_URL"


def code_secret_names() -> dict[str, set[str]]:
    """Имена-секреты из строковых литералов кода, который едет в образ: {имя: файлы}."""
    found: dict[str, set[str]] = {}
    for p in ROOT.rglob("*.py"):
        rel = p.relative_to(ROOT)
        if rel.parts[0] in {"tests", "venv", ".venv", "node_modules", "miniapp"}:
            continue
        for name in re.findall(r"""["']([A-Z][A-Z0-9_]{2,})["']""", p.read_text(encoding="utf-8", errors="replace")):
            if is_secret(name) and name not in NOT_ENV:
                found.setdefault(name, set()).add(str(rel))
    return found


def compose_services_with_env_file() -> set[str]:
    compose = yaml.safe_load((ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    return {name for name, svc in compose["services"].items() if svc.get("env_file")}


def test_every_service_with_an_env_file_is_in_the_map():
    assert set(secret_map()) == compose_services_with_env_file()


def test_each_service_reads_only_its_own_env_file():
    """Пакет 8б: общий .env не читает ни один контейнер — только свой файл из split-env.sh."""
    compose = yaml.safe_load((ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    for name in secret_map():
        assert compose["services"][name]["env_file"] == [f"/opt/market-bot/env/{name}.env"], name


def test_smoke_checks_the_api_container_itself():
    text = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    smoke = re.search(r"^step_smoke\(\) \{\n(.*?)^\}", text, re.M | re.S).group(1)
    assert "docker exec market-bot-api sh -c 'test -z \"${BYBIT_API_KEY:-}${BYBIT_API_SECRET:-}${ENCRYPTION_KEY:-}\"'" in smoke


def test_deploy_recreates_exactly_the_mapped_services():
    text = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    services = re.search(r'^ENV_SERVICES="([^"]+)"', text, re.M).group(1).split()
    assert set(services) == set(secret_map())


def test_every_secret_the_code_reads_is_given_to_some_service():
    """Новый секрет в коде без строки в карте на проде молча пропал бы из всех контейнеров."""
    granted = set().union(*secret_map().values())
    missing = {name: sorted(files) for name, files in code_secret_names().items() if name not in granted}
    assert not missing, f"секреты без сервиса в deploy/service-secrets.conf: {missing}"


def test_the_map_names_only_secrets():
    """Несекретные строки общего .env получают все — в карте им не место (иначе кажется, что их можно отнять)."""
    for svc, names in secret_map().items():
        assert all(is_secret(n) for n in names), (svc, sorted(n for n in names if not is_secret(n)))


# (сервис, секрет, файл, которым сервис его использует, признак использования в этом файле)
USES = [
    ("bot", "OPENROUTER_API_KEY", "ai_trader/client.py", '"OPENROUTER_API_KEY"'),
    ("bot", "BYBIT_API_KEY", "exchange/bybit_client.py", '"BYBIT_API_KEY"'),
    ("bot", "ENCRYPTION_KEY", "exchange/bybit_client.py", "from utils.crypto import decrypt"),
    ("news", "OPENROUTER_API_KEY", "news/scorer.py", "from ai_trader import client"),
    ("api", "TELEGRAM_BOT_TOKEN", "api/deps.py", '"TELEGRAM_BOT_TOKEN"'),
    ("api", "REDIS_URL", "api/sessions.py", '"REDIS_URL"'),
    ("api", "SENTRY_DSN", "api/main.py", '"SENTRY_DSN"'),
    ("portfolio", "BYBIT_API_KEY", "portfolio/client.py", '"BYBIT_API_KEY"'),
    ("btcalts", "BTCALTS_BYBIT_API_KEY", "btcalts/client.py", '"BTCALTS_BYBIT_API_KEY"'),
    ("carry", "CARRY_BYBIT_API_KEY", "carry/client.py", '"CARRY_BYBIT_API_KEY"'),
]


@pytest.mark.parametrize("svc,name,path,marker", USES)
def test_each_consumer_keeps_the_secret_it_uses(svc, name, path, marker):
    assert marker in (ROOT / path).read_text(encoding="utf-8"), f"{path} больше не использует {name} — обновить карту"
    assert name in secret_map()[svc]


@pytest.mark.parametrize("svc", ["portfolio", "btcalts", "carry", "news"])
def test_the_bot_token_goes_exactly_to_services_that_message_the_owner(svc):
    """Сборщик новостей владельцу не пишет — токен бота ему не нужен (29.09.2026)."""
    package = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / svc).rglob("*.py"))
    messages_owner = bool(re.search(r"telegram_bot|executor\.cycle_outcome|\bnotify\(", package))
    assert ("TELEGRAM_BOT_TOKEN" in secret_map()[svc]) == messages_owner, svc


def test_the_internet_facing_api_holds_no_exchange_keys():
    api = secret_map()["api"]
    assert not {n for n in api if "BYBIT" in n}, api
    assert "ENCRYPTION_KEY" not in api, "ключ расшифровки ключей биржи из базы"
    assert not [p for p in (ROOT / "api").rglob("*.py") if "bybit_client" in p.read_text(encoding="utf-8")], \
        "API снова ходит в биржу с ключами"


def test_each_executor_sees_only_its_own_account():
    m = secret_map()
    assert {"BTCALTS_BYBIT_API_KEY", "BTCALTS_BYBIT_API_SECRET"} <= m["btcalts"]
    assert {"CARRY_BYBIT_API_KEY", "CARRY_BYBIT_API_SECRET"} <= m["carry"]
    for svc, names in m.items():
        if svc != "btcalts":
            assert not {n for n in names if n.startswith("BTCALTS_")}, svc
        if svc != "carry":
            assert not {n for n in names if n.startswith("CARRY_")}, svc
    # Демо-счёт бота и И14 общий (решение владельца 14.09): его ключи — только у этих двух.
    assert {svc for svc, names in m.items() if "BYBIT_API_KEY" in names} == {"bot", "portfolio"}
    assert not {n for n in m["news"] if "BYBIT" in n}


def test_the_balance_endpoint_with_exchange_keys_is_gone():
    from api.routers import system
    assert "/api/system/balance" not in {r.path for r in system.router.routes}


# ---------------------------------------------------------------------------
# Сборка файлов на сервере
# ---------------------------------------------------------------------------

SAMPLE = """# Создан deploy.sh env
ENVIRONMENT=production
TELEGRAM_BOT_TOKEN=tg-value-1
BYBIT_API_KEY=bybit-value-2
BYBIT_API_SECRET=bybit-value-3
BTCALTS_BYBIT_API_KEY=btc-value-4
CARRY_BYBIT_API_SECRET=carry-value-5
ENCRYPTION_KEY=enc-value-6
REDIS_URL=redis://:pw-value-7@redis:6379
REDIS_PASSWORD=pw-value-7
OPENROUTER_API_KEY=or-value-8
SENTRY_DSN=https://dsn-value-9@example
BOT_INTERVAL=1800

DB_PATH=/data/db/market_bot.db
"""


@pytest.fixture
def split(tmp_path):
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("нет sh")
    env = tmp_path / "shared.env"
    env.write_bytes(SAMPLE.encode("utf-8"))
    out = tmp_path / "env"
    run = subprocess.run([sh, str(ROOT / "deploy" / "split-env.sh"), str(MAP), str(env), str(out)],
                         capture_output=True, timeout=60)
    assert run.returncode == 0, run.stderr.decode("utf-8", "replace")
    files = {p.stem: p.read_text(encoding="utf-8") for p in out.glob("*.env")}
    return files, run.stdout.decode("utf-8", "replace")


def names(text):
    return {line.split("=", 1)[0] for line in text.splitlines() if "=" in line and not line.startswith("#")}


def test_split_gives_each_service_its_secrets_and_all_plain_settings(split):
    files, _ = split
    assert set(files) == set(secret_map())
    plain = {"ENVIRONMENT", "BOT_INTERVAL", "DB_PATH"}
    for svc, text in files.items():
        assert plain <= names(text), svc
        present_secrets = {n for n in names(text) if is_secret(n)}
        assert present_secrets == {n for n in secret_map()[svc] if n in names(SAMPLE)}, svc


def test_split_keeps_the_api_away_from_exchange_keys(split):
    files, _ = split
    api = files["api"]
    for value in ("bybit-value-2", "bybit-value-3", "btc-value-4", "carry-value-5", "enc-value-6", "or-value-8"):
        assert value not in api
    assert "tg-value-1" in api and "pw-value-7@redis" in api


def test_split_prints_names_never_values(split):
    _, output = split
    assert "BYBIT_API_KEY" in output
    assert not re.search(r"value-\d", output), output


def test_deploy_rebuilds_the_files_before_every_up():
    text = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    compose = re.search(r"^compose\(\) \{\n(.*?)^\}", text, re.M | re.S).group(1)
    assert compose.index('[ "${1:-}" = up ] && split_env') < compose.index("docker compose")
    support = re.search(r"^install_support_files\(\) \{\n(.*?)^\}", text, re.M | re.S).group(1)
    assert re.search(r"for f in [^;\n]*\bsplit-env\.sh\b", support), "скрипт сборки не ставится на сервер"
    assert 'cp "$rel/deploy/service-secrets.conf"' in support, "карта не ставится на сервер"
    assert support.rstrip().endswith("split_env") or "\n  split_env\n" in support


@pytest.mark.parametrize("step,name", [("step_token", "TELEGRAM_BOT_TOKEN"), ("step_ai_key", "OPENROUTER_API_KEY"),
                                       ("step_bybit_key", "BYBIT_API_KEY")])
def test_replacing_a_secret_recreates_exactly_the_services_that_hold_it(step, name):
    """До 30.09 списки были в шагах: token пересоздавал bot и api, bybit-key — никого (И14 со старым ключом)."""
    text = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    body = re.search(rf"^{step}\(\) \{{\n(.*?)^\}}", text, re.M | re.S).group(1)
    assert f"svc=$(services_with {name})" in body and "--force-recreate $svc" in body


@pytest.fixture
def services_with(tmp_path):
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("нет sh")
    text = (ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")
    fn = re.search(r"^services_with\(\) \{\n.*?^\}\n", text, re.M | re.S).group(0)

    def run(name, with_map=True):
        app = tmp_path / ("app" if with_map else "empty")
        app.mkdir(exist_ok=True)
        if with_map:
            (app / "service-secrets.conf").write_bytes(MAP.read_bytes())
        script = f'APP="{app.as_posix()}"\nENV_SERVICES="bot api portfolio btcalts carry news"\n{fn}services_with {name}\n'
        out = subprocess.run([sh, "-c", script], capture_output=True, timeout=30)
        return out.stdout.decode().split()
    return run


def test_services_with_reads_the_map(services_with):
    for name in ("TELEGRAM_BOT_TOKEN", "OPENROUTER_API_KEY", "BYBIT_API_KEY", "CARRY_BYBIT_API_KEY"):
        assert set(services_with(name)) == {s for s, names in secret_map().items() if name in names}, name
    assert set(services_with("BYBIT_API_KEY")) == {"bot", "portfolio"}
    assert services_with("TELEGRAM_BOT_TOKEN", with_map=False) == ["bot", "api", "portfolio", "btcalts", "carry", "news"]
