"""
Напоминания владельцу и инструменты, добавленные 14.10.2026 после проверки прода: срок ключей биржи (найден только
ручной проверкой), оплата хостинга (сервер простоял 34 ч), текущая просадка в мини-аппе (показывалась наибольшая),
пересборка замков и слияние PR Dependabot (делались руками).
"""
import os
import pathlib
import re
import shutil
import subprocess
from datetime import UTC, datetime

import pytest

from portfolio import calendar, executor

ROOT = pathlib.Path(__file__).resolve().parent.parent
DAY = 86_400_000


def ms(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=UTC).timestamp() * 1000)


class Store:
    def __init__(self):
        self.kv = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = str(v)


class Cli:
    def __init__(self, days=None, expires="2026-12-12T08:00:00Z", fail=False):
        self.days, self.expires, self.fail, self.calls = days, expires, fail, 0

    def _get(self, path, params, signed):
        self.calls += 1
        assert path == "/v5/user/query-api" and signed
        if self.fail:
            raise RuntimeError("сеть")
        return {"deadlineDay": self.days, "expiredAt": self.expires}


@pytest.mark.parametrize("days, warned", [(21, True), (0, True), (22, False), (-2, False)])
def test_key_expiry_warns_within_the_window_and_stays_quiet_for_permanent_keys(days, warned):
    sent = []
    left = executor.key_expiry_notice(Cli(days), Store(), ms("2026-11-21 10:00"), lambda t: sent.append(t) or True, "И14")
    assert left == days
    assert bool(sent) == warned
    if warned:
        assert "И14" in sent[0] and f"через {days} дн." in sent[0] and "2026-12-12" in sent[0]


def test_key_expiry_is_checked_once_a_day_and_survives_errors():
    store, sent = Store(), []
    cli = Cli(10)
    executor.key_expiry_notice(cli, store, ms("2026-12-02 09:00"), sent.append, "И13")
    executor.key_expiry_notice(cli, store, ms("2026-12-02 15:00"), sent.append, "И13")
    assert cli.calls == 1 and len(sent) == 1
    executor.key_expiry_notice(cli, store, ms("2026-12-03 09:00"), sent.append, "И13")
    assert cli.calls == 2 and len(sent) == 2, "напоминание повторяется каждый день, пока ключ не заменён"
    broken = Cli(fail=True)
    assert executor.key_expiry_notice(broken, Store(), ms("2026-12-02 09:00"), sent.append, "И18") is None
    assert len(sent) == 2


def test_warning_window_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("BYBIT_EXPIRY_WARN_DAYS", "40")
    sent = []
    executor.key_expiry_notice(Cli(35), Store(), ms("2026-11-01 09:00"), sent.append, "И14")
    assert sent


def test_every_executor_checks_its_key_each_cycle():
    for rel, name in (("portfolio/__main__.py", "И14"), ("btcalts/__main__.py", "И18"), ("carry/__main__.py", "И13")):
        text = (ROOT / rel).read_text(encoding="utf-8")
        body = re.search(r"^def cycle\(.*?(?=^def )", text, re.M | re.S).group(0)
        assert f'executor.key_expiry_notice(cli, store, now, notify, "{name}")' in body, rel


def test_the_calendar_no_longer_guesses_key_dates():
    assert not [t for _, t in calendar.CHECKPOINTS if "ключ" in t.lower()]


def _due(now: str, env_day, monkeypatch):
    if env_day is None:
        monkeypatch.delenv("HOSTING_RENEWAL_DAY", raising=False)
    else:
        monkeypatch.setenv("HOSTING_RENEWAL_DAY", str(env_day))
    return [t for _, t in calendar.due(ms(now), lambda k: False) if "хостинга" in t]


def test_hosting_payment_is_reminded_three_days_before_and_on_the_day(monkeypatch):
    assert _due("2026-11-10 08:30", 13, monkeypatch) and "Через 3 дня, 13.11.2026" in _due("2026-11-10 08:30", 13, monkeypatch)[0]
    assert "Сегодня, 13.11.2026" in _due("2026-11-13 09:00", 13, monkeypatch)[0]
    assert _due("2026-11-11 09:00", 13, monkeypatch) == []
    assert _due("2026-11-13 09:00", None, monkeypatch) == [], "без настройки — молчим"
    assert _due("2026-11-13 09:00", 31, monkeypatch) == [], "29–31 есть не в каждом месяце — не принимаются"


