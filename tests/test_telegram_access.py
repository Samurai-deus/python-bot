"""
Доступ к командам бота (аудит 10.09.2026: B-5, C-6, H-14).

Раньше /balance, /positions, /history и кнопки отвечали любому, кто нашёл бота
по имени, а /reset_trades выполнялся без OTP — хотя меняет базу так, что Risk
Core перестаёт видеть позиции, живые на бирже.

Хендлеры вызываются напрямую на подменённых объектах Update/Context: сеть и
Telegram не нужны.
"""
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

OWNER = 1001
FRIEND = 2002
STRANGER = 3003


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("ADMIN_CHAT_ID", str(OWNER))
    monkeypatch.setenv("ALLOWED_USER_IDS", str(FRIEND))


class _Message:
    def __init__(self, text=""):
        self.text = text
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


def make_update(user_id, text=""):
    msg = _Message(text)
    user = SimpleNamespace(id=user_id) if user_id is not None else None
    return SimpleNamespace(effective_user=user, effective_message=msg, message=msg, callback_query=None)


@pytest.fixture
def tc():
    import telegram_commands
    return telegram_commands


# ---------------------------------------------------------------------------
# Общий фильтр допуска
# ---------------------------------------------------------------------------

async def test_stranger_update_is_stopped(tc):
    with pytest.raises(ApplicationHandlerStop):
        await tc._reject_unknown_users(make_update(STRANGER), None)


async def test_stranger_gets_no_reply(tc):
    """Молчим: ответ на каждое сообщение позволил бы залпом выжечь лимит бота."""
    upd = make_update(STRANGER)
    with pytest.raises(ApplicationHandlerStop):
        await tc._reject_unknown_users(upd, None)
    assert upd.message.replies == []


async def test_update_without_user_is_stopped(tc):
    with pytest.raises(ApplicationHandlerStop):
        await tc._reject_unknown_users(make_update(None), None)


async def test_owner_and_observer_pass(tc):
    await tc._reject_unknown_users(make_update(OWNER), None)
    await tc._reject_unknown_users(make_update(FRIEND), None)


async def test_empty_admin_config_stops_even_the_owner(tc, monkeypatch):
    """Пустая настройка теперь закрывает всё, а не открывает."""
    monkeypatch.delenv("ADMIN_CHAT_ID")
    monkeypatch.delenv("ALLOWED_USER_IDS")
    with pytest.raises(ApplicationHandlerStop):
        await tc._reject_unknown_users(make_update(OWNER), None)


def test_gate_is_registered_before_every_handler(tc):
    """
    Фильтр обязан стоять в группе раньше всех остальных хендлеров, иначе
    команда успеет выполниться до проверки.
    """
    class FakeApp:
        def __init__(self):
            self.calls = []

        def add_handler(self, handler, group=0):
            self.calls.append((handler, group))

    app = FakeApp()
    tc.setup_commands(app)

    gates = [(h, g) for h, g in app.calls if getattr(h, "callback", None) is tc._reject_unknown_users]
    assert len(gates) == 1, "фильтр допуска должен быть зарегистрирован ровно один раз"
    gate_group = gates[0][1]
    other_groups = [g for h, g in app.calls if h is not gates[0][0]]
    assert other_groups, "хендлеры команд не зарегистрированы"
    assert all(gate_group < g for g in other_groups), (
        f"фильтр в группе {gate_group}, а есть хендлеры в группах {sorted(set(other_groups))}"
    )


# ---------------------------------------------------------------------------
# /reset_trades под OTP
# ---------------------------------------------------------------------------

@pytest.fixture
def cancel_calls(monkeypatch):
    import database
    calls = []

    def fake_cancel():
        calls.append(1)
        return 3

    monkeypatch.setattr(database, "force_cancel_open_trades", fake_cancel)
    return calls


async def test_reset_trades_does_nothing_without_code(tc, cancel_calls):
    ctx = SimpleNamespace(user_data={})
    await tc.cmd_reset_trades(make_update(OWNER), ctx)
    assert cancel_calls == [], "до подтверждения кодом база меняться не должна"
    assert ctx.user_data["pending_confirm"]["action"] == "reset_trades"


