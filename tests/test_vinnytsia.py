"""Vinnytsiaoblenergo: the address form on www.voe.com.ua, against trimmed copies of its answers.

``vinnytsia_form.html``, ``vinnytsia_result.html`` and the autocomplete answers are real
(20.10.2026, the operator's own office at вул. Магістратська, 2). That day the energy system
worked normally and no address had outages, so ``vinnytsia_outages.html`` is the real result
page with constructed hour cells, marked the way the site marked outages in early 2026.
"""

import asyncio
import json
from datetime import date

import pytest
from conftest import FakeHttp, fixture_text, kyiv, spans

from ua_outages import Choice
from ua_outages.http import FetchError
from ua_outages.providers import vinnytsia
from ua_outages.providers.base import SourceBlocked, SourceFormatError
from ua_outages.providers.vinnytsia import Vinnytsia, VinnytsiaLookup

OCT20, OCT21 = date(2026, 10, 20), date(2026, 10, 21)
NOW = kyiv(OCT20, 9)
OFFICE = "510100000/1524/640806"  # вул. Магістратська, 2: Вінницяобленерго itself
BUILD_ID = "form-w6E2EO8s4ZqyDG8UtRTDla_DnGh6KnuAPvPSzivcEPQ"


def run(coro):
    return asyncio.run(coro)


def outages_page(**replace):
    page = fixture_text("vinnytsia_outages.html")
    for old, new in replace.items():
        assert old in page, old
        page = page.replace(old, new)
    return page


def site(result="vinnytsia_outages.html"):
    """The form page for GET, ``result`` for the POST, and the autocompletes."""

    def form(call):
        if call.method == "GET":
            return fixture_text("vinnytsia_form.html")
        return fixture_text(result) if result.endswith(".html") else result

    return FakeHttp(
        {
            "/disconnection/detailed": form,
            "/read_city": fixture_text("vinnytsia_cities.json"),
            "/read_street/510100000": fixture_text("vinnytsia_streets.json"),
            "/read_house/1524": fixture_text("vinnytsia_houses.json"),
        }
    )


def test_the_form_gives_its_hidden_fields():
    form = vinnytsia.parse_form(fixture_text("vinnytsia_form.html"))
    # Not the site search's form_build_id, which comes first on the page.
    assert form == {"form_build_id": BUILD_ID, "form_id": "disconnection_detailed_search_form"}
    for broken in (
        fixture_text("vinnytsia_form.html").replace(BUILD_ID, "form-x"),
        fixture_text("vinnytsia_form.html").replace('value="disconnection_detailed_search_form"', 'value="other"'),
        "<html><body><form id='search-block'></form></body></html>",
    ):
        with pytest.raises(SourceFormatError):
            vinnytsia.parse_form(broken)
    challenge = "<!DOCTYPE html><title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x.js'></script>"
    with pytest.raises(SourceBlocked):
        vinnytsia.parse_form(challenge)


def test_normal_mode_shows_no_queue_and_nothing_off():
    page = fixture_text("vinnytsia_result.html")
    assert vinnytsia.parse(page, OCT20) == {}
    assert vinnytsia.parse_queues(page) == []


def test_schedule_with_outages():
    page = fixture_text("vinnytsia_outages.html")
    schedules = vinnytsia.parse(page, OCT20)
    assert list(schedules) == [OCT20, OCT21]
    today, tomorrow = schedules[OCT20], schedules[OCT21]
    assert list(today.groups) == ["4.2"] and today.unknown == {} and today.updated_at is None
    # 07–09 confirmed (confirm_3, full hours) and 09:00–09:30 (a "confirmed" fill over the first half);
    # 14:30–15:00 (a confirm_0 fill over the second half) and 15–17 (confirm_0) are possible.
    assert spans(today, "4.2") == [("07:00", "09:30", False), ("14:30", "17:00", True), ("21:00", "22:00", False)]
    assert spans(tomorrow, "4.2") == [("03:00", "04:00", True), ("18:00", "19:00", False)]  # confirm_1, confirm_4
    assert vinnytsia.parse_queues(page) == ["4.2"]


