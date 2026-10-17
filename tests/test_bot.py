import asyncio
from datetime import date, datetime

import pytest

from loe_bot.config import Config
from loe_bot.db import Storage
from loe_bot.formatting import fmt_duration, format_schedule
from loe_bot.keyboards import DayCallback, GroupCallback, groups_keyboard
from loe_bot.monitor import Monitor
from loe_core import GROUPS, KYIV, parse_day

DAY = date(2026, 10, 17)
TOMORROW = date(2026, 10, 18)


def schedule(day: date, *sentences: str):
    raw = f"<p>Графік погодинних відключень на {day:%d.%m.%Y}</p>"
    raw += "".join(f"<p>{s}</p>" for s in sentences)
    return parse_day(raw)


def kyiv(h: int, m: int = 0, day: date = DAY) -> datetime:
    return datetime(day.year, day.month, day.day, h, m, tzinfo=KYIV)


class FakeClient:
    def __init__(self, result):
        self.result = result

    async def fetch_schedule(self):
        return self.result


class FakeBot:
    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text))


@pytest.fixture
def db():
    storage = Storage(":memory:")
    storage.ensure_chat(1, 15)
    storage.toggle_group(1, "4.2")
    storage.ensure_chat(2, 0)  # reminders off
    storage.toggle_group(2, "1.1")
    storage.toggle_group(2, "4.2")
    yield storage
    storage.close()


def test_subscriptions(db):
    assert db.groups(1) == {"4.2"}
    assert db.toggle_group(1, "4.2") == set()
    assert [s.chat_id for s in db.subscribers()] == [2]
    db.migrate_chat(2, -100500)
    assert db.groups(-100500) == {"1.1", "4.2"}
    db.delete_chat(-100500)
    assert db.subscribers() == []


def test_monitor_cycle(db):
    now = [kyiv(9)]
    client = FakeClient(
        {DAY: schedule(DAY, "Група 1.1. Електроенергія є.", "Група 4.2. Електроенергія є.")}
    )
    bot = FakeBot()
    monitor = Monitor(bot, db, client, poll_interval=180, clock=lambda: now[0])

    # First poll only stores what is already published.
    asyncio.run(monitor.poll_once())
    assert bot.sent == []

    # 4.2 changes: both chats watching 4.2 hear about it, with the old value.
    client.result = {
        DAY: schedule(
            DAY,
            "Група 1.1. Електроенергія є.",
            "Група 4.2. Електроенергії немає з 10:00 до 12:00.",
        )
    }
    asyncio.run(monitor.poll_once())
    assert [chat for chat, _ in bot.sent] == [1, 2]
    assert "Змінено графік на 17.10 (сб)" in bot.sent[0][1]
    assert "10:00–12:00" in bot.sent[0][1] and "було: світло є" in bot.sent[0][1]
    assert "1.1" not in bot.sent[1][1]  # only the changed group

    # Tomorrow is published: each chat gets its own groups.
    bot.sent.clear()
    client.result = {**client.result, TOMORROW: schedule(TOMORROW, "Група 1.1. Електроенергія є.")}
    asyncio.run(monitor.poll_once())
    assert bot.sent == [(2, bot.sent[0][1])]
    assert "Опубліковано графік на 18.10 (нд)" in bot.sent[0][1]

    # Reminder 15 min before the outage, only for chat 1, only once.
    bot.sent.clear()
    now[0] = kyiv(9, 44)
    asyncio.run(monitor.check_reminders())
    assert bot.sent == []
    now[0] = kyiv(9, 45)
    asyncio.run(monitor.check_reminders())
    now[0] = kyiv(9, 46)
    asyncio.run(monitor.check_reminders())
    assert len(bot.sent) == 1
    assert bot.sent[0][0] == 1 and "через 15 хв вимкнення" in bot.sent[0][1]

    # Restoration reminder.
    now[0] = kyiv(11, 50)
    asyncio.run(monitor.check_reminders())
    assert "через 10 хв за графіком мають увімкнути світло (о 12:00)" in bot.sent[-1][1]

    # State survives a restart.
    assert Monitor(bot, db, client, poll_interval=180).schedules == monitor.schedules


def test_restoration_reminder_waits_for_outage_start(db):
    db.set_reminder(1, 60)
    now = [kyiv(9, 30)]
    client = FakeClient({DAY: schedule(DAY, "Група 4.2. Електроенергії немає з 10:00 до 10:30.")})
    bot = FakeBot()
    monitor = Monitor(bot, db, client, poll_interval=180, clock=lambda: now[0])
    asyncio.run(monitor.poll_once())
    asyncio.run(monitor.check_reminders())
    assert len(bot.sent) == 1 and "вимкнення" in bot.sent[0][1]
    now[0] = kyiv(10)
    asyncio.run(monitor.check_reminders())
    assert len(bot.sent) == 2 and "через 30 хв за графіком мають увімкнути" in bot.sent[1][1]


def test_past_days_are_ignored(db):
    now = [kyiv(1, day=TOMORROW)]
    bot = FakeBot()
    monitor = Monitor(bot, db, FakeClient({}), poll_interval=180, clock=lambda: now[0])
    asyncio.run(monitor.poll_once())  # bootstrap
    # The site still shows yesterday as "Today" right after midnight.
    monitor._client.result = {DAY: schedule(DAY, "Група 4.2. Електроенергія є.")}
    asyncio.run(monitor.poll_once())
    assert bot.sent == [] and monitor.schedules == {}


def test_format_schedule_and_duration():
    s = schedule(DAY, "Група 4.2. Електроенергії немає з 10:00 до 12:00, з 21:00 до 24:00.")
    text = format_schedule(s, ["4.2", "1.1"], title="Графік на сьогодні,")
    assert "🔴 <b>4.2</b> — 10:00–12:00, 21:00–24:00 <i>(5 год)</i>" in text
    assert "❔ <b>1.1</b> — немає даних" in text
    assert fmt_duration(kyiv(12, 30) - kyiv(10)) == "2 год 30 хв"
    assert fmt_duration(kyiv(10, 45) - kyiv(10)) == "45 хв"


def test_callback_data_fits_telegram_limit():
    markup = groups_keyboard({"1.1"})
    buttons = [b for row in markup.inline_keyboard for b in row]
    assert len(buttons) == len(GROUPS) + 1
    assert buttons[0].text == "✅ 1.1"
    for data in [b.callback_data for b in buttons] + [DayCallback(day="2026-10-17").pack()]:
        assert len(data.encode()) <= 64
    assert GroupCallback.unpack(buttons[0].callback_data).group == "1.1"


def test_config_from_env():
    with pytest.raises(SystemExit):
        Config.from_env({})
    config = Config.from_env({"BOT_TOKEN": "x", "POLL_INTERVAL": "5", "LOG_LEVEL": "debug"})
    assert config.poll_interval == 60  # clamped to the minimum
    assert config.default_reminder == 15 and config.log_level == "DEBUG"