async def test_reset_trades_runs_after_correct_code(tc, cancel_calls):
    ctx = SimpleNamespace(user_data={})
    await tc.cmd_reset_trades(make_update(OWNER), ctx)
    code = ctx.user_data["pending_confirm"]["code"]

    confirm = make_update(OWNER, text=code)
    await tc.cmd_confirm(confirm, ctx)

    assert cancel_calls == [1]
    assert any("закрыто позиций" in r for r in confirm.message.replies)
    assert "pending_confirm" not in ctx.user_data


async def test_reset_trades_wrong_code_does_nothing(tc, cancel_calls):
    ctx = SimpleNamespace(user_data={})
    await tc.cmd_reset_trades(make_update(OWNER), ctx)
    await tc.cmd_confirm(make_update(OWNER, text="000000-wrong"), ctx)
    assert cancel_calls == []


async def test_observer_cannot_reset_trades(tc, cancel_calls):
    ctx = SimpleNamespace(user_data={})
    upd = make_update(FRIEND)
    await tc.cmd_reset_trades(upd, ctx)
    assert cancel_calls == []
    assert "pending_confirm" not in ctx.user_data
    assert upd.message.replies == ["❌ Unauthorized."]


async def test_observer_cannot_confirm_owners_code(tc, cancel_calls):
    """Код, выданный владельцу, не должен сработать в руках наблюдателя."""
    ctx = SimpleNamespace(user_data={})
    await tc.cmd_reset_trades(make_update(OWNER), ctx)
    code = ctx.user_data["pending_confirm"]["code"]
    await tc.cmd_confirm(make_update(FRIEND, text=code), ctx)
    assert cancel_calls == []


# ---------------------------------------------------------------------------
# /risk_reset — снятие защёлки HALTED: только владелец и только с OTP
# ---------------------------------------------------------------------------

class _FakeRiskCore:
    def __init__(self, latched=True):
        self.halt_latched = latched
        self.halt_reason = "тестовая причина"
        self.releases = []

    def reset_halt(self, by):
        self.releases.append(by)
        was_latched = self.halt_latched
        self.halt_latched = False
        return was_latched


@pytest.fixture
def fake_risk_core(monkeypatch):
    import core.risk_core as rc_module
    rc = _FakeRiskCore()
    monkeypatch.setattr(rc_module, "get_risk_core", lambda *a, **k: rc)
    return rc


async def test_risk_reset_releases_only_after_correct_code(tc, fake_risk_core):
    ctx = SimpleNamespace(user_data={})
    await tc.cmd_risk_reset(make_update(OWNER), ctx)
    assert fake_risk_core.releases == [], "до подтверждения кодом защёлка сниматься не должна"
    pending = ctx.user_data["pending_confirm"]
    assert pending["action"] == "risk_reset"

    await tc.cmd_confirm(make_update(OWNER, text=pending["code"]), ctx)
    assert len(fake_risk_core.releases) == 1
    assert str(OWNER) in fake_risk_core.releases[0], "в журнале должно остаться, кто снял защёлку"


async def test_observer_cannot_release_halt(tc, fake_risk_core):
    ctx = SimpleNamespace(user_data={})
    upd = make_update(FRIEND)
    await tc.cmd_risk_reset(upd, ctx)
    assert fake_risk_core.releases == []
    assert "pending_confirm" not in ctx.user_data
    assert upd.message.replies == ["❌ Unauthorized."]


async def test_risk_reset_without_halt_asks_no_code(tc, fake_risk_core):
    fake_risk_core.halt_latched = False
    ctx = SimpleNamespace(user_data={})
    upd = make_update(OWNER)
    await tc.cmd_risk_reset(upd, ctx)
    assert "pending_confirm" not in ctx.user_data
    assert any("нечего" in r for r in upd.message.replies)


def test_risk_reset_command_is_registered(tc):
    class FakeApp:
        def __init__(self):
            self.handlers = []

        def add_handler(self, handler, group=0):
            self.handlers.append(handler)

    app = FakeApp()
    tc.setup_commands(app)
    commands = set()
    for h in app.handlers:
        commands |= set(getattr(h, "commands", ()) or ())
    assert "risk_reset" in commands
