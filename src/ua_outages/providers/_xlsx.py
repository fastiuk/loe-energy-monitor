"""The first sheet of an XLSX workbook as rows of text: untrusted input, so stdlib only and bounded.

An XLSX file is a zip of XML parts. Only the workbook, its relationships, the shared strings and
the first sheet are read: stored or deflated parts only, each capped before it is inflated, and
parsed with expat as a stream (no tree is built). A DOCTYPE or entity declaration is refused, and
rows, columns and cells are bounded. Home
Assistant ships no spreadsheet library, and these files need none of one's features.
"""

from __future__ import annotations

import io
import re
import zipfile
import zlib
from collections.abc import Callable
from xml.parsers import expat

from .base import fail

#: A real queue workbook is about 200 KB.
MAX_BYTES = 2 * 1024 * 1024
#: One part inflated; the largest real sheet is about 1.5 MB.
MAX_PART = 8 * 1024 * 1024
MAX_PARTS = 100
MAX_ROWS = 50_000
MAX_COLUMNS = 16
MAX_STRINGS = 200_000
#: Characters kept of one cell; the longest real one (a list of houses) has about 2400.
MAX_CELL = 10_000

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main "
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships "
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships "
_REF_RE = re.compile(r"([A-Z]{1,3})[0-9]{1,7}")
_INDEX_RE = re.compile(r"[0-9]{1,9}")
#: "worksheets/sheet1.xml" or "/xl/worksheets/sheet1.xml": a sheet part, nothing else.
_SHEET_RE = re.compile(r"(?:/xl/)?(worksheets/[A-Za-z0-9_]{1,40}\.xml)")
_SHARED = "xl/sharedStrings.xml"

Handler = Callable[..., None]


