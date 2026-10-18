"""Per-group view across days: merged outages, on/off events, and change detection."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Literal

from .models import DaySchedule, Outage, merge_outages


@dataclass(frozen=True)
class Event:
    kind: Literal["off", "on"]
    at: datetime
    outage: Outage


def group_outages(
    schedules: Iterable[DaySchedule], group: str, *, possible: bool = True
) -> tuple[Outage, ...]:
    """Outages of a group across days; 22:00–24:00 plus next day's 00:00–02:00 become one.

    ``possible=False`` leaves out outages the schedule only says may happen.
    """
    merged = merge_outages(o for schedule in schedules for o in schedule.outages(group))
    return merged if possible else tuple(o for o in merged if not o.possible)


def group_events(
    schedules: Iterable[DaySchedule], group: str, *, possible: bool = False
) -> list[Event]:
    events: list[Event] = []
    for outage in group_outages(schedules, group, possible=possible):
        events.append(Event("off", outage.start, outage))
        events.append(Event("on", outage.end, outage))
    return events


def current_outage(
    schedules: Iterable[DaySchedule], group: str, now: datetime, *, possible: bool = False
) -> Outage | None:
    outages = group_outages(schedules, group, possible=possible)
    return next((o for o in outages if o.start <= now < o.end), None)


def next_outage(
    schedules: Iterable[DaySchedule], group: str, now: datetime, *, possible: bool = False
) -> Outage | None:
    return next((o for o in group_outages(schedules, group, possible=possible) if o.start > now), None)


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

    A group missing from the new version is not reported: sources sometimes serve a
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


#: Days after today a schedule may cover. Sources publish today and tomorrow; anything far
#: beyond is a broken or hostile answer and would only pile up in memory.
DAYS_AHEAD = 2


def trim_schedules(
    schedules: Mapping[date, DaySchedule], *, today: date, groups: Iterable[str] | None = None
) -> dict[date, DaySchedule]:
    """Only days from ``today`` to ``DAYS_AHEAD`` after it and, if given, only ``groups``."""
    last = today + timedelta(days=DAYS_AHEAD)
    known = None if groups is None else frozenset(groups)
    trimmed = {}
    for day, schedule in schedules.items():
        if not today <= day <= last:
            continue
        if known is not None:
            schedule = replace(
                schedule,
                groups={g: o for g, o in schedule.groups.items() if g in known},
                unknown={g: t for g, t in schedule.unknown.items() if g in known},
            )
        trimmed[day] = schedule
    return dict(sorted(trimmed.items()))


def merge_schedules(
    old: Mapping[date, DaySchedule], new: Mapping[date, DaySchedule], *, since: date
) -> dict[date, DaySchedule]:
    """Combine stored and fresh schedules, keeping days from ``since`` to ``DAYS_AHEAD`` after it.

    Days and groups the fresh fetch lacks are kept from the stored version, so a
    temporarily empty page neither wipes reminders nor re-announces the schedule.
    """
    last = since + timedelta(days=DAYS_AHEAD)
    merged = {day: s for day, s in old.items() if since <= day <= last}
    for day, schedule in new.items():
        if not since <= day <= last:
            continue
        merged[day] = schedule.with_missing_from(old[day]) if day in old else schedule
    return dict(sorted(merged.items()))


def combine_schedules(parts: Iterable[Mapping[date, DaySchedule]]) -> dict[date, DaySchedule]:
    """One schedule per day from answers that each cover some groups (one address each)."""
    combined: dict[date, DaySchedule] = {}
    for part in parts:
        for day, schedule in part.items():
            if (other := combined.get(day)) is None:
                combined[day] = schedule
                continue
            stamps = [s.updated_at for s in (other, schedule) if s.updated_at]
            combined[day] = DaySchedule(
                day,
                max(stamps) if stamps else None,
                {**other.groups, **schedule.groups},
                {**other.unknown, **schedule.unknown},
                tuple(dict.fromkeys(other.notes + schedule.notes)),
                other.image_url or schedule.image_url,
            )
    return dict(sorted(combined.items()))
