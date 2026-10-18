from datetime import date

from conftest import kyiv

from ua_outages.models import DaySchedule, Outage
from ua_outages.timeline import (
    current_outage,
    diff_schedules,
    group_events,
    group_outages,
    merge_schedules,
    next_outage,
)

DAY = date(2026, 10, 17)
TOMORROW = date(2026, 10, 18)


def schedule(day: date, groups: dict, updated_hour: int = 10) -> DaySchedule:
    return DaySchedule(day, kyiv(day, updated_hour), groups)


def off(day: date, h1: int, h2: int, *, possible: bool = False) -> Outage:
    end = kyiv(TOMORROW if h2 == 24 and day == DAY else day, 0 if h2 == 24 else h2)
    return Outage(kyiv(day, h1), end, possible)


def test_outage_across_midnight_is_one_event_pair():
    today = schedule(DAY, {"1.1": (off(DAY, 22, 24),)})
    tomorrow = schedule(TOMORROW, {"1.1": (off(TOMORROW, 0, 2),)})
    events = group_events([today, tomorrow], "1.1")
    assert [(e.kind, e.at) for e in events] == [("off", kyiv(DAY, 22)), ("on", kyiv(TOMORROW, 2))]


def test_possible_outages_are_left_out_of_events_by_default():
    s = schedule(DAY, {"1.1": (off(DAY, 9, 10, possible=True), off(DAY, 10, 12))})
    assert [(e.kind, e.at) for e in group_events([s], "1.1")] == [("off", kyiv(DAY, 10)), ("on", kyiv(DAY, 12))]
    assert len(group_outages([s], "1.1")) == 2
    assert len(group_outages([s], "1.1", possible=False)) == 1


def test_current_and_next_outage():
    s = schedule(DAY, {"4.2": (off(DAY, 10, 12), off(DAY, 21, 23))})
    assert current_outage([s], "4.2", kyiv(DAY, 11)).start == kyiv(DAY, 10)
    assert current_outage([s], "4.2", kyiv(DAY, 12)) is None
    assert next_outage([s], "4.2", kyiv(DAY, 12)).start == kyiv(DAY, 21)
    assert next_outage([s], "4.2", kyiv(DAY, 23)) is None


def test_diff_published_changed_unchanged():
    old = {DAY: schedule(DAY, {"1.1": (), "1.2": ()})}
    new = {
        DAY: schedule(DAY, {"1.1": (), "1.2": (off(DAY, 10, 12),)}, 11),
        TOMORROW: schedule(TOMORROW, {"1.1": ()}),
    }
    changes = diff_schedules(old, new)
    assert [(c.schedule.day, c.published, c.groups) for c in changes] == [
        (DAY, False, ("1.2",)),
        (TOMORROW, True, ("1.1",)),
    ]
    assert diff_schedules(new, new) == []


def test_diff_ignores_groups_dropped_from_a_half_edited_page():
    old = {DAY: schedule(DAY, {"1.1": (), "1.2": ()})}
    new = {DAY: schedule(DAY, {"1.1": ()})}
    assert diff_schedules(old, new) == []


def test_merge_keeps_stored_days_and_groups():
    old = {
        date(2026, 10, 16): schedule(date(2026, 10, 16), {"1.1": ()}),
        DAY: schedule(DAY, {"1.1": (), "1.2": (off(DAY, 10, 12),)}),
    }
    new = {DAY: schedule(DAY, {"1.1": (off(DAY, 8, 9),)})}
    merged = merge_schedules(old, new, since=DAY)
    assert list(merged) == [DAY]
    assert merged[DAY].groups["1.1"] == (off(DAY, 8, 9),)
    assert merged[DAY].groups["1.2"] == (off(DAY, 10, 12),)
