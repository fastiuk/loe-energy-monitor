"""Each source's parser against trimmed copies of real responses (October 2026)."""

import asyncio
import io
import struct
import time
import zlib
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from conftest import FIXTURES, fixture_json, fixture_text, kyiv, spans
from conftest import FakeHttp as RoutedHttp
from PIL import Image, ImageDraw

from ua_outages.http import FetchError
from ua_outages.models import KYIV, DaySchedule
from ua_outages.providers import (
    cherkasy,
    chernihiv,
    chernivtsi,
    dtek,
    kharkiv,
    khmelnytskyi,
    kirovohrad,
    loe,
    mykolaiv,
    poltava,
    prykarpattia,
    rivne,
    sumy,
    volyn,
    yasno,
    zaporizhzhia,
    zhytomyr,
)
from ua_outages.providers.base import SourceBlocked, SourceFormatError
from ua_outages.registry import STANDARD_GROUPS

OCT17, OCT18 = date(2026, 10, 17), date(2026, 10, 18)


def test_yasno():
    schedules = yasno.parse_planned(fixture_json("yasno_kyiv.json"))
    assert list(schedules) == [OCT18]  # tomorrow is still WaitingForSchedule
    s = schedules[OCT18]
    assert s.updated_at == datetime(2026, 10, 18, 12, 53, 46, tzinfo=UTC)
    assert spans(s, "1.1") == [("13:30", "17:00", False), ("20:30", "24:00", False)]
    assert spans(s, "3.1") == [("07:00", "10:30", False), ("15:30", "18:30", False), ("21:30", "24:00", False)]


def test_yasno_statuses():
    def entry(status, slots=()):
        return {"slots": list(slots), "date": "2026-10-18T00:00:00+03:00", "status": status}

    payload = {
        "1.1": {"today": entry("NoOutages"), "tomorrow": entry("WaitingForSchedule")},
        "1.2": {"today": entry("EmergencyShutdowns")},
        "2.1": {"today": entry("ScheduleApplies", [{"start": 600, "end": 660, "type": "Possible"}])},
        "2.2": {"today": entry("ScheduleApplies", [{"start": 600, "end": 9999, "type": "Definite"}])},
    }
    s = yasno.parse_planned(payload)[OCT18]
    assert s.groups["1.1"] == ()
    assert s.unknown["1.2"] == yasno.EMERGENCY and s.notes == (yasno.EMERGENCY,)
    assert spans(s, "2.1") == [("10:00", "11:00", True)]
    assert "2.2" in s.unknown
    with pytest.raises(SourceFormatError):
        yasno.parse_planned(["not", "a", "map"])


def test_dtek_fact():
    s = dtek.parse_fact(fixture_json("dtek_fact.json"))[OCT18]
    assert s.updated_at == kyiv(OCT18, 14, 59)
    assert sorted(s.groups) == sorted(STANDARD_GROUPS)
    # Hours 13–19 "no", 20 "first", 23 "second", 24 "no".
    assert spans(s, "1.1") == [("12:00", "19:30", False), ("22:30", "24:00", False)]


def test_dtek_page_and_cek_groups():
    fact = {
        "data": {"1792270800": {"GPV1.1": {str(h): "maybe" if h == 11 else "yes" for h in range(1, 25)},
                                 "GPV1003.2": {str(h): "no" if h == 1 else "yes" for h in range(1, 25)}}},
        "update": "18.10.2026 09:00",
        "today": 1792270800,
    }  # fmt: skip
    import json

    page = f"<script>DisconSchedule.fact = {json.dumps(fact)}\nDisconSchedule.x = 1</script>"
    dnem = dtek.parse_fact(dtek.extract_fact(page))[OCT18]
    assert spans(dnem, "1.1") == [("10:00", "11:00", True)]
    assert "3.2" not in dnem.groups
    cek = dtek.parse_fact(dtek.extract_fact(page), dtek.cek_groups)[OCT18]
    assert list(cek.groups) == ["3.2"] and spans(cek, "3.2") == [("00:00", "01:00", False)]
    assert dtek.parse_fact({"data": [], "update": "", "today": 0}) == {}
    with pytest.raises(SourceBlocked, match="Imperva"):
        dtek.extract_fact("<html>Request unsuccessful. Incapsula incident ID</html>")


