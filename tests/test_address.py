"""Address lookups against trimmed copies of the real directories (October 2026)."""

import asyncio
import base64
import json
from datetime import UTC, date, datetime

import aiohttp
import pytest
from aiohttp import web
from conftest import FakeHttp, address_fixture, kyiv, spans
from multidict import CIMultiDict

from ua_outages import Choice, operator
from ua_outages.http import CachingFetcher, Response, ResponseCache, Throttle, redact
from ua_outages.providers import ternopil
from ua_outages.providers._address import (
    MAX_LABEL,
    MAX_QUERY,
    clean_label,
    clean_query,
    normalize_group,
    numeric_id,
    pick,
    strip_kind,
)
from ua_outages.providers.base import SourceFormatError
from ua_outages.providers.loe import LoeLookup
from ua_outages.providers.mykolaiv import MykolaivLookup
from ua_outages.providers.poltava import PoltavaLookup
from ua_outages.providers.zhytomyr import ZhytomyrLookup

OCT17 = date(2026, 10, 17)


def run(coro):
    return asyncio.run(coro)


def labels(choices):
    return [c.label for c in choices]


def test_directory_text_is_cleaned():
    assert clean_label("  вул.‮Руська\n​ ") == "вул. Руська"  # bidi override, newline, zero-width
    assert clean_label("<b>x</b>") == "<b>x</b>"  # markup stays text: renderers escape it
    long = clean_label("ж" * 500)
    assert len(long) == MAX_LABEL and long.endswith("…")
    assert clean_label(12) == "12" and clean_label(None) is None and clean_label("  ") is None
    assert clean_label(True) is None and clean_label(["x"]) is None
    assert len(clean_query("Руська\x00 1 " * 50)) <= MAX_QUERY and "\x00" not in clean_query("a\x00b")


def test_directory_ids_are_digits_only():
    assert numeric_id(1032) == "1032" and numeric_id("10035") == "10035"
    for hostile in ("../1", "1/../../admin", "1e3", " 1", "1" * 13, True, None, 1.5, "١٢"):
        assert numeric_id(hostile) is None, hostile


def test_groups_are_normalized():
    assert normalize_group("3.2") == "3.2" and normalize_group("3-2") == "3.2" and normalize_group(" 03,2 ") == "3.2"
    assert normalize_group("Не входить") is None and normalize_group("3") is None and normalize_group(32) is None


def test_matching_ignores_kind_case_punctuation_and_word_order():
    assert strip_kind("вул. Руська") == "Руська" and strip_kind("м.Полтава") == "Полтава"
    assert strip_kind("Сихівська") == "Сихівська" and strip_kind("с-ще Дніпровське") == "Дніпровське"
    streets = [Choice("1", "просп. Шевченка Тараса"), Choice("2", "вул. Шевченка"), Choice("3", "пров. Мар’янівський")]
    assert [c.id for c in pick(streets, "Тараса Шевченка")] == ["1"]
    assert [c.id for c in pick(streets, "вул. шевченка")] == ["2", "1"]  # exact first
    assert [c.id for c in pick(streets, "мар'ян")] == ["3"]
    houses = [Choice(h, h) for h in ("10-А", "110", "10", "2", "1", "10-А")]
    assert labels(pick(houses, "10а")) == ["10-А"]
    assert labels(pick(houses, "10")) == ["10", "10-А", "110"]
    assert labels(pick(houses, "")) == ["1", "2", "10", "10-А", "110"]  # natural order, no duplicates
    assert len(pick([Choice(str(n), str(n)) for n in range(100)], "", limit=30)) == 30


def yasno_http(streets=None):
    return FakeHttp(
        {
            "/cities": address_fixture("yasno_cities.json"),
            "/streets": streets or address_fixture("yasno_streets.json"),
            "/houses": address_fixture("yasno_houses.json"),
            "/group": address_fixture("yasno_group.json"),
        }
    )


def test_yasno_lookup_kyiv():
    lookup, http = operator("dtek-kem").lookup, yasno_http()
    assert len(lookup.steps) == 2
    streets = run(lookup.options(http, [], "вул. Шевченка"))
    assert streets[0] == Choice("1042", "вул. Шевченка")
    assert http.calls[-1].params == {"regionId": "25", "dsoId": "902", "query": "Шевченка"}
    # The directory writes "1А": search by the number, match "1-а" here.
    assert labels(run(lookup.options(http, ["1042"], "буд. 1-а"))) == ["1А"]
    assert http.calls[-1].params == {"regionId": "25", "dsoId": "902", "streetId": "1042", "query": "1"}
    assert run(lookup.resolve(http, ["1042", "232437"])).groups == ("20.1",)
    assert http.calls[-1].params == {"regionId": "25", "dsoId": "902", "streetId": "1042", "houseId": "232437"}


