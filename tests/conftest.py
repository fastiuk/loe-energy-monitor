import importlib.util
import io
import json
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from types import ModuleType
from xml.sax.saxutils import escape

from multidict import CIMultiDict

from ua_outages.http import FetchError, Response
from ua_outages.models import KYIV

FIXTURES = Path(__file__).parent / "fixtures"
SCRIPTS = Path(__file__).parent.parent / "scripts"


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


def address_bytes(name: str) -> bytes:
    return (FIXTURES / "address" / name).read_bytes()


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def workbook(rows=(), *, sheet=None, parts=None, compression=zipfile.ZIP_DEFLATED) -> bytes:
    """A minimal XLSX: ``rows`` of text as inline strings, or ``sheet`` as the sheet's XML.

    ``parts`` adds or replaces parts by name (``None`` leaves one out).
    """
    if sheet is None:
        body = "".join(
            f'<row r="{r}">'
            + "".join(f'<c r="{chr(65 + c)}{r}" t="inlineStr"><is><t>{escape(v)}</t></is></c>' for c, v in enumerate(row))
            + "</row>"
            for r, row in enumerate(rows, 1)
        )
        sheet = f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{MAIN_NS}"><sheetData>{body}</sheetData></worksheet>'
    files = {
        "xl/workbook.xml": f'<workbook xmlns="{MAIN_NS}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>',
        "xl/worksheets/sheet1.xml": sheet,
        **(parts or {}),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, data in files.items():
            if data is not None:
                archive.writestr(name, data)
    return buffer.getvalue()


def load_script(name: str) -> ModuleType:
    """``scripts/<name>.py`` as a module: the scripts are not a package."""
    if (module := sys.modules.get(name)) is None:
        spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
        module = sys.modules[name] = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module
