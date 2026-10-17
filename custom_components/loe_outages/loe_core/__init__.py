"""Fetch and parse Lvivoblenergo (LOE) hourly outage schedules."""

from .client import LoeClient, LoeError
from .models import GROUPS, KYIV, DaySchedule, Outage, merge_outages, now_kyiv
from .parser import parse_day, parse_menu
from .timeline import (
    DayChange,
    Event,
    current_outage,
    diff_schedules,
    group_events,
    group_outages,
    merge_schedules,
    next_outage,
)

__all__ = [
    "GROUPS",
    "KYIV",
    "DayChange",
    "DaySchedule",
    "Event",
    "LoeClient",
    "LoeError",
    "Outage",
    "current_outage",
    "diff_schedules",
    "group_events",
    "group_outages",
    "merge_outages",
    "merge_schedules",
    "next_outage",
    "now_kyiv",
    "parse_day",
    "parse_menu",
]
