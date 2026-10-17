"""Data model for LOE hourly outage schedules (графік погодинних відключень, ГПВ)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

KYIV = ZoneInfo("Europe/Kyiv")

#: All outage groups LOE publishes: 1.1, 1.2, 2.1, ..., 6.2.
GROUPS: tuple[str, ...] = tuple(f"{queue}.{sub}" for queue in range(1, 7) for sub in (1, 2))


def now_kyiv() -> datetime:
    return datetime.now(KYIV)


@dataclass(frozen=True, order=True)
class Outage:
    """A single power-off interval; both ends are tz-aware Kyiv datetimes."""

    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start


def merge_outages(outages: Iterable[Outage]) -> tuple[Outage, ...]:
    """Sort outages and join the ones that overlap or touch."""
    merged: list[Outage] = []
    for outage in sorted(outages):
        if merged and outage.start <= merged[-1].end:
            if outage.end > merged[-1].end:
                merged[-1] = Outage(merged[-1].start, outage.end)
        else:
            merged.append(outage)
    return tuple(merged)


@dataclass(frozen=True)
class DaySchedule:
    """The schedule LOE published for one calendar day."""

    day: date
    updated_at: datetime | None = None
    #: Groups the schedule lists. An empty tuple means power all day.
    groups: dict[str, tuple[Outage, ...]] = field(default_factory=dict)
    #: Groups whose sentence could not be parsed, mapped to its raw text.
    unknown: dict[str, str] = field(default_factory=dict)
    #: Lines that are neither the header nor a group sentence.
    notes: tuple[str, ...] = ()
    image_url: str | None = None

    def outages(self, group: str) -> tuple[Outage, ...]:
        return self.groups.get(group, ())

    def mentions(self, group: str) -> bool:
        return group in self.groups or group in self.unknown

    def mentioned_groups(self) -> list[str]:
        return sorted(self.groups.keys() | self.unknown.keys())

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
                g: [[o.start.isoformat(), o.end.isoformat()] for o in outages]
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
                g: tuple(Outage(dt(start), dt(end)) for start, end in outages)
                for g, outages in data.get("groups", {}).items()
            },
            unknown=dict(data.get("unknown", {})),
            notes=tuple(data.get("notes", ())),
            image_url=data.get("image_url"),
        )
