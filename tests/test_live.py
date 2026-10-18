"""Every source and address lookup against the real site, run daily in CI to notice changes.

Skipped unless LIVE=1, so the regular test run stays offline. Each source is checked on
its own (fallbacks too): a failure names the operator and the source that broke.
"""

import asyncio
import os

import pytest

from ua_outages import AddressSource, Fetcher, SourceBlocked, now_kyiv, operator
from ua_outages.http import new_session
from ua_outages.registry import OPERATORS, available_operators

pytestmark = pytest.mark.skipif(os.environ.get("LIVE") != "1", reason="set LIVE=1 to query the real sources")

#: Per-address sources are checked through a looked-up address below.
CASES = [(o, s) for o in available_operators() for s in o.sources if not isinstance(s, AddressSource)]
#: What a person would type at each step of the search; the best match is taken.
ADDRESSES = {
    "dtek-kem": ["Хрещатик", "1"],
    "dtek-dnem": ["Дніпро", "Шевченка", "1"],
    "cek": ["Дніпровське", "Набережна", "1"],
    "lviv-oe": ["Львів", "Ринок", "1"],  # the city hall
    "mykolaiv-oe": ["Миколаївський", "Миколаїв", "Шевченка", "1"],
    "poltava-oe": ["Полтавська об", "Полтава", "Соборності", "10"],
    "zhytomyr-oe": ["Житомирський", "Житомир", "Київська", "5"],
    "ternopil-oe": ["Тернопіль", "Руська", "1"],
}


def _summary(line: str) -> None:
    if path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    print(line)


def test_every_lookup_has_a_live_case():
    assert {o.id for o in OPERATORS if o.lookup} == set(ADDRESSES)


@pytest.mark.parametrize("operator_id", ADDRESSES)
def test_address_lookup(operator_id):
    op = operator(operator_id)
    lookup = op.lookup
    if lookup.ukraine_only and os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.skip("the site blocks requests from outside Ukraine")

    async def search():
        async with new_session() as session:
            http = Fetcher(session, lookup.hosts, max_bytes=lookup.max_bytes)
            path = []
            for query in ADDRESSES[operator_id]:
                choices = await lookup.options(http, [c.id for c in path], query)
                assert choices, f"{operator_id}: nothing found for {query!r} at step {len(path) + 1}"
                path.append(choices[0])
                await asyncio.sleep(lookup.request_gap)
            resolution = await lookup.resolve(http, [c.id for c in path])
            schedules = None
            if op.by_address:
                source = op.sources[0]
                assert source.valid_address(resolution.address)
                schedules = await source.fetch_address(Fetcher(session, source.hosts), now_kyiv(), resolution.address)
            return path, resolution, schedules

    path, resolution, schedules = asyncio.run(search())
    address = ", ".join(c.label for c in path)
    assert resolution.groups, f"{operator_id}: {address} is in no group ({resolution.other})"
    assert set(resolution.groups) <= set(op.groups), f"{operator_id}: unknown groups {resolution.groups}"
    line = f"- **{operator_id}** адреса: {address} → {', '.join(resolution.groups)}"
    if schedules is not None:
        for schedule in schedules.values():
            assert set(schedule.mentioned_groups()) <= set(op.groups)
            assert not schedule.unknown, f"{operator_id}: unparsed groups {sorted(schedule.unknown)}"
        line += f"; графік: {', '.join(f'{d:%d.%m}' for d in schedules) or 'не опубліковано'}"
    _summary(line)


@pytest.mark.parametrize("operator, source", CASES, ids=[f"{o.id}-{s.name}" for o, s in CASES])
def test_source(operator, source):
    if source.ukraine_only and os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.skip("the site blocks requests from outside Ukraine; the bot's own alerts cover it")
    async def fetch():
        async with new_session() as session:
            return await source.fetch(Fetcher(session, source.hosts), now_kyiv())

    try:
        schedules = asyncio.run(fetch())
    except SourceBlocked as err:
        # Bot protection on GitHub's IPs comes and goes; it says nothing about the format,
        # and the bot falls back to the operator's next source when it happens.
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.skip(f"blocked by the site's bot protection: {err}")
        raise
    unknown_groups = set()
    lines = []
    for day, schedule in schedules.items():
        extra = set(schedule.mentioned_groups()) - set(operator.groups)
        assert not extra, f"{operator.id}: groups the registry does not know: {sorted(extra)}"
        unknown_groups |= set(schedule.unknown)
        lines.append(f"{day:%d.%m}: {len(schedule.groups)} груп, нерозпізнано {len(schedule.unknown)}")
    _summary(f"- **{operator.id}** / {source.name}: " + ("; ".join(lines) or "графіків немає"))
    assert not unknown_groups, f"{operator.id}: unparsed groups {sorted(unknown_groups)}"