def test_the_year_is_the_nearest_one():
    page = outages_page(**{"вт 20.10": "чт 31.12", "ср 21.10": "пт 01.01"})
    assert list(vinnytsia.parse(page, date(2026, 12, 31))) == [date(2026, 12, 31), date(2027, 1, 1)]
    assert list(vinnytsia.parse(page, date(2027, 1, 1))) == [date(2026, 12, 31), date(2027, 1, 1)]


def test_older_and_variant_markup():
    # December 2025: hourly cells without hour_block, the queue in "<p>" all the same.
    old = '<div class="disconnection-detailed-table-cell cell  has_disconnection confirm_3 current_day"><div class="disconnection disconnection_confirm_3" title="Номер черги: 4.2">4.2</div></div>'
    on = '<div class="disconnection-detailed-table-cell cell no_disconnection current_day"><div class="hour_block"></div></div>'
    page = outages_page(**{on: old}).replace("<p>4.2 черга</p>", "<p>Черга 4.2</p>")
    assert spans(vinnytsia.parse(page, OCT20)[OCT20], "4.2")[0] == ("00:00", "09:30", False)
    # A half named by class rather than a fill.
    half = '<div class="disconnection-detailed-table-cell cell has_disconnection confirm_3 current_day"><div class="hour_block has_any second_half"></div></div>'
    page = outages_page(**{on: half})
    assert spans(vinnytsia.parse(page, OCT20)[OCT20], "4.2")[0] == ("00:30", "01:00", False)


@pytest.mark.parametrize(
    "cell",
    [
        '<div class="disconnection-detailed-table-cell cell current_day"><div class="hour_block"></div></div>',
        '<div class="disconnection-detailed-table-cell cell has_disconnection no_disconnection current_day"></div>',
        '<div class="disconnection-detailed-table-cell cell no_disconnection full_hour current_day"></div>',
        '<div class="disconnection-detailed-table-cell cell no_disconnection current_day"><div class="hour_block has_any"><div class="fill" style="--start: 80; --size: 50"></div></div></div>',
        '<div class="disconnection-detailed-table-cell cell no_disconnection current_day"><div class="hour_block has_any"><div class="fill" style="--start: 20"></div></div></div>',
        '<div class="disconnection-detailed-table-cell cell no_disconnection current_day"><div class="hour_block has_any"><div class="fill" style="--start: 0; --size: 0"></div></div></div>',
        '<div class="disconnection-detailed-table-cell cell no_disconnection current_day"><div class="hour_block has_any">'
        + '<div class="fill" style="--start: 0; --size: 10"></div>' * 5
        + "</div></div>",
    ],
)
def test_an_unrecognised_cell_makes_the_day_unknown(cell):
    on = '<div class="disconnection-detailed-table-cell cell no_disconnection current_day"><div class="hour_block"></div></div>'
    schedules = vinnytsia.parse(outages_page(**{on: cell}), OCT20)
    assert schedules[OCT20].unknown == {"4.2": vinnytsia.UNKNOWN} and "4.2" not in schedules[OCT20].groups
    assert spans(schedules[OCT21], "4.2") == [("03:00", "04:00", True), ("18:00", "19:00", False)]


def test_a_missing_cell_makes_the_day_unknown():
    on = '<div class="disconnection-detailed-table-cell cell no_disconnection current_day"><div class="hour_block"></div></div>'
    page = fixture_text("vinnytsia_outages.html")
    page = page.replace(on, "", 1)
    assert vinnytsia.parse(page, OCT20)[OCT20].unknown == {"4.2": vinnytsia.UNKNOWN}


