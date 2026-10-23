"""Data model for hourly outage schedules (графіки погодинних відключень, ГПВ)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")


def now_kyiv() -> datetime:
    return datetime.now(KYIV)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    """Kyiv wall-clock time on ``day``; hour 24 (how schedules write midnight) is the next day."""
    extra_days, hour = divmod(hour, 24)
    return datetime.combine(day + timedelta(days=extra_days), time(hour, minute), KYIV)


def at_minute(day: date, minutes: int) -> datetime:
    """Kyiv time ``minutes`` after the start of ``day`` on the wall clock (1440 is midnight)."""
    return at(day, *divmod(minutes, 60))


@dataclass(frozen=True, order=True)
class Outage:
    """A power-off interval; both ends are tz-aware Kyiv datetimes.

    ``possible`` marks an outage the schedule only says may happen (grey or yellow cells,
    "можливе відключення").
    """

    start: datetime
    end: datetime
    possible: bool = False

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


def _join(outages: Iterable[Outage]) -> list[Outage]:
    joined: list[Outage] = []
    for outage in sorted(outages):
        if joined and outage.start <= joined[-1].end:
            if outage.end > joined[-1].end:
                joined[-1] = replace(joined[-1], end=outage.end)
        else:
            joined.append(outage)
    return joined


def _cut(outage: Outage, definite: Sequence[Outage]) -> list[Outage]:
    pieces = [outage]
    for d in definite:
        rest = []
        for piece in pieces:
            if d.end <= piece.start or d.start >= piece.end:
                rest.append(piece)
                continue
            if piece.start < d.start:
                rest.append(replace(piece, end=d.start))
            if d.end < piece.end:
                rest.append(replace(piece, start=d.end))
        pieces = rest
    return pieces


def merge_outages(outages: Iterable[Outage]) -> tuple[Outage, ...]:
    """Sort outages and join the ones that overlap or touch.

    A definite outage wins over a possible one: the possible one keeps only the time
    outside definite outages.
    """
    outages = list(outages)
    definite = _join(o for o in outages if not o.possible)
    possible = [p for o in _join(o for o in outages if o.possible) for p in _cut(o, definite)]
    return tuple(sorted(definite + possible))


class Slot(StrEnum):
    """State of one cell in a grid schedule."""

    ON = "on"
    OFF = "off"
    MAYBE = "maybe"


def outages_from_slots(day: date, slots: Sequence[Slot], *, minutes: int = 30) -> tuple[Outage, ...]:
    """Outages from consecutive cells of ``minutes`` each, starting at midnight of ``day``."""
    outages = []
    for i, slot in enumerate(slots):
        if slot is not Slot.ON:
            start = at_minute(day, i * minutes)
            end = at_minute(day, (i + 1) * minutes)
            outages.append(Outage(start, end, possible=slot is Slot.MAYBE))
    return merge_outages(outages)


#: What a group shows instead of hours while emergency outages (ГАВ) replace the schedule: the
#: operator's own word, not a parse failure.
EMERGENCY = "Діють аварійні відключення, графік не застосовується"
#: Texts in ``DaySchedule.unknown`` that explain the missing hours rather than report a failure.
EXPLANATIONS = frozenset({EMERGENCY})


@dataclass(frozen=True)
class DaySchedule:
    """The schedule an operator published for one calendar day."""

    day: date
    updated_at: datetime | None = None
    #: Groups the schedule lists. An empty tuple means power all day.
    groups: dict[str, tuple[Outage, ...]] = field(default_factory=dict)
    #: Groups whose data could not be parsed, mapped to the raw text.
    unknown: dict[str, str] = field(default_factory=dict)
    #: Other lines worth showing (e.g. "emergency outages apply").
    notes: tuple[str, ...] = ()
    image_url: str | None = None

    def outages(self, group: str) -> tuple[Outage, ...]:
        return self.groups.get(group, ())

    def mentions(self, group: str) -> bool:
        return group in self.groups or group in self.unknown

    def mentioned_groups(self) -> list[str]:
        return sorted(self.groups.keys() | self.unknown.keys(), key=group_sort_key)

    def unparsed_groups(self) -> list[str]:
        """Groups whose hours could not be read; not those the operator explains (``EXPLANATIONS``)."""
        return sorted((g for g, text in self.unknown.items() if text not in EXPLANATIONS), key=group_sort_key)

    def state(self, group: str) -> tuple[tuple[Outage, ...] | None, str | None]:
        """Comparable state of a group, used to detect schedule changes."""
        return self.groups.get(group), self.unknown.get(group)

    def with_missing_from(self, older: DaySchedule) -> DaySchedule:
        """Keep groups that this version dropped (e.g. a half-edited page) from an older one."""
        missing = [g for g in older.mentioned_groups() if not self.mentions(g)]
        if not missing:
            return self
        return replace(
            self,
            groups={**{g: older.groups[g] for g in missing if g in older.groups}, **self.groups},
            unknown={**{g: older.unknown[g] for g in missing if g in older.unknown}, **self.unknown},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "groups": {
                g: [
                    [o.start.isoformat(), o.end.isoformat(), *([True] if o.possible else [])]
                    for o in outages
                ]
                for g, outages in self.groups.items()
            },
            "unknown": self.unknown,
            "notes": list(self.notes),
            "image_url": self.image_url,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DaySchedule:
        def dt(value: str) -> datetime:
            return datetime.fromisoformat(value).astimezone(KYIV)

        return cls(
            day=date.fromisoformat(data["day"]),
            updated_at=dt(data["updated_at"]) if data.get("updated_at") else None,
            groups={
                g: tuple(Outage(dt(item[0]), dt(item[1]), len(item) > 2 and item[2]) for item in outages)
                for g, outages in data.get("groups", {}).items()
            },
            unknown=dict(data.get("unknown", {})),
            notes=tuple(data.get("notes", ())),
            image_url=data.get("image_url"),
        )


def group_sort_key(group: str) -> tuple[int, ...]:
    """Sort "2.1" before "10.1" and "1" before "12"."""
    try:
        return tuple(int(part) for part in group.split("."))
    except ValueError:
        return (1 << 30,)
