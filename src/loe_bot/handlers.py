"""Telegram commands and buttons.

Works in private chats, groups and channels; in groups and channels only admins
can change settings.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from datetime import date, timedelta
from typing import Any

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    CallbackQuery,
    Chat,
    ChatMemberUpdated,
    InlineKeyboardMarkup,
    Message,
    User,
)

from loe_core import GROUPS

from .config import Config
from .db import Storage
from .formatting import format_reminder_setting, format_schedule, format_subscription
from .keyboards import (
    DONE,
    REMINDER_OPTIONS,
    DayCallback,
    GroupCallback,
    ReminderCallback,
    all_groups_keyboard,
    groups_keyboard,
    reminder_keyboard,
)
from .monitor import Monitor

router = Router()

COMMANDS = [
    BotCommand(command="today", description="Графік на сьогодні"),
    BotCommand(command="tomorrow", description="Графік на завтра"),
    BotCommand(command="groups", description="Обрати групи"),
    BotCommand(command="reminder", description="Нагадування перед вимкненням"),
    BotCommand(command="stop", description="Скасувати підписку"),
    BotCommand(command="help", description="Довідка"),
]

WELCOME = (
    "⚡ Слідкую за графіком погодинних відключень Львівобленерго.\n\n"
    "Оберіть свої групи — я повідомлю, коли опублікують чи змінять графік, "
    "і нагадаю перед вимкненням та увімкненням світла.\n\n"
    'Свою групу можна знайти за адресою на <a href="https://poweron.loe.lviv.ua/">poweron.loe.lviv.ua</a>.'
)
HELP = (
    "/today — графік на сьогодні\n"
    "/tomorrow — графік на завтра\n"
    "/groups — обрати групи\n"
    "/reminder — за скільки хвилин нагадувати\n"
    "/stop — скасувати підписку\n\n"
    "У групових чатах і каналах налаштування змінюють лише адміністратори."
)
NOT_ADMIN = "Змінювати налаштування в цьому чаті можуть лише адміністратори."

ADMIN_STATUSES = {ChatMemberStatus.CREATOR, ChatMemberStatus.ADMINISTRATOR}
GONE_STATUSES = {ChatMemberStatus.LEFT, ChatMemberStatus.KICKED}


def command(*filters: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a handler for both chat messages and channel posts."""

    def register(handler: Callable[..., Any]) -> Callable[..., Any]:
        router.message(*filters)(handler)
        router.channel_post(*filters)(handler)
        return handler

    return register


async def can_manage(
    bot: Bot, chat: Chat, user: User | None, sender_chat: Chat | None = None
) -> bool:
    if chat.type == ChatType.PRIVATE:
        return True
    # Anonymous group admins and channel posts are sent on behalf of the chat itself.
    if sender_chat is not None and sender_chat.id == chat.id:
        return True
    if user is None:
        return False
    member = await bot.get_chat_member(chat.id, user.id)
    return member.status in ADMIN_STATUSES


async def _answer(query: CallbackQuery, text: str | None = None, *, alert: bool = False) -> None:
    # A button press handled late (e.g. the bot was offline) can no longer be answered;
    # its action is still applied, only the button's loading state stays a bit longer.
    with suppress(TelegramBadRequest):
        await query.answer(text, show_alert=alert)


async def _edit(message: Message, text: str) -> None:
    with suppress(TelegramBadRequest):  # "message is not modified" on double clicks
        await message.edit_text(text)


def _day_view(
    db: Storage, monitor: Monitor, chat_id: int, day: date, *, all_groups: bool
) -> tuple[str, InlineKeyboardMarkup | None]:
    is_today = day == monitor.today()
    schedule = monitor.schedules.get(day)
    if schedule is None:
        return ("Графіка на сьогодні немає." if is_today else "Графік на завтра ще не опублікували."), None
    subscribed = sorted(db.groups(chat_id))
    groups = subscribed if subscribed and not all_groups else list(GROUPS)
    title = "Графік на сьогодні," if is_today else "Графік на завтра,"
    markup = None if groups == list(GROUPS) else all_groups_keyboard(day.isoformat())
    return format_schedule(schedule, groups, title=title), markup


@command(CommandStart())
async def cmd_start(message: Message, db: Storage, config: Config) -> None:
    db.ensure_chat(message.chat.id, config.default_reminder)
    await message.answer(WELCOME, reply_markup=groups_keyboard(db.groups(message.chat.id)))