def test_yasno_lookup_dnipro_passes_the_city_on():
    lookup, http = operator("dtek-dnem").lookup, yasno_http()
    assert len(lookup.steps) == 3
    assert run(lookup.options(http, [], "Дніпро"))[0] == Choice("6", "м. Дніпро")
    run(lookup.options(http, ["6"], "Шевченка"))
    assert http.calls[-1].params == {"regionId": "3", "dsoId": "301", "cityId": "6", "query": "Шевченка"}
    run(lookup.resolve(http, ["6", "1322", "157853"]))
    assert http.calls[-1].params == {
        "regionId": "3", "dsoId": "301", "cityId": "6", "streetId": "1322", "houseId": "157853",
    }  # fmt: skip


def test_yasno_lookup_retries_with_the_longest_word():
    def streets(call):
        return "[]" if " " in call.params["query"] else address_fixture("yasno_streets.json")

    lookup, http = operator("dtek-kem").lookup, yasno_http(streets)
    found = run(lookup.options(http, [], "Тараса Шевченка"))
    assert [c.params["query"] for c in http.calls] == ["Тараса Шевченка", "шевченка"]
    assert found and all("Тараса" in c.label or "Т." in c.label for c in found)


def test_yasno_lookup_edge_cases():
    lookup, http = operator("dtek-kem").lookup, yasno_http()
    assert run(lookup.options(http, [], "Ше")) == [] and http.calls == []  # too short to search
    with pytest.raises(ValueError):
        run(lookup.options(http, ["../../admin"], "1"))
    with pytest.raises(ValueError):
        run(lookup.resolve(http, ["1042"]))
    weird = FakeHttp({"/group": json.dumps({"group": None, "subgroup": 1})})
    assert run(lookup.resolve(weird, ["1", "2"])).groups == ()


def test_loe_lookup():
    lookup = LoeLookup()
    http = FakeHttp(
        {
            "/pw_cities": address_fixture("loe_cities.json"),
            "/pw_streets": address_fixture("loe_streets.json"),
            "/pw_accounts": address_fixture("loe_accounts.json"),
        }
    )
    cities = run(lookup.options(http, [], "м. Львів"))
    assert cities[0] == Choice("693", "Львів (Львівська громада)")
    assert http.calls[-1].params == {"pagination": "false", "name": "львів"}
    assert run(lookup.options(http, ["693"], "вул. Наукова"))[0] == Choice("12446", "Наукова")
    assert http.calls[-1].params == {"pagination": "false", "city.id": "693", "name": "наукова"}
    houses = run(lookup.options(http, ["693", "12446"], ""))
    assert labels(houses) == ["2", "2-А", "7", "10", "10-А", "12", "12-А", "33/38", "34", "34-А", "96", "112"]
    assert labels(run(lookup.options(http, ["693", "12446"], "10а"))) == ["10-А"]
    # A building where most consumers are in 3.1 and one in 5.2; one outside any group.
    assert run(lookup.resolve(http, ["693", "12446", "34"])).groups == ("3.1", "5.2")
    seven = run(lookup.resolve(http, ["693", "12446", "7"]))
    assert seven.groups == ("2.1",) and seven.other == ("Не входить",)
    with pytest.raises(ValueError):
        run(lookup.resolve(http, ["693", "12446", "999"]))


def test_mykolaiv_lookup():
    lookup = MykolaivLookup()
    http = FakeHttp(
        {
            "/addr/filii": address_fixture("mykolaiv_filii.json"),
            "/filii/15/ns": address_fixture("mykolaiv_ns.json"),
            "/ns/171/street": address_fixture("mykolaiv_streets.json"),
            "/street/2968/dom": address_fixture("mykolaiv_dom.json"),
            "/dom/124212/outage-queue": address_fixture("mykolaiv_outage_queue.json"),
        }
    )
    districts = run(lookup.options(http, [], ""))
    assert len(districts) == 20 and Choice("15", "Миколаївський РЕМ") in districts
    assert run(lookup.options(http, ["15"], "")) == [Choice("171", "м. Миколаїв")]
    assert labels(run(lookup.options(http, ["15", "171"], "Шевченка"))) == ["вул. Шевченка", "пров. Шевченка"]
    assert run(lookup.options(http, ["15", "171", "2968"], "1"))[0] == Choice("124212", "1")
    # The answer lists a СГАВ queue "2" too: only ГПВ is the schedule's group.
    assert run(lookup.resolve(http, ["15", "171", "2968", "124212"])).groups == ("2.2",)
    assert [c.url.removeprefix("https://off.energy.mk.ua/api") for c in http.calls][-1] == "/addr/dom/124212/outage-queue"


