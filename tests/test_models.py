from datetime import date, timedelta

from conftest import kyiv

from ua_outages.models import DaySchedule, Outage, Slot, at, group_sort_key, merge_outages, outages_from_slots

DAY = date(2026, 10, 17)


def test_at_turns_24_00_into_next_midnight():
    assert at(DAY, 24) == kyiv(DAY + timedelta(days=1), 0)


def test_merge_joins_touching_and_overlapping():
    merged = merge_outages(
        [Outage(kyiv(DAY, 12), kyiv(DAY, 14)), Outage(kyiv(DAY, 10), kyiv(DAY, 12)), Outage(kyiv(DAY, 13), kyiv(DAY, 15))]
    )
    assert merged == (Outage(kyiv(DAY, 10), kyiv(DAY, 15)),)


def test_definite_outage_cuts_possible_one():
    merged = merge_outages(
        [Outage(kyiv(DAY, 9), kyiv(DAY, 13), possible=True), Outage(kyiv(DAY, 10), kyiv(DAY, 12))]
    )
    assert merged == (
        Outage(kyiv(DAY, 9), kyiv(DAY, 10), True),
        Outage(kyiv(DAY, 10), kyiv(DAY, 12)),
        Outage(kyiv(DAY, 12), kyiv(DAY, 13), True),
    )
    # Fully covered: the possible outage disappears.
    assert merge_outages(
        [Outage(kyiv(DAY, 10), kyiv(DAY, 11), possible=True), Outage(kyiv(DAY, 9), kyiv(DAY, 12))]
    ) == (Outage(kyiv(DAY, 9), kyiv(DAY, 12)),)


def test_outages_from_slots():
    slots = [Slot.ON] * 48
    slots[20:24] = [Slot.OFF] * 4  # 10:00–12:00
    slots[24] = Slot.MAYBE  # 12:00–12:30
    slots[47] = Slot.OFF  # 23:30–24:00
    assert outages_from_slots(DAY, slots) == (
        Outage(kyiv(DAY, 10), kyiv(DAY, 12)),
        Outage(kyiv(DAY, 12), kyiv(DAY, 12, 30), True),
        Outage(kyiv(DAY, 23, 30), kyiv(DAY + timedelta(days=1), 0)),
    )


def test_day_schedule_round_trip():
    schedule = DaySchedule(
        DAY,
        kyiv(DAY, 9, 15),
        {"1.1": (Outage(kyiv(DAY, 10), kyiv(DAY, 12)), Outage(kyiv(DAY, 12), kyiv(DAY, 13), True)), "1.2": ()},
        {"2.1": "незрозумілий рядок"},
        ("примітка",),
        "https://example.invalid/a.png",
    )
    assert DaySchedule.from_dict(schedule.to_dict()) == schedule


def test_with_missing_from_keeps_dropped_groups():
    old = DaySchedule(DAY, groups={"1.1": (), "1.2": ()}, unknown={"2.1": "?"})
    new = DaySchedule(DAY, groups={"1.1": (Outage(kyiv(DAY, 10), kyiv(DAY, 11)),)})
    merged = new.with_missing_from(old)
    assert merged.groups["1.1"] == new.groups["1.1"]
    assert merged.groups["1.2"] == ()
    assert merged.unknown == {"2.1": "?"}


def test_group_sort_key():
    assert sorted(["10.1", "2.1", "1.2", "1.1"], key=group_sort_key) == ["1.1", "1.2", "2.1", "10.1"]
    assert sorted(["12", "2", "1"], key=group_sort_key) == ["1", "2", "12"]