def test_outage_data_ua_mirror():
    payload = {"lastUpdated": "2026-10-18T13:35:55.328Z", "lastUpdateStatus": {"ok": True}, "fact": fixture_json("dtek_fact.json")}
    assert list(dtek.parse_mirror(payload, kyiv(OCT18, 17))) == [OCT18]
    with pytest.raises(FetchError, match="refreshed"):
        dtek.parse_mirror(payload, kyiv(OCT18, 23))
    with pytest.raises(FetchError, match="failed"):
        dtek.parse_mirror({**payload, "lastUpdateStatus": {"ok": False, "code": 422}}, kyiv(OCT18, 17))


def test_loe():
    schedules = loe.parse_menu(fixture_json("loe_menu.json"))
    s = schedules[OCT17]
    assert s.updated_at == kyiv(OCT17, 10, 59)
    assert sorted(s.groups) == sorted(STANDARD_GROUPS)
    assert spans(s, "4.2") == [("10:00", "12:00", False), ("21:00", "22:30", False)]
    assert s.image_url == "https://api.loe.lviv.ua/media/6ad32af94506a_GPV.png"


def test_mykolaiv():
    schedules = mykolaiv.parse(
        fixture_json("mykolaiv_queues.json"), fixture_json("mykolaiv_series.json"), fixture_json("mykolaiv_active.json")
    )
    s = schedules[OCT17]
    assert sorted(s.groups) == sorted(STANDARD_GROUPS)
    assert spans(s, "1.1")[:3] == [("10:00", "10:30", True), ("10:30", "12:00", False), ("12:00", "12:30", True)]
    nothing = mykolaiv.parse(fixture_json("mykolaiv_queues.json"), fixture_json("mykolaiv_series.json"), [])
    assert nothing == {}


def test_sumy():
    s = sumy.parse(fixture_json("sumy.json"), kyiv(OCT18, 17))[OCT18]
    assert s.updated_at == kyiv(OCT18, 14, 30)
    assert spans(s, "1.1") == [("22:00", "24:00", False)]
    assert spans(s, "3.1") == [("14:00", "18:00", False), ("20:00", "22:00", False)]
    # A stamp from two days ago is not taken as today's schedule.
    assert sumy.parse(fixture_json("sumy.json"), kyiv(date(2026, 10, 20), 9)) == {}


def test_poltava():
    s = poltava.parse(fixture_text("poltava_2026-10-17.html"), OCT17)
    assert sorted(s.groups) == sorted(STANDARD_GROUPS)
    assert spans(s, "1.1")[:3] == [("00:00", "00:30", True), ("10:00", "11:30", False), ("11:30", "12:00", True)]
    assert poltava.parse("", OCT18) is None
    # A subqueue cell must be 1 or 2: anything else would become a made-up group.
    page = fixture_text("poltava_2026-10-17.html")
    marker = 'turnoff-scheduleui-table-subqueue">'
    at = page.index(marker) + len(marker)
    forged = poltava.parse(page[:at] + "1 <b>x</b>" + page[page.index("<", at) :], OCT17)
    assert sorted(forged.groups) == sorted(set(STANDARD_GROUPS) - {"1.1"})


def test_zhytomyr():
    schedules = zhytomyr.parse(fixture_text("zhytomyr.html"))
    s = schedules[OCT18]
    assert s.updated_at == kyiv(OCT18, 16, 30)
    assert sorted(s.groups) == sorted(STANDARD_GROUPS)
    assert spans(s, "1.2") == [("10:00", "12:00", False), ("15:00", "16:00", False), ("18:00", "20:00", False)]


