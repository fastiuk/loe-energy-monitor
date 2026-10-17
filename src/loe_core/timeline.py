"""Per-group view across days: merged outages, on/off events, and change detection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from .models import DaySchedule, Outage, merge_outages


@dataclass(frozen=True)
class Event:
    kind: Literal["off", "on"]
    at: datetime
    outage: Outage


def group_outages(schedules: Iterable[DaySchedule], group: str) -> tuple[Outage, ...]:
    """Outages of a group across days; 22:00–24:00 plus next day's 00:00–02:00 become one."""
    return merge_outages(o for schedule in schedules for o in schedule.outages(group))


def group_events(schedules: Iterable[DaySchedule], group: str) -> list[Event]:
    events: list[Event] = []
    for outage in group_outages(schedules, group):
        events.append(Event("off", outage.start, outage))
        events.append(Event("on", outage.end, outage))
    return events


def current_outage(schedules: Iterable[DaySchedule], group: str, now: datetime) -> Outage | None:
    return next((o for o in group_outages(schedules, group) if o.start <= now < o.end), None)


def next_outage(schedules: Iterable[DaySchedule], group: str, now: datetime) -> Outage | None:
    return next((o for o in group_outages(schedules, group) if o.start > now), None)


@dataclass(frozen=True)
class DayChange:
    schedule: DaySchedule
    #: The version we had before; ``None`` when the day was just published.
    previous: DaySchedule | None
    #: Groups whose outages changed; every listed group when the day was just published.
    groups: tuple[str, ...]

    @property
    def published(self) -> bool:
        return self.previous is None


def diff_schedules(
    old: Mapping[date, DaySchedule], new: Mapping[date, DaySchedule]
) -> list[DayChange]:
    """What changed between two fetches.

    A group missing from the new version is not reported: LOE sometimes serves a
    half-edited page, and the group comes back unchanged on the next fetch.
    """
    changes = []
    for day in sorted(new):
        current, previous = new[day], old.get(day)
        if previous is None:
            changes.append(DayChange(current, None, tuple(current.mentioned_groups())))
            continue
        changed = tuple(
            g
            for g in current.mentioned_groups()
            if previous.mentions(g) and previous.state(g) != current.state(g)
        )
        if changed:
            changes.append(DayChange(current, previous, changed))
    return changes


def merge_schedules(
    old: Mapping[date, DaySchedule], new: Mapping[date, DaySchedule], *, since: date
) -> dict[date, DaySchedule]:
    """Combine stored and fresh schedules, dropping days before ``since``.

    Days and groups the fresh fetch lacks are kept from the stored version, so a
    temporarily empty page neither wipes reminders nor re-announces the schedule.
    """
    merged = {day: s for day, s in old.items() if day >= since}
    for day, schedule in new.items():
        if day < since:
            continue
        merged[day] = schedule.with_missing_from(old[day]) if day in old else schedule
    return dict(sorted(merged.items()))