@command(Command("groups"))
async def cmd_groups(message: Message, db: Storage, config: Config) -> None:
    db.ensure_chat(message.chat.id, config.default_reminder)
    await message.answer("Оберіть групи:", reply_markup=groups_keyboard(db.groups(message.chat.id)))


@command(Command("reminder"))
async def cmd_reminder(message: Message, db: Storage, config: Config) -> None:
    db.ensure_chat(message.chat.id, config.default_reminder)
    await message.answer(
        "За скільки хвилин нагадувати перед вимкненням і увімкненням світла?",
        reply_markup=reminder_keyboard(db.reminder(message.chat.id)),
    )


@command(Command("today"))
async def cmd_today(message: Message, db: Storage, monitor: Monitor) -> None:
    text, markup = _day_view(db, monitor, message.chat.id, monitor.today(), all_groups=False)
    await message.answer(text, reply_markup=markup)


@command(Command("tomorrow"))
async def cmd_tomorrow(message: Message, db: Storage, monitor: Monitor) -> None:
    tomorrow = monitor.today() + timedelta(days=1)
    text, markup = _day_view(db, monitor, message.chat.id, tomorrow, all_groups=False)
    await message.answer(text, reply_markup=markup)


@command(Command("stop"))
async def cmd_stop(message: Message, db: Storage, bot: Bot) -> None:
    if not await can_manage(bot, message.chat, message.from_user, message.sender_chat):
        await message.answer(NOT_ADMIN)
        return
    db.delete_chat(message.chat.id)
    await message.answer("Підписку скасовано. /start — підписатися знову.")


@command(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.callback_query(GroupCallback.filter())
async def on_group(
    query: CallbackQuery, callback_data: GroupCallback, db: Storage, config: Config, bot: Bot
) -> None:
    message = query.message
    if not isinstance(message, Message):
        await _answer(query)
        return
    if not await can_manage(bot, message.chat, query.from_user):
        await _answer(query, NOT_ADMIN, alert=True)
        return
    chat_id = message.chat.id
    db.ensure_chat(chat_id, config.default_reminder)
    if callback_data.group == DONE:
        await _edit(message, format_subscription(db.groups(chat_id), db.reminder(chat_id)))
    elif callback_data.group in GROUPS:
        selected = db.toggle_group(chat_id, callback_data.group)
        with suppress(TelegramBadRequest):
            await message.edit_reply_markup(reply_markup=groups_keyboard(selected))
    await _answer(query)


@router.callback_query(ReminderCallback.filter())
async def on_reminder(
    query: CallbackQuery, callback_data: ReminderCallback, db: Storage, config: Config, bot: Bot
) -> None:
    message = query.message
    if not isinstance(message, Message) or callback_data.minutes not in REMINDER_OPTIONS:
        await _answer(query)
        return
    if not await can_manage(bot, message.chat, query.from_user):
        await _answer(query, NOT_ADMIN, alert=True)
        return
    db.ensure_chat(message.chat.id, config.default_reminder)
    db.set_reminder(message.chat.id, callback_data.minutes)
    await _edit(message, f"⏰ Нагадування: {format_reminder_setting(callback_data.minutes)}")
    await _answer(query)


@router.callback_query(DayCallback.filter())
async def on_all_groups(
    query: CallbackQuery, callback_data: DayCallback, db: Storage, monitor: Monitor
) -> None:
    if isinstance(query.message, Message):
        day = date.fromisoformat(callback_data.day)
        text, _ = _day_view(db, monitor, query.message.chat.id, day, all_groups=True)
        await _edit(query.message, text)
    await _answer(query)


@router.my_chat_member()
async def on_membership(event: ChatMemberUpdated, db: Storage, config: Config, bot: Bot) -> None:
    if event.new_chat_member.status in GONE_STATUSES:
        db.delete_chat(event.chat.id)  # removed from a chat, or blocked in private
    elif (
        event.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}
        and event.old_chat_member.status in GONE_STATUSES
    ):
        db.ensure_chat(event.chat.id, config.default_reminder)
        with suppress(TelegramAPIError):
            await bot.send_message(
                event.chat.id, WELCOME, reply_markup=groups_keyboard(db.groups(event.chat.id))
            )


@router.message(F.migrate_to_chat_id)
async def on_migrate(message: Message, db: Storage) -> None:
    if message.migrate_to_chat_id is not None:
        db.migrate_chat(message.chat.id, message.migrate_to_chat_id)
