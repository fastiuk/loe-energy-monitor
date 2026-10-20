"""Sumyoblenergo's ГПВ news posts: finding the posts and reading the table images (October 2026)."""

import asyncio
import io
import json
import struct
import time
import zlib
from datetime import date, datetime

import pytest
from conftest import FIXTURES, FakeHttp, fixture_text, kyiv, spans
from PIL import Image, ImageDraw

from ua_outages.http import FetchError
from ua_outages.models import KYIV, DaySchedule
from ua_outages.providers import sumy_news
from ua_outages.providers.base import SourceFormatError

OCT18, OCT19, OCT20 = date(2026, 10, 18), date(2026, 10, 19), date(2026, 10, 20)
GROUPS = sumy_news.GROUPS
PNG18 = (FIXTURES / "sumy_news_2026-10-18.png").read_bytes()
PNG20 = (FIXTURES / "sumy_news_2026-10-20.png").read_bytes()
IMAGE20 = "9998792d25a3c326e65b071b214e14d5"  # post 445, «на 20-10-2026»
IMAGE19 = "e7d32bfa5d1468f04c037a1d5fe7000d"  # post 440, «на 19-10-2026» (not a fixture)
IMAGE18 = "f76ea73f31aea494c1b5355dea3c71f7"  # post 442, the last change for 18.10


def _day(png: bytes, day: date):
    groups, unknown = sumy_news.parse_image(png, day)
    return DaySchedule(day, None, groups, unknown)


def _fast(fn, *args, limit: float = 2.0):
    start = time.perf_counter()
    try:
        return fn(*args)
    finally:
        assert time.perf_counter() - start < limit


# --- Reading the images ----------------------------------------------------------------------


def test_image_of_20_october():
    s = _day(PNG20, OCT20)
    assert sorted(s.groups) == sorted(GROUPS) and s.unknown == {}
    assert spans(s, "1.1") == [("08:00", "12:00", False)]
    assert spans(s, "1.2") == []  # «—»
    assert spans(s, "2.1") == [("22:00", "24:00", False)]  # «22:00 - 00:00»: to midnight
    assert spans(s, "2.2") == [("20:00", "24:00", False)]
    assert spans(s, "4.1") == [("14:00", "18:00", False), ("20:00", "22:00", False)]
    assert spans(s, "6.2") == [("06:00", "08:00", False), ("10:00", "14:00", False)]


def test_image_of_18_october():
    s = _day(PNG18, OCT18)
    assert sorted(s.groups) == sorted(GROUPS) and s.unknown == {}
    assert spans(s, "1.1") == []
    assert spans(s, "1.2") == [("20:30", "22:00", False)]
    assert spans(s, "2.1") == [("18:00", "20:00", False), ("20:30", "21:30", False)]
    assert spans(s, "3.1") == [("14:00", "18:00", False), ("20:30", "21:00", False)]
    # Three boxes, the first of them half an hour («0 год. 30 хв.»).
    assert spans(s, "5.1") == [("09:30", "10:00", False), ("12:00", "16:00", False), ("18:00", "20:00", False)]
    assert spans(s, "6.2") == [("09:30", "10:00", False)]


def test_the_header_date_must_be_the_day_of_the_post():
    with pytest.raises(SourceFormatError, match=r"image is for 20\.10\.2026, the post for 19\.10\.2026"):
        sumy_news.parse_image(PNG20, OCT19)
    unreadable = _edited(PNG20, lambda draw: draw.rectangle((340, 70, 615, 115), fill=(0, 60, 160)))
    with pytest.raises(SourceFormatError, match="no readable date"):
        sumy_news.parse_image(unreadable, OCT20)


def _edited(png: bytes, edit) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    edit(ImageDraw.Draw(image))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def _copy(png: bytes, box: tuple[int, int, int, int], to: tuple[int, int]) -> bytes:
    image = Image.open(io.BytesIO(png)).convert("RGB")
    image.paste(image.crop(box), to)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


PINK = (255, 200, 200)  # not a box colour the schedule uses