@pytest.mark.parametrize(
    "replace",
    [
        {"вт 20.10": "вт"},  # a day without a date
        {"вт 20.10": "вт 31.02"},  # no such date
        {"вт 20.10": "пн 01.06"},  # far from today
        {"ср 21.10": "вт 20.10"},  # the same day twice
        {"<p>4.2 черга</p>": ""},  # outages nobody can attribute
        {"<p>4.2 черга</p>": "<p>черга</p>"},
        {'class="disconnection-detailed-table-container"': 'class="other"'},
        {'class="disconnection-detailed-table"': 'class="other"'},
        {'id="disconnection-detailed-search-form"': 'id="other"'},
    ],
)
def test_garbled_pages_are_format_errors(replace):
    with pytest.raises(SourceFormatError):
        vinnytsia.parse(outages_page(**replace), OCT20)


def test_hostile_pages_are_format_errors():
    for page in ("", "[]", "<div>" * 100_000, "<html><body><p>4.2 черга</p></body></html>"):
        with pytest.raises(FetchError):  # SourceFormatError, or TooManyNodes for the flood of divs
            vinnytsia.parse(page, OCT20)
    table = fixture_text("vinnytsia_outages.html")
    start = table.index('<div class="disconnection-detailed-table">')
    many = table[:start] + table[start : table.index('<div class="discon-fact-legend">')] * 6 + table[start:]
    with pytest.raises(SourceFormatError):
        vinnytsia.parse(many, OCT20)


def test_address_keys_are_ids_only():
    source = Vinnytsia()
    assert source.valid_address(OFFICE)
    assert vinnytsia.address_key("510100000", "1524", "640806") == OFFICE
    for hostile in ("510100000/1524", "510100000/1524/2А", "a/b/c", "1/2/3/4", "1/2/../3", "1/2/3\n", "1/2/" + "9" * 13, "", None, 5):
        assert not source.valid_address(hostile)
    for hostile in ("510100000/1524/2А", "1/2/../../admin", "1/2/3?x=1", ""):
        with pytest.raises(ValueError):
            run(source.fetch_address(site(), NOW, hostile))
    assert vinnytsia.address_key("1", "2", "3/4") is None


def test_fetches_one_address_like_the_page():
    source, http = Vinnytsia(), site()
    schedules = run(source.fetch_address(http, NOW, OFFICE))
    assert list(schedules) == [OCT20, OCT21]
    get, post = http.calls
    assert (get.method, get.url, get.params) == ("GET", "https://www.voe.com.ua/disconnection/detailed", {})
    assert (post.method, post.url, post.params) == ("POST", "https://www.voe.com.ua/disconnection/detailed", {})
    assert post.data == {
        "search_type": "0", "city": "", "city_id": "510100000", "street": "", "street_id": "1524",
        "house": "", "house_id": "640806", "form_build_id": BUILD_ID,
        "form_id": "disconnection_detailed_search_form", "search": "Показати",
    }  # fmt: skip
    assert not any("cookie" in k.lower() for call in http.calls for k in call.headers)
    # The form's hidden fields are reused: the next address costs one request.
    run(source.fetch_address(http, NOW, "510100000/1652/26600"))
    assert [c.method for c in http.calls] == ["GET", "POST", "POST"]
    assert http.calls[-1].data["street_id"] == "1652" and http.calls[-1].data["house_id"] == "26600"
    assert run(source.fetch(http, NOW)) == {}  # nothing to ask without an address


def test_a_bad_answer_forgets_the_form():
    source = Vinnytsia()
    with pytest.raises(SourceFormatError):
        run(source.fetch_address(site("<html></html>"), NOW, OFFICE))
    http = site()
    run(source.fetch_address(http, NOW, OFFICE))
    assert [c.method for c in http.calls] == ["GET", "POST"]