def test_poltava_lookup():
    lookup = PoltavaLookup()

    def search(call):
        operation = json.loads(call.data["disconn"])["operation_type"]
        return address_fixture({"city": "poltava_cities.json", "street": "poltava_streets.json", "building": "poltava_houses.json"}[operation])

    http = FakeHttp(
        {
            "/power-outages/": address_fixture("poltava_filials.html"),
            "search-disconnection-cabinet.php": search,
            "disconnectinforesult-cabinet.php": address_fixture("poltava_result.json"),
        }
    )
    filials = run(lookup.options(http, [], ""))
    assert len(filials) == 28 and Choice("123", "Полтавська об’єднана філія") in filials
    assert run(lookup.options(http, ["123"], "Полтава")) == [Choice("11389", "місто Полтава")]
    assert json.loads(http.calls[-1].data["disconn"]) == {
        "operation_type": "city", "filial_id": "123", "city_id": "", "street_id": "",
    }  # fmt: skip
    assert run(lookup.options(http, ["123", "11389"], "Соборності")) == [Choice("1227174", "вул. Соборності")]
    houses = run(lookup.options(http, ["123", "11389", "1227174"], "10"))
    assert houses[0] == Choice("10", "10")  # asked by name: the answer numbers buildings 1, 2, 3…
    assert run(lookup.resolve(http, ["123", "11389", "1227174", "10"])).groups == ("1.1",)
    assert json.loads(http.calls[-1].data["disconn"]) == {"cityid": "11389", "streerid": "1227174", "house": "10"}
    empty = FakeHttp({"disconnectinforesult-cabinet.php": json.dumps([{"gp_qnum_1": None, "gp_qnum_2": None}])})
    assert run(lookup.resolve(empty, ["123", "11389", "1227174", "10"])).groups == ()


def test_zhytomyr_lookup():
    lookup = ZhytomyrLookup()
    page = address_fixture("zhytomyr_search.html").encode("cp1251")
    http = FakeHttp({"/unhooking-search.php": page})
    assert Choice("7", "Житомирський") in run(lookup.options(http, [], ""))
    assert run(lookup.options(http, ["7"], "Житомир")) == [Choice("16657", "місто Житомир")]
    assert http.calls[-1].params == {"rem_id": "7"}
    assert run(lookup.options(http, ["7", "16657"], "Київська")) == [Choice("173934", "вул. Київська")]
    street = ["7", "16657", "173934"]
    assert run(lookup.options(http, street, "9"))[0] == Choice("9", "9")
    assert http.calls[-1].params == {"rem_id": "7", "naspunkt_id": "16657", "vulica_id": "173934", "all": "1"}
    # Households win over businesses in the same house; "не вимикається" is not a group.
    assert run(lookup.resolve(http, [*street, "9"])).groups == ("1.2",)
    thirteen = run(lookup.resolve(http, [*street, "13"]))
    assert thirteen.groups == () and thirteen.other == ("не вимикається",)
    assert run(lookup.resolve(http, [*street, "86"])).groups == ("5.2",)  # businesses only


def ternopil_http(**routes):
    return FakeHttp(
        {
            "/pw_cities": address_fixture("ternopil_cities.json"),
            "/pw_streets": address_fixture("ternopil_streets.json"),
            "/pw-accounts/building-names": address_fixture("ternopil_buildings.json"),
            "/a_gpv_g": address_fixture("ternopil_gpv.json"),
            **routes,
        }
    )


def test_ternopil_lookup_gives_the_address_key():
    lookup, http = operator("ternopil-oe").lookup, ternopil_http()
    assert run(lookup.options(http, [], "м. Тернопіль")) == [Choice("1032", "Тернопіль (Тернопільська громада)")]
    assert run(lookup.options(http, ["1032"], "Руська")) == [Choice("10035", "вул. Руська")]
    houses = run(lookup.options(http, ["1032", "10035"], ""))
    assert len(houses) == 16 and labels(houses)[:3] == ["1", "2", "2-А"]  # "12-А" listed twice in the answer
    resolution = run(lookup.resolve(http, ["1032", "10035", "2-А"]))
    assert resolution.groups == ("1.2",) and resolution.address == "1032/10035/2-А"
    with pytest.raises(ValueError):
        run(lookup.resolve(http, ["1032", "10035", "404"]))


