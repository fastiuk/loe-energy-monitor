from datetime import date, datetime

from loe_core import (
    KYIV,
    current_outage,
    diff_schedules,
    group_events,
    merge_schedules,
    next_outage,
    parse_day,
)

DAY = date(2026, 10, 17)


def schedule(day: date, *sentences: str, updated: str = "10:00"):
    raw = f"<p>Графік погодинних відключень на {day:%d.%m.%Y}</p>"
    raw += f"<p>Інформація станом на {updated} {day:%d.%m.%Y}</p>"
    raw += "".join(f"<p>{s}</p>" for s in sentences)
    return parse_day(raw)


def kyiv(day: date, h: int, m: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, h, m, tzinfo=KYIV)


TOMORROW = date(2026, 10, 18)


def test_outage_across_midnight_is_one_event_pair():
    today = schedule(DAY, "Група 1.1. Електроенергії немає з 22:00 до 24:00.")
    tomorrow = schedule(TOMORROW, "Група 1.1. Електроенергії немає з 00:00 до 02:00.")
    events = group_events([today, tomorrow], "1.1")
    assert [(e.kind, e.at) for e in events] == [
        ("off", kyiv(DAY, 22)),
        ("on", kyiv(TOMORROW, 2)),
    ]


def test_current_and_next_outage():
    s = schedule(DAY, "Група 4.2. Електроенергії немає з 10:00 до 12:00, з 21:00 до 22:30.")
    assert current_outage([s], "4.2", kyiv(DAY, 11)).start == kyiv(DAY, 10)
    assert current_outage([s], "4.2", kyiv(DAY, 12)) is None
    assert next_outage([s], "4.2", kyiv(DAY, 12)).start == kyiv(DAY, 21)
    assert next_outage([s], "4.2", kyiv(DAY, 23)) is None


def test_diff_published_changed_unchanged():
    old = {DAY: schedule(DAY, "Група 1.1. Електроенергія є.", "Група 1.2. Електроенергія є.")}
    new = {
        DAY: schedule(
            DAY,
            "Група 1.1. Електроенергія є.",
            "Група 1.2. Електроенергії немає з 10:00 до 12:00.",
            updated="11:00",
        ),
        TOMORROW: schedule(TOMORROW, "Група 1.1. Електроенергія є."),
    }
    changes = diff_schedules(old, new)
    assert [(c.schedule.day, c.published, c.groups) for c in changes] == [
        (DAY, False, ("1.2",)),
        (TOMORROW, True, ("1.1",)),
    ]


def test_diff_ignores_timestamp_only_update():
    old = {DAY: schedule(DAY, "Група 1.1. Електроенергія є.")}
    new = {DAY: schedule(DAY, "Група 1.1. Електроенергія є.", updated="12:00")}
    assert diff_schedules(old, new) == []


def test_half_edited_page_is_neither_reported_nor_stored():
    full = schedule(
        DAY,
        "Група 1.1. Електроенергії немає з 10:00 до 12:00.",
        "Група 1.2. Електроенергії немає з 14:00 до 16:00.",
    )
    partial = schedule(DAY, "Група 1.1. Електроенергії немає з 10:00 до 12:00.")
    assert diff_schedules({DAY: full}, {DAY: partial}) == []
    merged = merge_schedules({DAY: full}, {DAY: partial}, since=DAY)
    assert merged[DAY].groups == full.groups
    # When 1.2 comes back unchanged there is nothing to report.
    assert diff_schedules(merged, {DAY: full}) == []


def test_merge_keeps_missing_days_and_drops_past():
    yesterday = date(2026, 10, 16)
    old = {
        yesterday: schedule(yesterday, "Група 1.1. Електроенергія є."),
        TOMORROW: schedule(TOMORROW, "Група 1.1. Електроенергія є."),
    }
    new = {DAY: schedule(DAY, "Група 1.1. Електроенергія є.")}
    assert list(merge_schedules(old, new, since=DAY)) == [DAY, TOMORROW]
