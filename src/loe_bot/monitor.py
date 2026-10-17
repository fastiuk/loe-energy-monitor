"""Background jobs: poll the schedule, announce changes, send reminders."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from datetime import date, datetime, timedelta

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramMigrateToChat,
    TelegramRetryAfter,
)

from loe_core import (
    DayChange,
    DaySchedule,
    Event,
    LoeClient,
    LoeError,
    diff_schedules,
    group_events,
    merge_schedules,
    now_kyiv,
)

from .db import Storage, Subscriber
from .formatting import format_change, format_reminder

log = logging.getLogger(__name__)

REMINDER_TICK = 30  # seconds
REMINDER_RETENTION = timedelta(days=2)
SEND_DELAY = 0.05  # well below Telegram's ~30 messages/s limit
BOOTSTRAP_KEY = "bootstrapped_at"

ClaimReminder = Callable[[int, str, str, datetime], bool]


def change_messages(
    changes: Sequence[DayChange], subscribers: Iterable[Subscriber]
) -> list[tuple[int, str]]:
    messages = []
    for sub in subscribers:
        for change in changes:
            if groups := [g for g in change.groups if g in sub.groups]:
                messages.append((sub.chat_id, format_change(change, groups)))
    return messages


def due_reminders(
    now: datetime,
    schedules: Iterable[DaySchedule],
    subscribers: Iterable[Subscriber],
    claim: ClaimReminder,
) -> list[tuple[int, str]]:
    """Reminders whose time window [event - lead, event) contains ``now`` and were not sent yet.

    A bot that was down catches up within the window and skips events already past.
    """
    days = sorted(schedules, key=lambda s: s.day)
    events: dict[str, list[Event]] = {}
    messages = []
    for sub in subscribers:
        if sub.reminder_minutes <= 0:
            continue
        lead = timedelta(minutes=sub.reminder_minutes)
        for group in sorted(sub.groups):
            if group not in events:
                events[group] = group_events(days, group)
            for event in events[group]:
                window_start = event.at - lead
                if event.kind == "on":
                    # "Power is coming back" only makes sense once it went off.
                    window_start = max(window_start, event.outage.start)
                if window_start <= now < event.at and claim(
                    sub.chat_id, group, event.kind, event.at
                ):
                    messages.append((sub.chat_id, format_reminder(group, event, now)))
    return messages


class Monitor:
    def __init__(
        self,
        bot: Bot,
        db: Storage,
        client: LoeClient,
        *,
        poll_interval: int,
        clock: Callable[[], datetime] = now_kyiv,
    ) -> None:
        self._bot = bot
        self._db = db
        self._client = client
        self._poll_interval = poll_interval
        self._clock = clock
        self.schedules: dict[date, DaySchedule] = db.load_schedules()

    def today(self) -> date:
        return self._clock().date()

    async def run(self) -> None:
        await asyncio.gather(
            self._repeat(self.poll_once, self._poll_interval),
            self._repeat(self.check_reminders, REMINDER_TICK),
        )

    async def _repeat(self, job: Callable[[], Awaitable[None]], interval: float) -> None:
        while True:
            try:
                await job()
            except LoeError as err:
                log.warning("%s", err)
            except Exception:
                log.exception("%s failed", job.__name__)
            await asyncio.sleep(interval)

    async def poll_once(self) -> None:
        fresh = await self._client.fetch_schedule()
        now = self._clock()
        today = now.date()
        fresh = {day: s for day, s in fresh.items() if day >= today}

        changes = diff_schedules(self.schedules, fresh)
        self.schedules = merge_schedules(self.schedules, fresh, since=today)
        self._db.save_schedules(self.schedules)
        self._db.prune_reminders(now - REMINDER_RETENTION)

        if self._db.get_meta(BOOTSTRAP_KEY) is None:
            # First run: nothing to compare with, so don't announce what is already there.
            self._db.set_meta(BOOTSTRAP_KEY, now.isoformat())
            log.info("Stored initial schedule for %s", ", ".join(map(str, self.schedules)) or "-")
            return
        for change in changes:
            action = "Published" if change.published else "Changed"
            log.info("%s schedule for %s: %s", action, change.schedule.day, ", ".join(change.groups))
        await self._send_all(change_messages(changes, self._db.subscribers()))

    async def check_reminders(self) -> None:
        messages = due_reminders(
            self._clock(), self.schedules.values(), self._db.subscribers(), self._db.claim_reminder
        )
        await self._send_all(messages)

    async def _send_all(self, messages: Iterable[tuple[int, str]]) -> None:
        for chat_id, text in messages:
            await self._send(chat_id, text)
            await asyncio.sleep(SEND_DELAY)

    async def _send(self, chat_id: int, text: str, *, retry: bool = True) -> None:
        try:
            await self._bot.send_message(chat_id, text)
        except TelegramRetryAfter as err:
            if retry:
                await asyncio.sleep(err.retry_after)
                await self._send(chat_id, text, retry=False)
        except TelegramMigrateToChat as err:
            self._db.migrate_chat(chat_id, err.migrate_to_chat_id)
            if retry:
                await self._send(err.migrate_to_chat_id, text, retry=False)
        except TelegramForbiddenError:
            log.info("Chat %s blocked or removed the bot; unsubscribing", chat_id)
            self._db.delete_chat(chat_id)
        except TelegramBadRequest as err:
            if "chat not found" in err.message.lower():
                log.info("Chat %s not found; unsubscribing", chat_id)
                self._db.delete_chat(chat_id)
            else:
                log.warning("Failed to send to %s: %s", chat_id, err)
        except TelegramAPIError as err:
            log.warning("Failed to send to %s: %s", chat_id, err)