@pytest.mark.parametrize(
    "png, group",
    [
        # A box of a colour we do not know: after the last box, before another, or alone.
        (_edited(PNG20, lambda d: d.rectangle((474, 980, 712, 1087), fill=PINK)), "4.1"),
        (_edited(PNG20, lambda d: d.rectangle((202, 1501, 440, 1609), fill=PINK)), "6.1"),
        (_edited(PNG20, lambda d: d.rectangle((202, 236, 440, 343), fill=PINK)), "1.1"),
        # Damaged digits: a stroke across «08» of «08:00» joins them into one unknown glyph.
        (_edited(PNG20, lambda d: d.line((236, 271, 258, 271), fill=(0, 0, 0), width=2)), "1.1"),
        # «22:00 - 00:00» copied over «08:00 - 12:00»: real digits, but 2 h against «4 год. 00 хв.».
        (_copy(PNG20, (214, 478, 430, 510), (214, 255)), "1.1"),
        # No «—» in a row without boxes.
        (_edited(PNG20, lambda d: d.rectangle((198, 388, 236, 397), fill=(1, 30, 97))), "1.2"),
        # A label that does not read as the group of its row.
        (_edited(PNG20, lambda d: d.rectangle((60, 1650, 180, 1656), fill=(255, 255, 255))), "6.2"),
    ],
    ids=["unknown colour last", "unknown colour first", "unknown colour alone", "damaged digit",
         "length disagrees", "no dash", "label"],
)
def test_anything_that_fails_a_check_makes_its_group_unknown(png, group):
    groups, unknown = sumy_news.parse_image(png, OCT20)
    assert unknown == {group: sumy_news.UNRECOGNISED}
    assert len(groups) == 11
    assert groups == {g: o for g, o in sumy_news.parse_image(PNG20, OCT20)[0].items() if g != group}