def _part(archive: zipfile.ZipFile, name: str, what: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError:
        fail(f"{what}: the workbook has no {name}")
    # bzip2 and LZMA inflate a whole block at once: only deflate is read in bounded steps.
    if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
        fail(f"{what}: {name} uses an unexpected compression")
    if info.file_size > MAX_PART:
        fail(f"{what}: {name} is {info.file_size} bytes inflated, over {MAX_PART}")
    try:
        with archive.open(info) as part:
            data = part.read(MAX_PART + 1)
    except (zipfile.BadZipFile, zlib.error, OSError, EOFError, RuntimeError, ValueError, NotImplementedError) as err:
        fail(f"{what}: {name} is unreadable ({type(err).__name__})")
    if len(data) > MAX_PART:
        fail(f"{what}: {name} is over {MAX_PART} bytes")
    return data


def _parse(data: bytes, name: str, what: str, start: Handler, end: Handler, chars: Handler | None = None) -> None:
    # The parser itself refuses a DOCTYPE, however the part is encoded: no byte search to fool.
    parser = expat.ParserCreate("utf-8", " ")
    parser.buffer_text = True

    def refuse(*_: object) -> None:
        fail(f"{what}: {name} declares a DOCTYPE or entities")

    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    if chars is not None:
        parser.CharacterDataHandler = chars
    try:
        parser.Parse(data, True)
    except expat.ExpatError as err:
        fail(f"{what}: {name} is not well-formed XML ({err})")


class _Text:
    """Text of the ``<t>`` elements of a string item, without phonetic runs, at most ``MAX_CELL``."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.size = 0
        self.in_t = False
        self.phonetic = 0

    def reset(self) -> None:
        self.parts, self.size = [], 0

    def start(self, tag: str) -> None:
        if tag == _MAIN + "rPh":
            self.phonetic += 1
        elif tag == _MAIN + "t" and not self.phonetic:
            self.in_t = True

    def end(self, tag: str) -> None:
        if tag == _MAIN + "rPh":
            self.phonetic -= 1
        elif tag == _MAIN + "t":
            self.in_t = False

    def add(self, data: str) -> None:
        if self.size < MAX_CELL:
            self.parts.append(data[: MAX_CELL - self.size])
            self.size += len(self.parts[-1])

    def value(self) -> str:
        return "".join(self.parts)


def _first_sheet(archive: zipfile.ZipFile, what: str) -> str:
    """The part name of the workbook's first sheet."""
    sheet_ids: list[str] = []
    targets: dict[str, str] = {}

    def workbook(tag: str, attrs: dict[str, str]) -> None:
        if tag == _MAIN + "sheet" and not sheet_ids:
            sheet_ids.append(attrs.get(_DOC_REL + "id", ""))

    def relationship(tag: str, attrs: dict[str, str]) -> None:
        if tag == _PKG_REL + "Relationship" and len(targets) < MAX_PARTS:
            targets[attrs.get("Id", "")] = attrs.get("Target", "")

    _parse(_part(archive, "xl/workbook.xml", what), "the workbook", what, workbook, lambda tag: None)
    _parse(_part(archive, "xl/_rels/workbook.xml.rels", what), "its relationships", what, relationship, lambda tag: None)
    if not sheet_ids or not (m := _SHEET_RE.fullmatch(targets.get(sheet_ids[0], ""))):
        fail(f"{what}: the workbook names no sheet")
    return "xl/" + m[1]


def _shared_strings(data: bytes, what: str) -> list[str]:
    strings: list[str] = []
    text = _Text()

    def start(tag: str, attrs: dict[str, str]) -> None:
        if tag == _MAIN + "si":
            text.reset()
        text.start(tag)

    def end(tag: str) -> None:
        text.end(tag)
        if tag == _MAIN + "si":
            if len(strings) >= MAX_STRINGS:
                fail(f"{what}: over {MAX_STRINGS} shared strings")
            strings.append(text.value())

    def chars(data: str) -> None:
        if text.in_t:
            text.add(data)

    _parse(data, "the shared strings", what, start, end, chars)
    return strings


def _column(letters: str) -> int:
    number = 0
    for letter in letters:
        number = number * 26 + ord(letter) - ord("A") + 1
    return number - 1


def _sheet_rows(data: bytes, shared: list[str], what: str) -> list[list[str]]:
    rows: list[list[str]] = []
    row: dict[int, str] = {}
    value = _Text()
    column: int | None = None  # of the cell being read
    kind = "n"
    in_v = False

    def start(tag: str, attrs: dict[str, str]) -> None:
        nonlocal column, kind, in_v
        if tag == _MAIN + "row":
            row.clear()
        elif tag == _MAIN + "c":
            ref = attrs.get("r")
            if ref is None:
                column = max(row, default=-1) + 1
            elif m := _REF_RE.fullmatch(ref):
                column = _column(m[1])
            else:
                fail(f"{what}: a cell with reference {ref[:20]!r}")
            kind = attrs.get("t", "n")
            value.reset()
        elif column is not None and tag == _MAIN + "v":
            in_v = True
        elif column is not None:
            value.start(tag)

    def end(tag: str) -> None:
        nonlocal column, in_v
        if tag == _MAIN + "row":
            if len(rows) >= MAX_ROWS:
                fail(f"{what}: over {MAX_ROWS} rows")
            rows.append([row.get(i, "") for i in range(max(row, default=-1) + 1)])
        elif tag == _MAIN + "c" and column is not None:
            text = value.value()
            if kind == "s":
                index = int(text) if _INDEX_RE.fullmatch(text.strip()) else -1
                if not 0 <= index < len(shared):
                    fail(f"{what}: a cell points outside the shared strings")
                text = shared[index]
            if column < MAX_COLUMNS:
                row[column] = text
            column = None
        elif tag == _MAIN + "v":
            in_v = False
        elif column is not None:
            value.end(tag)

    def chars(data: str) -> None:
        if in_v or value.in_t:
            value.add(data)

    _parse(data, "the sheet", what, start, end, chars)
    return rows


def read_sheet(data: bytes, what: str) -> list[list[str]]:
    """Rows of the workbook's first sheet as text ("" for an empty cell), at most ``MAX_COLUMNS`` wide.

    Rows the file leaves out (empty ones) are not in the list. Raises ``SourceFormatError``.
    """
    if len(data) > MAX_BYTES:
        fail(f"{what}: workbook is {len(data)} bytes, over {MAX_BYTES}")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zlib.error, OSError, EOFError, RuntimeError, ValueError, NotImplementedError) as err:
        fail(f"{what}: not an XLSX workbook ({type(err).__name__})")
    with archive:
        if len(archive.infolist()) > MAX_PARTS:
            fail(f"{what}: the workbook has over {MAX_PARTS} parts")
        sheet = _first_sheet(archive, what)
        shared = _shared_strings(_part(archive, _SHARED, what), what) if _SHARED in archive.namelist() else []
        return _sheet_rows(_part(archive, sheet, what), shared, what)