def test_lookup_steps():
    lookup, http = VinnytsiaLookup(), site()
    assert [s.house for s in lookup.steps] == [False, False, True]
    assert run(lookup.options(http, [], "м. Вінниця")) == [Choice("510100000", "м. Вінниця (Вінницька Область/М.Вінниця)")]
    assert http.calls[-1].url.endswith("/disconnection/detailed/autocomplete/read_city")
    assert http.calls[-1].params == {"q": "вінниця"}
    assert run(lookup.options(http, ["510100000"], "вул. Магістратська")) == [Choice("1524", "вулиця Магістратська")]
    assert http.calls[-1].params == {"q": "магістратська"}
    houses = run(lookup.options(http, ["510100000", "1524"], "буд. 2"))
    assert houses[0] == Choice("640806", "2") and len(houses) == 10  # exact first, then "20"…"29"
    assert http.calls[-1].url.endswith("/read_house/1524") and http.calls[-1].params == {"q": "2"}
    run(lookup.options(http, ["510100000", "1524"], "51-а"))
    assert http.calls[-1].params == {"q": "51А"}  # the directory writes "51А"
    calls = len(http.calls)
    assert run(lookup.options(http, [], "В")) == [] and len(http.calls) == calls  # too short to search
    for hostile in (["../../admin"], ["510100000", "1524?x=1"], ["1", "2", "3"]):
        with pytest.raises(ValueError):
            run(lookup.options(http, hostile, "2"))


def test_lookup_resolves_the_queue_and_the_address_key():
    resolution = run(VinnytsiaLookup().resolve(site(), ["510100000", "1524", "640806"]))
    assert resolution.groups == ("4.2",) and resolution.address == OFFICE and resolution.reason is None
    # In normal mode the page shows no queue at all.
    quiet = run(VinnytsiaLookup().resolve(site("vinnytsia_result.html"), ["510100000", "1524", "640806"]))
    assert quiet.groups == () and quiet.address == OFFICE
    assert "лише тоді, коли діють графіки" in quiet.reason  # said instead of "in no group"
    for hostile in (["510100000", "1524"], ["510100000", "1524", "2А"], ["1", "2", "../3"]):
        with pytest.raises(ValueError):
            run(VinnytsiaLookup().resolve(site(), hostile))


def test_directory_answers_are_checked():
    def item(method="read_street", id_="1524", value="вулиця. Магістратська"):
        label = f'<div class="change_api_autocomplit" data-method="{method}" data-id="{id_}">{value}</div>'
        return {"value": value, "label": label}

    choices = vinnytsia.parse_choices(
        [
            item(),
            item(method="read_city"),  # another step's answer
            item(id_="../1"),
            item(id_="1 OR 1=1"),
            item(value="вул.‮ Соборна​\n"),  # bidi override, zero-width, newline
            {"value": "x", "label": "<b>no autocomplete div</b>"},
            {"value": "x", "label": "<div class='change_api_autocomplit'>" + "x" * 3000 + "</div>"},
            {"value": "x"},
            "not an object",
        ],
        "read_street",
    )
    assert choices == [Choice("1524", "вулиця Магістратська"), Choice("1524", "вул. Соборна")]
    assert len(vinnytsia.parse_choices([item(id_=str(n)) for n in range(1000)], "read_street")) == vinnytsia.MAX_SUGGESTIONS
    assert vinnytsia.parse_choices([], "read_house") == []
    for hostile in ({"value": "x"}, "[]", None):
        with pytest.raises(SourceFormatError):
            vinnytsia.parse_choices(hostile, "read_street")
    assert vinnytsia.tidy("м.. Вінниця") == "м. Вінниця" and vinnytsia.tidy("с.. Агрономічне") == "с. Агрономічне"
    assert vinnytsia.house_term("буд. №53/5") == "53/5" and vinnytsia.house_term("10 б") == "10Б"


def test_fixtures_hold_only_the_public_address():
    for name in ("vinnytsia_form.html", "vinnytsia_result.html", "vinnytsia_outages.html"):
        page = fixture_text(name)
        assert "personal_account" not in page and 'name="eic"' not in page
    result = fixture_text("vinnytsia_result.html")
    assert 'name="city_id" value="510100000"' in result and 'name="house_id" value="640806"' in result
    assert json.loads(fixture_text("vinnytsia_houses.json"))[0]["value"] == "2"
