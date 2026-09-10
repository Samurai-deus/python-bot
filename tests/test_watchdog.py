"""
Поведение сторожа хоста (deploy/watchdog.sh) на заглушках docker, curl,
systemctl, openssl и df. Проверяется то, что ломается молча: сторож, который
пишет каждые 5 минут, перестают читать; сторож, который забыл про неотправленное,
молчит ровно тогда, когда нужен.
"""
import os
import pathlib
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
TOKEN = "123456:SECRET_TOKEN_VALUE_FOR_TEST"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(not BASH, reason="нет bash для запуска сторожа")

STUBS = {
    "docker": """#!/bin/sh
[ "$1" = inspect ] || exit 1
fmt="$3"; name="$4"
case "$fmt" in
  *RestartCount*) cat "$STUB_DIR/restarts_$name" 2>/dev/null || echo 0 ;;
  *Health.Log*) cat "$STUB_DIR/reason_$name" 2>/dev/null ;;
  *State.Status*) if [ -f "$STUB_DIR/status_$name" ]; then cat "$STUB_DIR/status_$name"; else echo running/healthy; fi ;;
esac
""",
    "curl": """#!/bin/sh
case " $* " in
  *" -K - "*)
    cat >> "$STUB_DIR/curl_config"
    printf '%s\\n---\\n' "$*" >> "$STUB_DIR/curl_argv"
    if [ -f "$STUB_DIR/tg_response" ]; then cat "$STUB_DIR/tg_response"; else echo '{"ok":true}'; fi ;;
  *) echo 200 ;;
esac
""",
    "systemctl": """#!/bin/sh
[ -f "$STUB_DIR/backup_failed" ]
""",
    "openssl": """#!/bin/sh
echo "notAfter=$(cat "$STUB_DIR/cert_end")"
""",
    "df": """#!/bin/sh
echo "Filesystem 1024-blocks Used Available Capacity Mounted"
echo "/dev/vda 100 26 74 $(cat "$STUB_DIR/disk" 2>/dev/null || echo 26)% /"
""",
}


def cert_date(dt):
    return f"{MONTHS[dt.month - 1]} {dt.day:2d} {dt:%H:%M:%S} {dt.year} GMT"


class Host:
    def __init__(self, tmp_path):
        self.app = tmp_path / "app"
        self.stub = tmp_path / "stub"
        self.bin = tmp_path / "bin"
        self.state = tmp_path / "state"
        for d in (self.app / "backups", self.stub, self.bin):
            d.mkdir(parents=True)
        (self.app / ".env").write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\nADMIN_CHAT_ID=42\n", encoding="ascii")
        (self.app / "deploy.conf").write_text("DOMAIN=example.test\n", encoding="ascii")
        shutil.copy(DEPLOY / "notify.sh", self.app / "notify.sh")
        for name, body in STUBS.items():
            path = self.bin / name
            path.write_bytes(body.encode("utf-8"))
            path.chmod(0o755)
        self.now = int(time.time())
        self.backup = self.app / "backups" / "market_bot-20260910-034000.db.gz"
        self.backup.write_bytes(b"gz")
        (self.app / "backups" / ".offsite_ok").write_text(str(self.now), encoding="ascii")
        self.cert_expires_in(days=60)

    def put(self, name, value):
        (self.stub / name).write_text(value, encoding="utf-8")

    def drop(self, name):
        (self.stub / name).unlink()

    def cert_expires_in(self, days):
        self.put("cert_end", cert_date(datetime.now(timezone.utc) + timedelta(days=days)))

    def run(self, at=0):
        env = dict(
            os.environ,
            PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            MARKET_BOT_APP=str(self.app),
            WATCHDOG_STATE=str(self.state),
            WATCHDOG_NOW=str(self.now + at),
            WATCHDOG_CERT=str(self.app / "cert.pem"),
            STUB_DIR=str(self.stub),
        )
        return subprocess.run([BASH, str(DEPLOY / "watchdog.sh")], env=env,
                              capture_output=True, text=True, encoding="utf-8")

    def messages(self):
        path = self.stub / "curl_argv"
        if not path.exists():
            return []
        return [m for m in path.read_text(encoding="utf-8").split("\n---\n") if m.strip()]


@pytest.fixture
def host(tmp_path):
    return Host(tmp_path)


def test_first_run_announces_itself_then_stays_quiet(host):
    assert host.run().returncode == 0
    assert host.run(at=300).returncode == 0
    msgs = host.messages()
    assert len(msgs) == 1, msgs
    assert "сторож включён" in msgs[0]


def test_problem_alerts_once_reminds_and_reports_recovery(host):
    host.run()
    host.put("status_market-bot", "running/unhealthy")
    host.put("reason_market-bot", "Healthcheck failed: цикл анализа не оборачивался 2000 с\n")

    host.run(at=300)
    host.run(at=600)
    msgs = host.messages()
    assert len(msgs) == 2, "одна и та же проблема не повторяется каждые 5 минут"
    assert "проблема" in msgs[1] and "running/unhealthy" in msgs[1]
    assert "цикл анализа не оборачивался" in msgs[1], "причину из healthcheck видно в алерте"

    host.run(at=300 + 6 * 3600)
    assert "всё ещё" in host.messages()[2]

    host.drop("status_market-bot")
    host.run(at=600 + 6 * 3600)
    assert "снова в порядке" in host.messages()[3]
    assert len(host.messages()) == 4


def test_failed_delivery_is_retried_next_run(host):
    host.run()
    host.put("status_market-bot-redis", "exited/unhealthy")
    host.put("tg_response", '{"ok":false,"description":"Bad Gateway"}')
    failed = host.run(at=300)
    assert failed.returncode == 1, "неотправленный алерт обязан уронить юнит"

    host.drop("tg_response")
    assert host.run(at=600).returncode == 0
    msgs = host.messages()
    assert "market-bot-redis" in msgs[-1] and "проблема" in msgs[-1], "повтор после сбоя отправки"


def test_bot_restart_is_reported(host):
    host.run()
    host.put("restarts_market-bot", "2")
    host.run(at=300)
    assert "перезапускался: 2" in host.messages()[-1]
    host.run(at=600)
    assert len(host.messages()) == 2, "о тех же перезапусках второй раз не пишет"


def test_stale_backup_and_missing_offsite_copy(host):
    old = host.now - 30 * 3600
    os.utime(host.backup, (old, old))
    (host.app / "backups" / ".offsite_ok").write_text(str(old), encoding="ascii")
    host.run()
    text = host.messages()[-1]
    assert "бэкап базы сделан" in text
    assert "вне сервера" in text


def test_expiring_certificate_and_full_disk(host):
    host.cert_expires_in(days=5)
    host.put("disk", "95")
    host.run()
    text = host.messages()[-1]
    assert "сертификат сайта истекает" in text
    assert "диск заполнен на 95" in text


def test_token_never_goes_to_the_command_line(host):
    host.put("status_market-bot", "running/unhealthy")
    host.run()
    argv = (host.stub / "curl_argv").read_text(encoding="utf-8")
    config = (host.stub / "curl_config").read_text(encoding="utf-8")
    assert TOKEN not in argv, "аргументы процесса видны в ps — токен только через stdin"
    assert TOKEN in config
