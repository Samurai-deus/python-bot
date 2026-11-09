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
from datetime import datetime, timedelta, UTC

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
  *Mounts*) echo "$STUB_DIR/data" ;;
  *StartedAt*) cat "$STUB_DIR/started_$name" 2>/dev/null ;;
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
        # Бот доставляет heartbeat: метка в томе /data обновляется к каждому запуску сторожа (deliver=True).
        self.mark = self.stub / "data" / "db" / "telegram_delivered"
        self.mark.parent.mkdir(parents=True)
        self.deliver = True
        self.started(self.now - 2 * 86400)

    def started(self, ts):
        """Время запуска контейнера бота — в формате docker (наносекунды, Z)."""
        self.put("started_market-bot", datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.123456789Z"))

    def delivered(self, ts):
        self.mark.write_text(str(ts), encoding="ascii")

    def put(self, name, value):
        (self.stub / name).write_text(value, encoding="utf-8")

    def drop(self, name):
        (self.stub / name).unlink()

    def cert_expires_in(self, days):
        self.put("cert_end", cert_date(datetime.now(UTC) + timedelta(days=days)))

    def run(self, at=0):
        if self.deliver:
            self.delivered(self.now + at - 600)
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


H = 3600


def test_silent_bot_alerts_once_and_reports_recovery(host):
    """
    19.10–09.11.2026: бот был healthy, но три недели не доставил ни одного сообщения. Сторож пишет сам — одно
    сообщение на эпизод (текст без «сколько часов», иначе новое каждый час) и отдельную строку о восстановлении.
    """
    host.deliver = False
    host.delivered(host.now - H)
    host.run()
    host.run(at=int(1.5 * H))                   # 2,5 ч молчания — ещё не тревога
    assert len(host.messages()) == 1, host.messages()

    host.run(at=int(2.5 * H))                   # 3,5 ч
    host.run(at=3 * H)
    host.run(at=4 * H)
    msgs = host.messages()
    assert len(msgs) == 2, "о молчании — одно сообщение на эпизод"
    assert "не доставляет в Telegram" in msgs[1] and "последний heartbeat" in msgs[1]

    host.deliver = True
    host.run(at=4 * H + 300)
    msgs = host.messages()
    assert len(msgs) == 3
    assert "снова в порядке" in msgs[2] and "снова доставляет в Telegram" in msgs[2]
    host.run(at=4 * H + 600)
    assert len(host.messages()) == 3, "о восстановлении — один раз"


def test_recovery_is_reported_even_while_other_problems_hold(host):
    host.deliver = False
    host.delivered(host.now - 4 * H)
    host.put("disk", "95")
    host.run()
    assert "не доставляет в Telegram" in host.messages()[-1]

    host.deliver = True
    host.run(at=300)
    last = host.messages()[-1]
    assert "диск заполнен" in last and "не доставляет" not in last
    assert "снова доставляет в Telegram" in last, "«всё в порядке» не придёт — восстановление отдельной строкой"


def test_silence_is_counted_from_container_start(host):
    """После простоя бот час ждёт первого heartbeat: старая метка — не тревога, пока с запуска не прошло 3 ч."""
    host.deliver = False
    host.delivered(host.now - 2 * 86400)
    host.started(host.now - H)
    host.run()
    assert len(host.messages()) == 1 and "сторож включён" in host.messages()[0]

    host.run(at=int(2.5 * H))
    assert "ни одного heartbeat с запуска" in host.messages()[-1]


def test_no_mark_yet_is_silence_after_three_hours_of_uptime(host):
    host.deliver = False
    host.started(host.now - 4 * H)
    host.run()
    assert "ни одного heartbeat с запуска" in host.messages()[-1]


def test_stopped_bot_is_a_container_problem_not_silence(host):
    host.deliver = False
    host.delivered(host.now - 10 * H)
    host.put("status_market-bot", "exited/none")
    host.run()
    text = host.messages()[-1]
    assert "контейнер market-bot: exited" in text
    assert "Telegram" not in text, "остановленный бот молчит по другой причине — о ней уже сказано"
