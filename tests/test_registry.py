import re

from ua_outages.http import forbidden_host
from ua_outages.registry import OCCUPIED, OPERATORS, OPERATORS_BY_ID, REGIONS, REGIONS_BY_ID

ID_RE = re.compile(r"[a-z][a-z0-9-]{1,19}")


def test_ids_are_unique_and_short():
    assert len(OPERATORS_BY_ID) == len(OPERATORS)
    # 24 oblasts, the Autonomous Republic of Crimea, Kyiv and Sevastopol.
    assert len(REGIONS_BY_ID) == len(REGIONS) == 27
    for item in (*OPERATORS, *REGIONS):
        assert ID_RE.fullmatch(item.id), item.id


def test_regions_and_operators_point_at_each_other():
    for region in REGIONS:
        assert region.operators
        for operator_id in region.operators:
            assert OPERATORS_BY_ID[operator_id].region == region.id
    listed = {o for r in REGIONS for o in r.operators}
    assert listed == set(OPERATORS_BY_ID)


def test_operators_without_sources_explain_why():
    for operator in OPERATORS:
        if not operator.available:
            assert operator.note, operator.id
        else:
            assert operator.groups and len(set(operator.groups)) == len(operator.groups)
            for source in operator.sources:
                assert source.hosts, source
                assert source.min_interval >= 60
            assert operator.site.startswith("https://")


def test_callback_data_fits_telegram_limit():
    # The bot packs "<action>:<operator>:<group>" into 64-byte callback data.
    longest = max(len(f"g:{o.id}:{g}".encode()) for o in OPERATORS for g in o.groups)
    assert longest <= 40


def test_address_lookups_and_per_address_operators():
    for operator in OPERATORS:
        if operator.lookup is not None:
            assert operator.available and operator.lookup.hosts and operator.lookup.steps, operator.id
            assert operator.lookup.steps[-1].house, operator.id
            assert len(f"f:{operator.id}".encode()) <= 64
        if operator.by_address:
            assert operator.lookup is not None, f"{operator.id}: subscribing needs an address lookup"
    assert OPERATORS_BY_ID["ternopil-oe"].by_address
    assert not OPERATORS_BY_ID["lviv-oe"].by_address
    # A search's buttons carry an 8-character token and an index: "p:0123abcd:23".
    assert len("p:0123abcd:23") <= 64


def test_occupied_territories_are_listed_but_never_fetched():
    occupied = [r for r in REGIONS if r.occupied]
    assert {r.id for r in occupied} == {"crimea", "sevastopol"}
    for region in occupied:
        for operator_id in region.operators:
            operator = OPERATORS_BY_ID[operator_id]
            assert not operator.sources and operator.lookup is None and not operator.site
            assert operator.note == OCCUPIED


def test_no_source_points_at_the_aggressors_domains():
    for operator in OPERATORS:
        hosts = [h for s in operator.sources for h in s.hosts]
        hosts += list(operator.lookup.hosts) if operator.lookup else []
        for host in hosts:
            assert not forbidden_host(host), f"{operator.id}: {host}"
