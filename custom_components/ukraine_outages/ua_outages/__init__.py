"""Hourly power outage schedules (графіки погодинних відключень) for every region of Ukraine."""

from .http import FetchError, Fetcher
from .models import KYIV, DaySchedule, Outage, Slot, merge_outages, now_kyiv
from .providers.base import (
    AddressLookup,
    AddressSource,
    Choice,
    Provider,
    Resolution,
    SourceBlocked,
    SourceFormatError,
    Step,
)
from .registry import OPERATORS, REGIONS, Operator, Region, available_operators, operator, region
from .timeline import (
    DayChange,
    Event,
    combine_schedules,
    current_outage,
    diff_schedules,
    group_events,
    group_outages,
    merge_schedules,
    next_outage,
)

__all__ = [
    "KYIV",
    "AddressLookup",
    "AddressSource",
    "Choice",
    "OPERATORS",
    "REGIONS",
    "DayChange",
    "DaySchedule",
    "Event",
    "FetchError",
    "Fetcher",
    "Operator",
    "Outage",
    "Provider",
    "Region",
    "Resolution",
    "Slot",
    "SourceBlocked",
    "SourceFormatError",
    "Step",
    "available_operators",
    "combine_schedules",
    "current_outage",
    "diff_schedules",
    "group_events",
    "group_outages",
    "merge_outages",
    "merge_schedules",
    "next_outage",
    "now_kyiv",
    "operator",
    "region",
]