def test_ternopil_schedule():
    schedules = ternopil.parse(json.loads(address_fixture("ternopil_gpv.json")))
    s = schedules[OCT17]
    assert s.updated_at == datetime(2026, 10, 17, 18, 52, 36, tzinfo=UTC)
    assert list(s.groups) == ["3.2"]
    assert spans(s, "3.2") == [("19:30", "20:00", True), ("20:00", "22:00", False), ("22:00", "22:30", True)]
    assert ternopil.parse({"hydra:member": None, "hydra:totalItems": 0}) == {}
    broken = json.loads(address_fixture("ternopil_gpv.json"))
    broken["hydra:member"][0]["dataJson"]["3.2"]["times"]["10:00"] = "<script>"
    assert ternopil.parse(broken)[OCT17].unknown == {"3.2": "Нерозпізнаний графік"}
    for hostile in ([], {"hydra:member": "x"}, {"hydra:member": [{"dataJson": {}}]}, {"hydra:member": [{}] * 50}):
        with pytest.raises(SourceFormatError):
            ternopil.parse(hostile)


def test_ternopil_fetches_one_address_like_the_site():
    source, http = operator("ternopil-oe").sources[0], ternopil_http()
    schedules = run(source.fetch_address(http, kyiv(date(2026, 10, 18), 9), "1032/10035/2-А"))
    assert list(schedules) == [OCT17]
    call = http.calls[-1]
    assert call.params == {
        "after": "2026-10-17T12:00:00+00:00", "before": "2026-10-19T00:00:00+00:00", "time": "1032100352-А",
    }  # fmt: skip
    assert base64.b64decode(call.headers["X-debug-key"]).decode() == "1032/10035/2-А"
    assert run(source.fetch(http, kyiv(date(2026, 10, 18), 9))) == {}  # nothing to ask without an address
    for hostile in ("1032/10035", "a/b/c", "1/2/" + "x" * 40, "1/2/\n", ""):
        assert not source.valid_address(hostile)
        with pytest.raises(ValueError):
            run(source.fetch_address(http, kyiv(date(2026, 10, 18), 9), hostile))


def test_redact_drops_query_strings():
    message = "GET https://api-poweron.toe.com.ua/api/a_gpv_g?time=103210035&after=x: HTTP 500"
    assert redact(message) == "GET https://api-poweron.toe.com.ua/api/a_gpv_g?…: HTTP 500"


def test_response_cache_expires_and_stays_within_its_size():
    now = [0.0]
    cache = ResponseCache(ttl=10, max_bytes=100, clock=lambda: now[0])

    def response(size):
        return Response("https://x.invalid/", 200, CIMultiDict(), b"x" * size, "utf-8")

    cache.put(("a",), response(40))
    cache.put(("b",), response(40))
    assert cache.get(("a",)) is not None  # now the most recently used
    cache.put(("c",), response(40))  # over 100 bytes: "b" goes
    assert cache.get(("b",)) is None and cache.get(("a",)) and cache.get(("c",))
    cache.put(("huge",), response(500))
    assert cache.get(("huge",)) is None
    now[0] = 11
    assert cache.get(("a",)) is None


def test_caching_fetcher_asks_once_and_spaces_requests():
    hits = []

    async def handler(request):
        hits.append((request.path_qs, asyncio.get_running_loop().time()))
        return web.Response(text="[]")

    async def scenario():
        app = web.Application()
        app.router.add_get("/{tail:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as session:
                cache, throttle = ResponseCache(60, 1024), Throttle(0.2)
                http = CachingFetcher(session, ["127.0.0.1"], schemes=["http"], cache=cache, throttle=throttle)
                base = f"http://127.0.0.1:{port}/streets"
                await asyncio.gather(*(http.get(base, params={"q": "a"}) for _ in range(3)))
                await http.get(base, params={"q": "b"})
                with pytest.raises(Exception, match="not allowed"):
                    await http.get("https://evil.example/")
        finally:
            await runner.cleanup()

    asyncio.run(scenario())
    assert [path for path, _ in hits] == ["/streets?q=a", "/streets?q=b"]
    assert hits[1][1] - hits[0][1] >= 0.19