def test_the_reminder_three_days_ahead_crosses_the_month_and_the_year(monkeypatch):
    assert "13.01.2027" in _due("2027-01-10 09:00", 13, monkeypatch)[0]
    assert "02.01.2027" in _due("2026-12-30 09:00", 2, monkeypatch)[0], "напоминание в декабре о 2 января"


def test_prod_compose_sets_the_hosting_day_for_the_calendar_service():
    import yaml
    compose = yaml.safe_load((ROOT / "deploy" / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    day = int(compose["services"]["portfolio"]["environment"]["HOSTING_RENEWAL_DAY"])
    assert 1 <= day <= 28


def test_current_drawdown_is_from_the_executor_peak_and_the_largest_is_kept(tmp_path):
    from api import research_data as rd
    from portfolio.store import Store as PStore
    db = tmp_path / "p.db"
    s = PStore(str(db))
    t0 = ms("2026-09-21 00:00")
    s.set("started_at", t0)
    s.set("start_equity", 1000)
    for i, eq in enumerate((1000, 1050, 800, 900, 980)):
        s.snapshot(t0 + i * 3_600_000, eq, {})
    s.set("peak_equity", 1100)                 # исполнитель видел пик между снимками
    p = rd.read_portfolio(str(db), 1000.0, t0 + 10 * 3_600_000)
    assert p["drawdown"] == pytest.approx(250.0), "наибольшая: 1050 → 800"
    assert p["drawdown_now"] == pytest.approx(120.0), "текущая — от пика исполнителя 1100 до 980"


def test_mini_app_labels_current_and_largest_drawdown():
    over = (ROOT / "miniapp/src/screens/Overview/Overview.tsx").read_text(encoding="utf-8")
    port = (ROOT / "miniapp/src/screens/Portfolios/Portfolios.tsx").read_text(encoding="utf-8")
    assert "Просадка от пика" not in over + port
    assert "Просадка сейчас" in over and "p.drawdown_now > p.capital * p.stop_fraction" in over
    assert "Просадка сейчас" in port and "Наибольшая просадка" in port


# --- инструменты зависимостей ---------------------------------------------------------------------------

def test_relock_keeps_the_header_and_relocks_both_files(tmp_path):
    sh = shutil.which("sh")
    if not sh:
        pytest.skip("нет sh")
    for name in ("requirements.txt", "requirements-dev.txt"):
        (tmp_path / name).write_text("x==1\n", encoding="utf-8")
    for name in ("requirements.lock", "requirements-dev.lock"):
        (tmp_path / name).write_text("# шапка 1\n# шапка 2\nold==0 \\\n    --hash=sha256:aa\n", encoding="utf-8")
    fake = tmp_path / "fake_uv.sh"
    fake.write_text('while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\nprintf "new==2\\n" > "$out"\n',
                    encoding="utf-8")
    run = subprocess.run([sh, str(ROOT / "deploy" / "relock.sh")], cwd=tmp_path, capture_output=True, timeout=60,
                         env={"UV": f"sh {fake.as_posix()}", "PATH": os.environ["PATH"]})   # sh из PATH: путь без пробелов
    assert run.returncode == 0, run.stderr.decode("utf-8", "replace")
    for name in ("requirements.lock", "requirements-dev.lock"):
        assert (tmp_path / name).read_text(encoding="utf-8") == "# шапка 1\n# шапка 2\nnew==2\n"


def test_dependabot_tool_relocks_checks_ci_and_deletes_the_branch():
    text = (ROOT / "deploy" / "dependabot.sh").read_text(encoding="utf-8")
    assert "sh deploy/relock.sh" in text and "grep -q '^requirements'" in text
    assert text.index("wait_ci") < text.index("gh pr merge") and "--delete-branch" in text
    assert 'app/dependabot' in text, "чужие PR инструмент не трогает"
    assert "--force-with-lease" in text
