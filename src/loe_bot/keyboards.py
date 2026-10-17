from __future__ import annotations

from collections.abc import Collection

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from loe_core import GROUPS

DONE = "done"
REMINDER_OPTIONS = (0, 5, 10, 15, 30, 60)


class GroupCallback(CallbackData, prefix="grp"):
    group: str


class ReminderCallback(CallbackData, prefix="rem"):
    minutes: int


class DayCallback(CallbackData, prefix="day"):
    day: str  # ISO date


def groups_keyboard(selected: Collection[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for group in GROUPS:
        text = f"✅ {group}" if group in selected else group
        builder.button(text=text, callback_data=GroupCallback(group=group))
    builder.button(text="Готово", callback_data=GroupCallback(group=DONE))
    builder.adjust(4, 4, 4, 1)
    return builder.as_markup()


def reminder_keyboard(current: int | None) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for minutes in REMINDER_OPTIONS:
        text = f"{minutes} хв" if minutes else "Вимкнути"
        if minutes == current:
            text = f"✅ {text}"
        builder.button(text=text, callback_data=ReminderCallback(minutes=minutes))
    builder.adjust(3)
    return builder.as_markup()


def all_groups_keyboard(day: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="Показати всі групи", callback_data=DayCallback(day=day))
    return builder.as_markup()