def _png_header(width: int, height: int, pixels: bytes = b"") -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def _blank(size: tuple[int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(out, "PNG")
    return out.getvalue()


@pytest.mark.parametrize(
    "png, error",
    [
        (b"<html><body>Just a moment...</body></html>", "not a readable PNG"),
        (b"\xff\xd8\xff\xe0" + bytes(100), "not a readable PNG"),  # a JPEG, like the Telegram copies
        (b"\x89PNG\r\n\x1a\n" + bytes(3 * 1024 * 1024), "bytes, over"),
        (_png_header(100_000, 100_000), "100000 px wide"),  # refused from the header alone
        (_png_header(1000, 1824), "1000 px wide"),
        (_png_header(780, 1000), "780×1000, expected 780×1300–1899"),
        (_png_header(780, 30_000), "780×30000"),
        (_png_header(780, 1824), "not a readable PNG"),  # no pixel data
        # 20 MB of zeros squeezed into 20 KB: inflated only as far as the image needs.
        (_png_header(780, 1824, bytes(20_000_000)), "table lines"),
        (_blank((1052, 1824)), "table lines"),
    ],
)
def test_hostile_images_are_refused_quickly(png, error):
    with pytest.raises(SourceFormatError, match=error):
        _fast(sumy_news.parse_image, png, OCT20)


ROWS20 = [(225, 354), (357, 446), (449, 577), (579, 707), (710, 837), (840, 967), (970, 1098), (1101, 1228),
          (1231, 1358), (1361, 1489), (1492, 1619), (1622, 1749)]  # fmt: skip


def _striped(draw):
    # Box-coloured runs of every length in every cell (short of looking like table lines):
    # the box search stays bounded.
    for top, bottom in ROWS20:
        for x in range(194, 737, 3):
            draw.line((x, top + 5, x, top + 60), fill=sumy_news.BOX_GREY)
        for y in range(top + 61, bottom - 2, 2):
            draw.line((194, y, 194 + (y * 37) % 540, y), fill=sumy_news.BOX_YELLOW)


def test_striped_cells_are_read_quickly_as_unknown():
    groups, unknown = _fast(sumy_news.parse_image, _edited(PNG20, _striped), OCT20)
    assert groups == {} and sorted(unknown) == sorted(GROUPS)


def test_templates_are_intact():
    for name, chars in sumy_news.TEMPLATES.items():
        for codes in chars.values():
            for code in codes:
                assert sumy_news.encode_glyph(sumy_news._decode_template(code)) == code
    digits = set("0123456789")
    assert digits <= set(sumy_news.TEMPLATES["time"]) and digits <= set(sumy_news.TEMPLATES["header"])
    assert set("123456.") <= set(sumy_news.TEMPLATES["label"])


# --- Finding the posts -----------------------------------------------------------------------


def test_news_page():
    posts, last = sumy_news.parse_news(fixture_text("sumy_news.html"))
    # The photo of the week is another category; the content of 445 and 440 is in text rows
    # («$2a»), split from their header across scripts.
    assert [(p.id, p.day) for p in posts] == [(445, OCT20), (440, OCT19), (442, OCT18), (441, OCT18), (439, OCT18)]
    assert posts[0].posted == datetime(2026, 10, 19, 19, 34, 40, 556000, tzinfo=KYIV)
    assert posts[0].url == "https://www.soe.com.ua/press-service/news/445/gpv-tomorrow-19-10-2026-19-34"
    assert [p.image_url.rsplit("/", 1)[1][:-4] for p in posts[:3]] == [IMAGE20, IMAGE19, IMAGE18]
    assert last == OCT18


def _item(number, day, created, *, category="ГПВ", slug=None, image="", title=None, content=None, **extra):
    if content is None:
        img = f'<img src="https://www.soe.com.ua/uploads/public/news/{image}.png">' if image else ""
        content = f"<p>Зміни в обсязі застосування графіків на {day:%d-%m-%Y}.</p>{img}"
    return {
        "id": number,
        "categories": [{"id": 1, "name": category}],
        "title": title or "Зміни в застосуванні ГПВ на Сумщині",
        "content": {"content": content},
        "createdAt": created,
        "date": day.isoformat(),
        "url": slug or f"{number}/gpv-{day:%d-%m-%Y}-{number % 24:02}-00",
        **extra,
    }


def _page(items, rows: str = "") -> str:
    row = "6:" + json.dumps({"newsItems": {"data": items, "totalPages": 9, "totalCount": 50}}, ensure_ascii=False)
    push = json.dumps([1, rows + row + "\n"], ensure_ascii=False).replace("<", "\\u003c")
    return f"<html><body><script>self.__next_f.push({push})</script></body></html>"


def test_news_items():
    hostile = "a" * 32
    elsewhere = f'<img src="https://evil.example/uploads/public/news/{hostile}.png">'
    text_row = f"<img src=/uploads/public/news/{hostile}.png>"
    posts, last = sumy_news.parse_news(_page([
        _item(1, OCT20, "2026-10-19T16:00:00Z", image="1" * 32),
        _item(2, OCT20, "2026-10-19T16:00:00Z", category="Знеструмлення", image="2" * 32),  # other news
        _item(3, OCT20, "2026-10-19T16:00:00Z", slug="3/chomu-zastosovuyutsya-grafiki", image="3" * 32),  # an article
        _item(4, OCT20, "2026-10-19T16:00:00Z", slug="4/gpv-../../evil"),
        _item(5, OCT20, "not a time"),
        # The image is taken by its name only: always from the operator's host.
        _item(6, OCT20, "2026-10-19T16:00:00Z", content=elsewhere),
        # No ``date``: the day from the title.
        {**_item(7, OCT19, "2026-10-18T16:00:00Z", title="Графіки погодинних відключень на 19-10-2026"), "date": None},
        _item(8, OCT19, "2026-10-18T16:00:00Z", content="$2a"),  # in a text row
        "not an item",
    ], rows=f"2a:T{len(text_row):x},{text_row}"))  # fmt: skip
    assert [(p.id, p.day, p.image_url) for p in posts] == [
        (1, OCT20, f"https://www.soe.com.ua/uploads/public/news/{'1' * 32}.png"),
        (6, OCT20, f"https://www.soe.com.ua/uploads/public/news/{hostile}.png"),
        (7, OCT19, None),
        (8, OCT19, f"https://www.soe.com.ua/uploads/public/news/{hostile}.png"),
    ]
    assert last == OCT19


@pytest.mark.parametrize(
    "markup",
    [
        "<html><body>Just a moment...</body></html>",
        "self.__next_f.push(" * 300_000,
        "self.__next_f.push([1," + "[" * 200_000 + ")</script>",
        "self.__next_f.push([1,\"6:" + '\\"newsItems\\":' + "[" * 100_000 + '"])</script>',
        _page("not a list").replace("[1,", "[1,\"1:Tffffffff,\"]) </script><script>self.__next_f.push([1,", 1),
        "<script>self.__next_f.push([1,\"" + "1:T" * 300_000 + "\"])</script>",
        _page("not a list"),
    ],
)
def test_hostile_news_pages_are_refused_quickly(markup):
    with pytest.raises(SourceFormatError):
        _fast(sumy_news.parse_news, markup)


# --- The source ------------------------------------------------------------------------------


def _png_route(**images):
    def answer(call):
        return images.get(call.url.rsplit("/", 1)[1][:-4])

    return answer


def test_fetch_reads_the_newest_image_and_reuses_it():
    http = FakeHttp({"/press-service/news": fixture_text("sumy_news.html"), ".png": _png_route(**{IMAGE20: PNG20})})
    source = sumy_news.SumyNews()
    schedules = asyncio.run(source.fetch(http, kyiv(OCT20, 1)))
    assert list(schedules) == [OCT20]  # nothing for 21.10 yet
    s = schedules[OCT20]
    assert s.image_url == f"https://www.soe.com.ua/uploads/public/news/{IMAGE20}.png"
    assert s.updated_at == datetime(2026, 10, 19, 19, 34, 40, 556000, tzinfo=KYIV)
    assert spans(s, "2.1") == [("22:00", "24:00", False)] and not s.unknown
    asyncio.run(source.fetch(http, kyiv(OCT20, 1, 5)))
    assert sum(call.url.endswith(".png") for call in http.calls) == 1  # an unchanged image: not again
    # Only pages robots.txt allows, on the operator's host, with nothing but our own User-Agent.
    for call in http.calls:
        assert call.url.startswith("https://www.soe.com.ua/") and "/api/" not in call.url
        assert call.headers == {}
    assert sumy_news.SumyNews.hosts == {"www.soe.com.ua"}


def test_fetch_refuses_an_image_for_another_day_and_remembers():
    # The post says 19.10, the image says 18.10: refused, never shown as 19.10.
    http = FakeHttp({
        "/press-service/news": fixture_text("sumy_news.html"),
        ".png": _png_route(**{IMAGE19: PNG18, IMAGE20: PNG20}),
    })  # fmt: skip
    source = sumy_news.SumyNews()
    for _ in range(2):
        with pytest.raises(SourceFormatError, match=r"image is for 18\.10\.2026, the post for 19\.10\.2026"):
            asyncio.run(source.fetch(http, kyiv(OCT19, 20)))
    assert sum(call.url.endswith(".png") for call in http.calls) == 2  # each image downloaded once


def test_fetch_takes_the_newest_post_of_a_day():
    page = _page([
        _item(10, OCT18, "2026-10-18T03:26:44Z", image="a" * 32),
        _item(12, OCT18, "2026-10-18T18:57:13Z", image=IMAGE18),
        _item(11, OCT18, "2026-10-18T18:27:00Z", image="b" * 32),
        _item(9, date(2026, 10, 17), "2026-10-17T19:00:00Z", image="c" * 32),
    ])  # fmt: skip
    http = FakeHttp({"/press-service/news": page, ".png": _png_route(**{IMAGE18: PNG18})})
    s = asyncio.run(sumy_news.SumyNews().fetch(http, kyiv(OCT18, 22)))[OCT18]
    assert s.updated_at == datetime(2026, 10, 18, 21, 57, 13, tzinfo=KYIV)
    assert spans(s, "5.1")[0] == ("09:30", "10:00", False)
    assert [call.url.rsplit("/", 1)[1] for call in http.calls] == ["news", f"{IMAGE18}.png"]


def test_fetch_looks_at_the_second_page_when_today_is_pushed_off_the_first():
    first = _page([_item(n, OCT20, "2026-10-20T08:00:00Z", category="Знеструмлення") for n in range(20, 26)])
    second = _page([_item(19, OCT20, "2026-10-19T16:34:40Z", image=IMAGE20)])
    http = FakeHttp({
        "/press-service/news": lambda call: second if call.params.get("page") == "2" else first,
        ".png": _png_route(**{IMAGE20: PNG20}),
    })  # fmt: skip
    assert list(asyncio.run(sumy_news.SumyNews().fetch(http, kyiv(OCT20, 12)))) == [OCT20]
    assert [call.params for call in http.calls[:2]] == [{}, {"page": "2"}]


def test_fetch_without_posts_or_without_a_table():
    old = _page([_item(1, OCT18, "2026-10-18T18:00:00Z", image=IMAGE18)])
    http = FakeHttp({"/press-service/news": old})
    assert asyncio.run(sumy_news.SumyNews().fetch(http, kyiv(OCT20, 9))) == {}
    assert len(http.calls) == 1  # the newest news is older than today: no second page

    # The newest post for a day has no table (yet): the older table may be outdated.
    page = _page([_item(2, OCT20, "2026-10-20T09:00:00Z"), _item(1, OCT20, "2026-10-19T16:00:00Z", image=IMAGE20)])
    s = asyncio.run(sumy_news.SumyNews().fetch(FakeHttp({"/press-service/news": page}), kyiv(OCT20, 9)))[OCT20]
    assert s.groups == {} and s.unknown == dict.fromkeys(GROUPS, sumy_news.UNRECOGNISED)
    assert s.image_url is None and "https://www.soe.com.ua/press-service/news/2/gpv-20-10-2026-02-00" in s.notes[0]


def test_fetch_errors_propagate():
    with pytest.raises(FetchError):
        asyncio.run(sumy_news.SumyNews().fetch(FakeHttp({}), kyiv(OCT20, 9)))
    http = FakeHttp({"/press-service/news": fixture_text("sumy_news.html"), ".png": lambda call: b"<html>"})
    with pytest.raises(SourceFormatError, match="not a readable PNG"):
        asyncio.run(sumy_news.SumyNews().fetch(http, kyiv(OCT20, 9)))