def test_chernivtsi():
    page = fixture_text("chernivtsi.html")
    s = chernivtsi.parse(page, kyiv(OCT18, 18))
    assert s.day == OCT18 and s.updated_at == kyiv(OCT18, 17)
    assert sorted(s.groups, key=int) == [str(n) for n in range(1, 13)] and s.groups["1"] == ()

    # Group 1 without power 10:00–12:00, maybe 12:00–12:30.
    start = page.index('id="inf1"')
    row_end = page.index("</div>", start)
    cells = ["в" if 20 <= i < 24 else "мз" if i == 24 else "з" for i in range(48)]
    row = page[start : page.index(">", start) + 1] + "".join(f"<u>{c}</u>" for c in cells)
    s = chernivtsi.parse(page[:start] + row + page[row_end:], kyiv(OCT18, 18))
    assert spans(s, "1") == [("10:00", "12:00", False), ("12:00", "12:30", True)]


def test_cherkasy():
    posts = cherkasy.pick_posts(fixture_json("cherkasy_posts.json"), OCT17)
    assert posts[OCT18][0].endswith("na-18-zhovtnya-3")  # the newest of four updates
    assert posts[OCT17][0].endswith("na-17-zhovtnya-2")
    # Each picked post costs a request: days past tomorrow are never followed.
    far = [
        {"title": f"Графік погодинних відключень на {d} жовтня", "slug": f"post-{d}", "publishedAt": "2026-10-17T10:00:00"}
        for d in range(17, 32)
    ]
    assert sorted(cherkasy.pick_posts({"data": far}, OCT17)) == [OCT17, OCT18]
    s = cherkasy.parse_post(fixture_json("cherkasy_post.json"), OCT17, kyiv(OCT17, 2, 20))
    assert sorted(s.groups) == sorted(STANDARD_GROUPS)
    assert spans(s, "1.1") == [("10:00", "12:00", False), ("16:00", "18:00", False), ("20:00", "22:00", False)]
    assert spans(s, "2.1")[-1] == ("22:00", "24:00", False)


def test_kharkiv():
    schedules = kharkiv.parse_feed(fixture_text("kharkiv_rss.xml"), OCT17)
    s = schedules[OCT17]
    assert sorted(s.groups) == sorted(STANDARD_GROUPS)
    assert spans(s, "1.1") == [("07:00", "10:30", False), ("17:30", "21:00", False)]
    assert spans(s, "6.2") == [("06:00", "07:00", False), ("14:00", "17:30", False)]
    assert kharkiv.parse_feed(fixture_text("kharkiv_rss.xml"), OCT18) == {}


def test_zaporizhzhia():
    schedules = zaporizhzhia.parse(fixture_text("zaporizhzhia.html"), OCT17)
    s = schedules[OCT17]  # the newest update (18:52), not the earlier ones
    assert sorted(s.groups) == sorted(STANDARD_GROUPS)
    assert spans(s, "1.1") == [("10:30", "13:30", False), ("17:00", "18:30", False)]
    not_planned = zaporizhzhia.parse(fixture_text("zaporizhzhia.html"), date(2026, 10, 6))
    assert not_planned[date(2026, 10, 6)].groups == dict.fromkeys(STANDARD_GROUPS, ())


def test_rivne():
    page = fixture_text("rivne.html")
    assert rivne.parse(page) == {}  # every cell still says "Очікується"
    page = page.replace("Очікується", "08:00-10:00</p><p>20:00-22:00", 1).replace("Очікується", "", 1)
    s = rivne.parse(page)[OCT18]
    assert s.updated_at == kyiv(OCT18, 16, 30)
    assert spans(s, "1.1") == [("08:00", "10:00", False), ("20:00", "22:00", False)]
    assert s.groups["1.2"] == ()
    assert not s.mentions("2.1")


def test_prykarpattia():
    schedules: dict = {}
    prykarpattia.merge_response(
        [{"eventDate": "17.10.2026", "scheduleApprovedSince": "17.10.2026 18:54",
          "queues": {"1.1": [{"from": "10:00", "to": "12:00", "status": 1}], "1.2": []}}],
        schedules,
    )  # fmt: skip
    s = schedules[OCT17]
    assert s.updated_at == kyiv(OCT17, 18, 54)
    assert spans(s, "1.1") == [("10:00", "12:00", False)]
    assert s.groups["1.2"] == ()
    assert not s.mentions("2.1")


# Volyn: a synthetic PNG drawn to the operator's layout (measured on real images, 2026).
VOLYN_ON, VOLYN_OFF = (185, 200, 215), (68, 110, 155)


