import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from multidict import CIMultiDict

from ua_outages.http import FetchError, Response
from ua_outages.models import KYIV

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str):
    return json.loads(fixture_text(name))


def kyiv(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=KYIV)


def spans(schedule, group: str) -> list[tuple[str, str, bool]]:
    """Outages of a group as ("HH:MM", "HH:MM", possible) for compact asserts."""
    day = schedule.day

    def fmt(moment: datetime) -> str:
        local = moment.astimezone(KYIV)
        return "24:00" if local.date() > day and local.hour == local.minute == 0 else f"{local:%H:%M}"

    return [(fmt(o.start), fmt(o.end), o.possible) for o in schedule.outages(group)]


@dataclass
class Call:
    method: str
    url: str
    params: dict
    data: dict
    headers: dict


class FakeHttp:
    """Stands in for ``Fetcher``: answers by URL suffix from fixtures and records every request.

    A route's value is the body (str or bytes), or a function of the call returning it;
    ``None`` or an unknown URL is an HTTP 404.
    """

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[Call] = []

    async def get(self, url, *, params=None, headers=None):
        return self._answer(Call("GET", url, dict(params or {}), {}, dict(headers or {})))

    async def post(self, url, *, data=None, params=None, headers=None):
        return self._answer(Call("POST", url, dict(params or {}), dict(data or {}), dict(headers or {})))

    def _answer(self, call: Call) -> Response:
        self.calls.append(call)
        for suffix, body in self.routes.items():
            if call.url.endswith(suffix):
                if callable(body):
                    body = body(call)
                if body is None:
                    break
                raw = body if isinstance(body, bytes) else body.encode()
                return Response(call.url, 200, CIMultiDict(), raw, "utf-8")
        raise FetchError(f"{call.method} {call.url}?secret=1: HTTP 404")


def address_fixture(name: str) -> str:
    return fixture_text(f"address/{name}")
