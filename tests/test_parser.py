import json
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from loe_core import GROUPS, KYIV, DaySchedule, Outage, parse_day, parse_menu

FIXTURES = Path(__file__).parent / "fixtures"
HEADER = (
    "<p>Графік погодинних відключень на 26.11.2025</p>\n"
    "<p>Інформація станом на 18:45 25.11.2025</p>\n"
)


def at(day: date, hm: str, plus_days: int = 0) -> datetime:
    h, m = map(int, hm.split(":"))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=KYIV) + timedelta(days=plus_days)


def spans(schedule: DaySchedule, group: str) -> list[tuple[str, str]]:
    return [(f"{o.start:%H:%M}", f"{o.end:%H:%M}") for o in schedule.outages(group)]


def test_real_response():
    payload = json.loads((FIXTURES / "photo_grafic_2026-10-17.json").read_text(encoding="utf-8"))
    schedules = parse_menu(payload)

    assert list(schedules) == [date(2026, 10, 17)]  # "Tomorrow" is not published yet
    today = schedules[date(2026, 10, 17)]
    assert today.updated_at == datetime(2026, 10, 17, 10, 59, tzinfo=KYIV)
    assert sorted(today.groups) == list(GROUPS)
    assert today.unknown == {}
    assert today.notes == ()
    assert spans(today, "1.1") == [("20:00", "22:30")]
    assert spans(today, "4.2") == [("10:00", "12:00"), ("21:00", "22:30")]
    assert today.image_url == "https://api.loe.lviv.ua/media/6ad32af94506a_GPV.png"


def test_power_on_and_missing_comma():
    s = parse_day(
        HEADER
        + "<p>Група 1.1. Електроенергія є.</p>\n"
        + "<p>Група 2.1. Електроенергії немає з 08:00 до 10:30 з 14:00 до 17:00.</p>"
    )
    assert s is not None
    assert s.day == date(2025, 11, 26)
    assert s.updated_at == datetime(2025, 11, 25, 18, 45, tzinfo=KYIV)
    assert s.groups["1.1"] == ()
    assert spans(s, "2.1") == [("08:00", "10:30"), ("14:00", "17:00")]
    assert not s.mentions("1.2")


def test_midnight_handling():
    s = parse_day(
        HEADER
        + "<p>Група 2.2. Електроенергії немає з 23:00 до 24:00.</p>\n"
        + "<p>Група 3.1. Електроенергії немає з 21:00 до 02:00.</p>"
    )
    day = date(2025, 11, 26)
    assert s.groups["2.2"] == (Outage(at(day, "23:00"), at(day, "00:00", plus_days=1)),)
    assert s.groups["3.1"] == (Outage(at(day, "21:00"), at(day, "02:00", plus_days=1)),)


def test_inline_tags_and_unclosed_paragraphs():
    s = parse_day(
        "<p><strong>Графік погодинних відключень на 26.11.2025</strong>\n"
        "<p><em>Інформація станом на 18:45 25.11.2025</em>\n"
        "<p>Група <span>1.1</span>. Електроенергія&nbsp;є.\n"
        "<p>Група 1.2. Електроенергії немає з 14:00 до 17:30."
    )
    assert s.groups == {"1.1": (), "1.2": s.groups["1.2"]}
    assert spans(s, "1.2") == [("14:00", "17:30")]


def test_overlapping_intervals_are_merged():
    s = parse_day(HEADER + "<p>Група 1.1. Електроенергії немає з 10:00 до 12:00, з 11:00 до 13:00.</p>")
    assert spans(s, "1.1") == [("10:00", "13:00")]


def test_unrecognised_sentence_is_kept_raw():
    s = parse_day(HEADER + "<p>Група 5.1. Графік уточнюється.</p><p>Додаткова інформація.</p>")
    assert s.groups == {}
    assert s.unknown == {"5.1": "Група 5.1. Графік уточнюється."}
    assert s.mentions("5.1")
    assert s.notes == ("Додаткова інформація.",)


@pytest.mark.parametrize("raw", ["", "<div></div>", "<p>Щось без дати</p>"])
def test_empty_or_headerless(raw):
    assert parse_day(raw) is None


def test_header_only():
    s = parse_day("<p>Графік погодинних відключень на 26.11.2025</p>")
    assert s.day == date(2025, 11, 26)
    assert s.groups == {} and s.updated_at is None


def test_menu_without_schedule_raises():
    with pytest.raises(ValueError):
        parse_menu({"hydra:member": [{"type": "other", "menuItems": []}]})
    with pytest.raises(ValueError):
        parse_menu({"error": "nope"})


def test_roundtrip_dict():
    s = parse_day(HEADER + "<p>Група 3.1. Електроенергії немає з 21:00 до 02:00.</p><p>Група 5.1. ?</p>")
    restored = DaySchedule.from_dict(json.loads(json.dumps(s.to_dict())))
    assert restored == s
    assert restored.groups["3.1"][0].start.tzinfo == KYIV