def volyn_png(off=(), other=(), shift=(0, 0), rows=12, size=(1750, 2250)) -> bytes:
    """``off``/``other``: (group index 0–11, half-hour slot 0–47) cells to paint dark / yellow."""
    dx, dy = shift
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)

    def box(x0, y0, x1, y1, color):
        draw.rectangle((x0 + dx, y0 + dy, x1 - 1 + dx, y1 - 1 + dy), fill=color)

    box(960, 25, 1730, 185, VOLYN_OFF)  # the summary table, which is not part of the grid
    columns = [(176 + 65 * k, 238 + 65 * k) for k in range(24)]
    for half, top in enumerate((350, 1241)):
        for x0, x1 in [(15, 80), (83, 173), *columns]:
            box(x0, top, x1, top + 67, VOLYN_OFF)
            box(x0 + 20, top + 20, x1 - 20, top + 40, "white")  # header text
        row_spans = [(top + 70 + 60 * r, top + 127 + 60 * r) for r in range(rows)]
        for q in range(rows // 2):
            box(15, row_spans[2 * q][0], 80, row_spans[2 * q + 1][1], VOLYN_ON)  # merged "Черга" cell
        for r, (y0, y1) in enumerate(row_spans):
            box(83, y0, 173, y1, VOLYN_ON)
            for k, (x0, x1) in enumerate(columns):
                slot = 24 * half + k
                color = VOLYN_OFF if (r, slot) in off else (255, 220, 0) if (r, slot) in other else VOLYN_ON
                box(x0, y0, x1, y1, color)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def volyn_day(png: bytes) -> DaySchedule:
    groups, unknown = volyn.parse_image(png, OCT18)
    return DaySchedule(OCT18, groups=groups, unknown=unknown)


VOLYN_OFF_CELLS = {(0, s) for s in range(20, 28)} | {(5, 0), (11, 46), (11, 47)}


@pytest.mark.parametrize("shift", [(0, 0), (6, -9)])
def test_volyn_image(shift):
    s = volyn_day(volyn_png(VOLYN_OFF_CELLS, shift=shift))
    assert sorted(s.groups) == sorted(STANDARD_GROUPS) and not s.unknown
    assert spans(s, "1.1") == [("10:00", "14:00", False)]  # across the two grids
    assert spans(s, "3.2") == [("00:00", "00:30", False)]
    assert spans(s, "6.2") == [("23:00", "24:00", False)]
    assert all(s.groups[g] == () for g in STANDARD_GROUPS if g not in ("1.1", "3.2", "6.2"))


def test_volyn_unknown_colour():
    s = volyn_day(volyn_png(other={(2, 30)}))
    assert s.unknown == {"2.1": volyn.UNRECOGNISED} and len(s.groups) == 11


def _png_header(width: int, height: int, pixels: bytes = b"") -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


@pytest.mark.parametrize(
    "png, error",
    [
        (b"<html><body>Just a moment...</body></html>", "not a readable PNG"),
        (b"GIF89a" + bytes(100), "not a readable PNG"),
        (b"\x89PNG\r\n\x1a\n" + bytes(3 * 1024 * 1024), "bytes, over"),
        (_png_header(100_000, 100_000), "too large"),
        (_png_header(3000, 3000), "3000×3000, expected 1750×2250"),  # rejected from the header alone
        (_png_header(1000, 1000), "1000×1000, expected 1750×2250"),
        (_png_header(1750, 2250), "not a readable PNG"),  # no pixel data
        # 20 MB of zeros squeezed into 20 KB: inflated only as far as the image needs.
        (_png_header(1750, 2250, bytes(20_000_000)), "table columns"),
        (volyn_png(size=(1750, 2251)), "1750×2251"),
        (volyn_png(rows=10), "two grids of 13 rows"),
    ],
)
def test_volyn_hostile_images(png, error):
    start = time.perf_counter()
    with pytest.raises(SourceFormatError, match=error):
        volyn.parse_image(png, OCT18)
    assert time.perf_counter() - start < 2


def test_volyn_grid_colours_are_checked():
    png = volyn_png()
    image = Image.open(io.BytesIO(png))
    image.paste((200, 60, 60), (176, 350, 1733, 417))  # a red header: not the layout we know
    out = io.BytesIO()
    image.save(out, "PNG")
    with pytest.raises(SourceFormatError, match="unexpected colours"):
        volyn.parse_image(out.getvalue(), OCT18)


def test_volyn_options_and_archive():
    assert volyn.parse_options(fixture_json("volyn_options.json")) == ["/media/2026/10/6ad3c89bb9049_GPV.png"]
    versions = volyn.parse_archive(fixture_json("volyn_archive.json"))
    uploaded = datetime(2026, 10, 17, 22, 12, 27, tzinfo=KYIV)  # 19:12:27 UTC
    assert versions["/media/2026/10/6ad3c89bb9049_GPV.png"] == (618, OCT18, uploaded)
    assert versions["/media/2026/10/6ad3988b22570_GPV.png"].day == OCT17

    def options(value):
        return {"hydra:member": [{"option_key": "pw_gpv_image_today", "value": value}]}

    assert volyn.parse_options(options("")) == []
    for hostile in ("//evil.example/a.png", "@evil.example/a.png", "https://evil.example/a.png", "/a b.png", 5):
        with pytest.raises(SourceFormatError, match="plain path"):
            volyn.parse_options(options(hostile))
    for broken in ({"hydra:member": []}, {"hydra:member": [{}] * 1000}, ["not", "hydra"]):
        with pytest.raises(SourceFormatError):
            volyn.parse_options(broken)


class FakeHttp:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    async def get(self, url, *, params=None, headers=None):
        self.calls.append((url, params))
        payload = self.responses[url.split("?")[0]]
        return SimpleNamespace(json=lambda: payload, body=payload)


def test_volyn_fetch():
    base = volyn.BASE_URL
    today, tomorrow, old = "/media/2026/10/today_GPV.png", "/media/2026/10/tomorrow_GPV.png", "/media/2026/10/old_GPV.png"
    archive = [
        {"id": 3, "dateGraph": "2026-10-18T00:00:00+00:00", "imageUrl": {"contentUrl": today, "uploadTimestamp": "2026-10-17T19:12:27+00:00"}},
        {"id": 2, "dateGraph": "2026-10-18T00:00:00+00:00", "imageUrl": {"contentUrl": tomorrow}},
        {"id": 1, "dateGraph": "2026-10-17T00:00:00+00:00", "imageUrl": {"contentUrl": old}},
    ]  # fmt: skip
    http = FakeHttp({
        f"{base}/api/options": {"hydra:member": [{"option_key": "pw_gpv_image_today", "value": today},
                                                 {"option_key": "pw_gpv_image_tomorrow", "value": tomorrow}]},
        f"{base}/api/archive_gpv_graphs": {"hydra:member": archive},
        base + today: volyn_png(VOLYN_OFF_CELLS),
    })  # fmt: skip
    source = volyn.Volyn()
    schedules = asyncio.run(source.fetch(http, kyiv(OCT17, 22, 30)))
    # "today" already shows the 18th; the older upload for the same day loses to it.
    assert list(schedules) == [OCT18]
    s = schedules[OCT18]
    assert s.updated_at == datetime(2026, 10, 17, 22, 12, 27, tzinfo=KYIV) and s.image_url == base + today
    assert spans(s, "1.1") == [("10:00", "14:00", False)]
    assert http.calls[1] == (f"{base}/api/archive_gpv_graphs", {"dateGraph[after]": "2026-10-16"})
    assert http.calls[0][0].endswith("?option_key[]=pw_gpv_image_today&option_key[]=pw_gpv_image_tomorrow")

    # The same upload is not downloaded again.
    http.calls.clear()
    assert asyncio.run(source.fetch(http, kyiv(OCT17, 22, 35))) == schedules
    assert [url.split("?")[0] for url, _ in http.calls] == [f"{base}/api/options", f"{base}/api/archive_gpv_graphs"]

    # An image the archive does not list (an older day still on show) is skipped.
    http.responses[f"{base}/api/archive_gpv_graphs"] = {"hydra:member": []}
    assert asyncio.run(source.fetch(http, kyiv(OCT18, 9))) == {}


def test_kirovohrad():
    schedules = kirovohrad.parse_channel(fixture_text("kirovohrad_tg.html"))
    assert list(schedules) == [date(2026, 4, 11), date(2026, 4, 13), date(2026, 6, 30), date(2026, 10, 17)]
    # The newest post for a day wins: 17.10 had eight, the 18:51 correction is the schedule.
    s = schedules[date(2026, 10, 17)]
    assert s.updated_at == datetime(2026, 10, 17, 18, 51, 24, tzinfo=KYIV)
    assert sorted(s.groups) == sorted(STANDARD_GROUPS) and s.unknown == {}
    assert spans(s, "1.1") == [("12:00", "14:00", False), ("20:00", "22:00", False)]
    assert spans(s, "6.1") == [("11:00", "12:00", False), ("14:30", "16:00", False)]
    # Summer format: hours only, "-" for none; the forecast posted in between is not a schedule.
    june = schedules[date(2026, 6, 30)]
    assert spans(june, "1.1") == [("17:00", "18:00", False)] and spans(june, "1.2") == []
    # A cancellation: no outages for anyone; a change for tomorrow posted the evening before.
    assert not any(schedules[date(2026, 4, 13)].groups.values())
    assert spans(schedules[date(2026, 4, 11)], "5.1") == [("06:00", "08:00", False)]


def test_kirovohrad_posts():
    published = datetime(2026, 10, 17, 9, 52, tzinfo=KYIV)
    head = ["17.10.2026 - Графік погодинних відключень", 'За розпорядженням НЕК "Укренерго" 17.10.2026 буде діяти наступний графік:']
    # Since October groups without outages are left out: they have none.
    s = kirovohrad.parse_post([*head, "Черга 1.1: 12:00-14:00", "Черга 4.1: 22:00-24:00"], published)
    assert spans(s, "1.1") == [("12:00", "14:00", False)] and spans(s, "4.1") == [("22:00", "24:00", False)]
    assert spans(s, "6.2") == [] and sorted(s.groups) == sorted(STANDARD_GROUPS)
    # An interval it can't read makes the group unknown, not empty.
    s = kirovohrad.parse_post([*head, "Черга 2.1: з обіду", "Черга 2.2: 18-16", "Черга 3.1: 10:00-12:00 і ще"], published)
    assert sorted(s.unknown) == ["2.1", "2.2", "3.1"] and "2.1" not in s.groups
    assert kirovohrad.parse_post(["Штормове попередження", "Черга 1.1: 10-12"], published) is None
    forecast = ['Попередній прогноз на ВІВТОРОК, 30 червня 2026 року.', 'Прогнозовано за розпорядженням НЕК "Укренерго" 30.06.2026 буде діяти', "Черга 1.1: 16-18"]
    assert kirovohrad.parse_post(forecast, published) is None
    assert kirovohrad.parse_post(['"Укренерго" 31.02.2026 буде діяти', "Черга 1.1: 10-12"], published) is None
    with pytest.raises(SourceFormatError):
        kirovohrad.parse_channel("<html><body>Just a moment...</body></html>")
    with pytest.raises(ValueError):
        kirovohrad.Kirovohrad("../evil")


def test_khmelnytskyi_news_and_post():
    assert khmelnytskyi.parse_news(fixture_text("khmelnytskyi_news.html")) == [
        ("/post/pogodinni-vidkljuchennja-na-17-zhovtnja.html", date(2026, 10, 17))
    ]
    # Three versions on 17.10; the newest name (upload time in Kyiv) is the schedule in force.
    path, uploaded, correction = khmelnytskyi.parse_post(fixture_text("khmelnytskyi_post.html"))
    assert path == "/Content/Uploads/2026/10/file20261017164437203.png"
    assert uploaded == datetime(2026, 10, 17, 16, 44, 37, tzinfo=KYIV) and correction is None


def test_khmelnytskyi_dates_and_corrections():
    article = (
        '<article class="item clearfix"><div class="heading"><a href="/post/pogodinni-vidkljuchennja-na-1-sichnja.html">'
        "Погодинні відключення на 1 січня</a></div><div class=\"item-footer\"><span>31 Гру 2026</span></div></article>"
        '<article class="item clearfix"><div class="heading"><a href="//evil.example/x.html">Погодинні відключення на 2 січня</a>'
        "</div><span>31 Гру 2026</span></article>"
    )
    assert khmelnytskyi.parse_news(article) == [("/post/pogodinni-vidkljuchennja-na-1-sichnja.html", date(2027, 1, 1))]
    post = (
        '<div class="post"><p>Зміни:<br />- підчерга 2.1. - відключення триватимуть з 16:00 до 17:00</p><hr />'
        '<p><img src="/Content/Uploads/2026/10/file20261017104940210.png" /></p><hr />'
        '<p><img src="/Content/Uploads/2026/10/file20261017100602968.png" /></p>'
        '<p><img src="https://evil.example/Content/Uploads/2026/10/file20991231235959999.png" /></p></div>'
    )
    path, uploaded, correction = khmelnytskyi.parse_post(post)
    assert path.endswith("file20261017104940210.png") and uploaded.hour == 10
    assert correction == "Після останнього графіка оператор змінив: - підчерга 2.1. - відключення триватимуть з 16:00 до 17:00"
    assert khmelnytskyi.parse_post('<div class="post"><p>Графік буде згодом</p></div>') == (None, None, None)
    with pytest.raises(SourceFormatError):
        khmelnytskyi.parse_news("<html>Just a moment...</html>")


@pytest.mark.parametrize(
    ("name", "day", "expected"),
    [
        # The web template (from June 2026): 1920×1021, two label columns.
        ("khmelnytskyi_2026-10-17.png", date(2026, 10, 17), {
            "1.1": [("16:00", "19:00", False)],
            "3.1": [("10:00", "13:00", False), ("19:00", "22:00", False)],
            "6.2": [("14:00", "16:00", False)],
        }),
        # The spreadsheet template (until spring 2026): 1920×1126, merged header, uneven rows.
        ("khmelnytskyi_2026-02-26.png", date(2026, 2, 26), {
            "1.1": [("22:00", "24:00", False)],
            "4.2": [],
            "6.1": [("11:00", "13:00", False)],
        }),
    ],
)  # fmt: skip
def test_khmelnytskyi_image(name, day, expected):
    groups, unknown = khmelnytskyi.parse_image((FIXTURES / name).read_bytes(), day)
    assert sorted(groups) == sorted(STANDARD_GROUPS) and unknown == {}
    schedule = DaySchedule(day, None, groups)
    for group, outages in expected.items():
        assert spans(schedule, group) == outages


def _recoloured(name: str, box, color) -> bytes:
    image = Image.open(FIXTURES / name).convert("RGB")
    ImageDraw.Draw(image).rectangle(box, fill=color)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_khmelnytskyi_unknown_cells_and_hostile_images():
    # A cell of a colour the legend does not have: that group is unknown, the rest is read.
    groups, unknown = khmelnytskyi.parse_image(
        _recoloured("khmelnytskyi_2026-10-17.png", (700, 355, 740, 375), (200, 40, 40)), OCT17
    )
    assert unknown == {"2.1": khmelnytskyi.UNRECOGNISED} and len(groups) == 11
    for png, error in [
        (_png_header(1107, 140), "1107×140"),  # a screenshot of a text correction
        (_png_header(1059, 1200), "1059×1200"),  # an archive snapshot
        (_png_header(1920, 30000), "1920×30000"),
        (b"GIF89a" + b"\0" * 100, "not a readable PNG"),
        (_recoloured("khmelnytskyi_2026-10-17.png", (0, 0, 1919, 1020), (255, 255, 255)), "frame"),
    ]:
        start = time.perf_counter()
        with pytest.raises(SourceFormatError, match=error):
            khmelnytskyi.parse_image(png, OCT17)
        assert time.perf_counter() - start < 2


def test_khmelnytskyi_fetch_reads_today_and_tomorrow_and_reuses_images():
    png = (FIXTURES / "khmelnytskyi_2026-10-17.png").read_bytes()
    http = RoutedHttp({
        "/post/novini-kompaniji": fixture_text("khmelnytskyi_news.html"),
        "/post/pogodinni-vidkljuchennja-na-17-zhovtnja.html": fixture_text("khmelnytskyi_post.html"),
        "/file20261017164437203.png": png,
    })  # fmt: skip
    source = khmelnytskyi.Khmelnytskyi()
    s = asyncio.run(source.fetch(http, kyiv(OCT17, 18)))[OCT17]
    assert s.image_url == "https://hoe.com.ua/Content/Uploads/2026/10/file20261017164437203.png"
    assert spans(s, "5.1") == [("13:00", "16:00", False)]
    asyncio.run(source.fetch(http, kyiv(OCT17, 18, 5)))
    assert sum(call.url.endswith(".png") for call in http.calls) == 1  # unchanged image: not again
    assert asyncio.run(source.fetch(http, kyiv(OCT18, 9))) == {}  # 17.10 is over: nothing to show


def test_chernihiv():
    schedules = chernihiv.parse_channel(fixture_text("chernihiv_tg.html"))
    assert list(schedules) == [date(2026, 10, 16), OCT17, OCT18]
    # Hours are only on the picture: every group says so, with the post that has the picture.
    s = schedules[OCT17]
    assert s.groups == {} and sorted(s.unknown) == sorted(STANDARD_GROUPS)
    assert s.image_url == "https://t.me/chernigivoblenergo/5556" and s.unknown["3.2"] == chernihiv.ON_PICTURE
    # Later posts' change lines go to the groups they name, in order.
    assert s.unknown["2.1"] == (
        chernihiv.ON_PICTURE + "; зміни: З 01:30 до 02:00 додатково відключаються черги 1/1, 1/2, 2/1, 6/1 та 6/2; "
        "До 03:00 продовжується відключення для черг 2/1, 2/2, 6/1 та 6/2"
    )
    assert s.updated_at == datetime(2026, 10, 17, 1, 42, 41, tzinfo=KYIV)
    # «В оновленому графіку…» brings a new picture: changes before it are void, later ones count.
    october16 = schedules[date(2026, 10, 16)]
    assert october16.image_url.endswith("/5551")
    assert october16.unknown["6.2"].endswith("зміни: З 17:00 додатково відключається черга 6/2")
    assert schedules[OCT18].unknown["1.1"].endswith("До 11:30 додатково відключаються черги 1/1 і 1/2")


def test_chernihiv_posts():
    def page(*posts):
        items = "".join(
            f'<div class="tgme_widget_message" data-post="chernigivoblenergo/{n}"><div class="tgme_widget_message_text">'
            f'{text}</div><time datetime="{when}"></time></div>'
            for n, when, text in posts
        )
        return f'<section class="tgme_channel_history">{items}</section>'

    # Around New Year the day is the nearest one: 1 січня posted on 31.12 is next year's.
    s = chernihiv.parse_channel(page((1, "2026-12-31T19:00:00+00:00", "1 січня в області діятиме Графік погодинних відключень")))
    assert list(s) == [date(2027, 1, 1)]
    # Lines without an hour or a group, and posts about other things, change nothing.
    s = chernihiv.parse_channel(page(
        (1, "2026-10-16T18:00:00+00:00", "17 жовтня в області діятиме Графік погодинних відключень"),
        (2, "2026-10-17T06:00:00+00:00", "маємо зміни у Графіку погодинних відключень на 17 жовтня<br/>Просимо бути ощадливими, черга 1/1<br/>🕗 З 10:00 зміни для всіх"),
        (3, "2026-10-17T07:00:00+00:00", "Знеструмлено 4,8 тисячі абонентів, черга 2/1 о 10:00"),
    ))  # fmt: skip
    assert set(s[OCT17].unknown.values()) == {chernihiv.ON_PICTURE}
    with pytest.raises(SourceFormatError):
        chernihiv.parse_channel("<html>Just a moment...</html>")
    later = asyncio.run(chernihiv.Chernihiv().fetch(RoutedHttp({"/s/chernigivoblenergo": fixture_text("chernihiv_tg.html")}), kyiv(OCT18, 9)))
    assert list(later) == [OCT18]  # only today and tomorrow
