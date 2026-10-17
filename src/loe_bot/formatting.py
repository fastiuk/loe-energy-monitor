"""Telegram message texts (HTML parse mode)."""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from html import escape

from loe_core import KYIV, DayChange, DaySchedule, Event, Outage

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "нд")


def fmt_day(day: date) -> str:
    return f"{day:%d.%m} ({WEEKDAYS[day.weekday()]})"


def fmt_time(moment: datetime, day: date | None = None) -> str:
    local = moment.astimezone(KYIV)
    if day is not None and local.date() > day and local.time() == time(0):
        return "24:00"
    return f"{local:%H:%M}"


def fmt_outage(outage: Outage, day: date | None = None) -> str:
    return f"{fmt_time(outage.start, day)}–{fmt_time(outage.end, day)}"


def fmt_duration(span: timedelta) -> str:
    hours, minutes = divmod(round(span.total_seconds() / 60), 60)
    parts = [f"{hours} год"] if hours else []
    if minutes or not hours:
        parts.append(f"{minutes} хв")
    return " ".join(parts)


def _describe(schedule: DaySchedule, group: str) -> tuple[str, str]:
    """Icon and plain description of a group's day."""
    if group in schedule.unknown:
        return "❔", escape(schedule.unknown[group])
    if group not in schedule.groups:
        return "❔", "немає даних"
    outages = schedule.groups[group]
    if not outages:
        return "🟢", "світло є"
    return "🔴", ", ".join(fmt_outage(o, schedule.day) for o in outages)


def group_line(schedule: DaySchedule, group: str) -> str:
    icon, text = _describe(schedule, group)
    line = f"{icon} <b>{group}</b> — {text}"
    if outages := schedule.outages(group):
        total = sum((o.duration for o in outages), timedelta())
        line += f" <i>({fmt_duration(total)})</i>"
    return line


def format_schedule(schedule: DaySchedule, groups: Iterable[str], *, title: str) -> str:
    lines = [f"📅 <b>{title} {fmt_day(schedule.day)}</b>"]
    if schedule.updated_at:
        lines.append(f"<i>станом на {schedule.updated_at.astimezone(KYIV):%H:%M %d.%m}</i>")
    lines.append("")
    lines += [group_line(schedule, g) for g in groups]
    if schedule.notes:
        lines += ["", *map(escape, schedule.notes)]
    if schedule.image_url:
        lines += ["", f'<a href="{escape(schedule.image_url)}">Графік картинкою</a>']
    return "\n".join(lines)


def format_change(change: DayChange, groups: Iterable[str]) -> str:
    schedule = change.schedule
    if change.previous is None:
        lines = [f"🆕 <b>Опубліковано графік на {fmt_day(schedule.day)}</b>"]
    else:
        lines = [f"🔄 <b>Змінено графік на {fmt_day(schedule.day)}</b>"]
    if schedule.updated_at:
        lines.append(f"<i>станом на {schedule.updated_at.astimezone(KYIV):%H:%M %d.%m}</i>")
    lines.append("")
    for group in groups:
        lines.append(group_line(schedule, group))
        if change.previous is not None:
            lines.append(f"      <s>було: {_describe(change.previous, group)[1]}</s>")
    return "\n".join(lines)


def format_reminder(group: str, event: Event, now: datetime) -> str:
    minutes = max(1, math.ceil((event.at - now).total_seconds() / 60))
    outage = event.outage
    if event.kind == "off":
        return (
            f"⚠️ <b>Група {group}</b>: через {minutes} хв вимкнення світла\n"
            f"🔴 {fmt_outage(outage)} ({fmt_duration(outage.duration)})"
        )
    return (
        f"💡 <b>Група {group}</b>: через {minutes} хв за графіком мають увімкнути світло "
        f"(о {fmt_time(outage.end)})"
    )


def format_reminder_setting(minutes: int | None) -> str:
    return f"за {minutes} хв" if minutes else "вимкнено"


def format_subscription(groups: Iterable[str], reminder: int | None) -> str:
    groups = sorted(groups)
    if not groups:
        return "Підписок немає. /groups — обрати групи."
    return (
        f"✅ Групи: <b>{', '.join(groups)}</b>\n"
        f"⏰ Нагадування: {format_reminder_setting(reminder)}\n\n"
        "Повідомлю, коли опублікують чи змінять графік, і нагадаю перед вимкненням "
        "та увімкненням світла.\n"
        "/today — графік на сьогодні, /tomorrow — на завтра."
    )
