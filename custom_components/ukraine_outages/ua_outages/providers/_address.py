"""Helpers for address lookups: cleaning directory text and matching what users type.

Directory entries and typed queries are untrusted: labels lose control and bidi characters
and get a length cap, ids are checked before they go into a request, and every pattern
here runs in linear time.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

from .base import Choice, fail

#: Most choices a lookup returns for one query; callers show fewer and ask to narrow down.
MAX_CHOICES = 40
MAX_LABEL = 80
MAX_QUERY = 60
#: Entries in one directory response; real lists have a few thousand at most.
MAX_ENTRIES = 50_000

# Control characters, soft hyphen, zero-width and bidi controls: invisible or reordering text.
_INVISIBLE_RE = re.compile("[\x00-\x1f\x7f-\x9f­​-‏ -‮⁠-⁯﻿]")
_SPACE_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[\W_]+")
_APOSTROPHES = str.maketrans(dict.fromkeys("’ʼ`′‘´", "'"))
#: "вул. ", "просп.", "м.", "с. " … at the start: users type them or not, directories differ.
_KIND_RE = re.compile(
    r"^(?:вулиця|вул|проспект|просп|пр-т|пр|провулок|пров|бульвар|бульв|бул|площа|пл|"
    r"місто|м|село|с|селище|смт|с-ще|будинок|буд|№)(?:\.\s{0,3}|\s{1,3})",
    re.IGNORECASE,
)
_DIGITS_RE = re.compile(r"([0-9]{1,9})")
# ASCII digits only: "\d" also takes "١٢" and other scripts' digits.
_ID_RE = re.compile(r"[0-9]{1,12}")
_GROUP_RE = re.compile(r"\s{0,3}([0-9]{1,2})\s{0,3}[.,/-]\s{0,3}([0-9]{1,2})\s{0,3}")


#: Unicode categories that never belong in a street name or a typed query: controls and line
#: or paragraph separators become spaces; format characters (bidi marks, zero-width and tag
#: characters that can smuggle hidden text), private-use, surrogates and unassigned go.
_AS_SPACE = frozenset({"Cc", "Zl", "Zp"})
_DROPPED = frozenset({"Cf", "Co", "Cs", "Cn"})


def _visible(text: str) -> str:
    return "".join(
        " " if (category := unicodedata.category(c)) in _AS_SPACE else "" if category in _DROPPED else c
        for c in text
    )


def clean_label(value: Any, limit: int = MAX_LABEL) -> str | None:
    """Directory text fit to show: one line, visible characters only, at most ``limit``."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    text = _SPACE_RE.sub(" ", _visible(_INVISIBLE_RE.sub(" ", value[: limit * 4]))).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text or None


def clean_query(text: str) -> str:
    """What the user typed, as one line of at most ``MAX_QUERY`` visible characters."""
    return _SPACE_RE.sub(" ", _visible(_INVISIBLE_RE.sub(" ", text[: MAX_QUERY * 4]))).strip()[:MAX_QUERY]


def numeric_id(value: Any) -> str | None:
    """A directory id that is safe in a URL path or query: digits only."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    return value if isinstance(value, str) and _ID_RE.fullmatch(value) else None


def require_id(value: str) -> str:
    """An id from a search path, checked again before it goes into a request."""
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError("not a directory id")
    return value


def normalize_group(value: Any) -> str | None:
    """ "3.2", "3,2", "3-2", "3/2" → "3.2"; anything else is not a group."""
    if isinstance(value, str) and (m := _GROUP_RE.fullmatch(value)):
        return f"{int(m[1])}.{int(m[2])}"
    return None


def entries(payload: Any, what: str, key: str | None = None) -> list[Any]:
    """The list a directory answered with (optionally under ``key``), bounded in size."""
    if key is not None:
        payload = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(payload, list) or len(payload) > MAX_ENTRIES:
        fail(f"{what}: expected a list")
    return payload


def strip_kind(text: str) -> str:
    """ "вул. Руська" → "Руська", "м.Полтава" → "Полтава"."""
    for _ in range(2):  # "м. с. Х" is not a thing, but "буд. №5" is
        text = _KIND_RE.sub("", text, count=1)
    return text


def fold(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold().translate(_APOSTROPHES)


def compact(text: str) -> str:
    """Letters and digits only: "10-А" and "10 а" both become "10а"."""
    return _NON_WORD_RE.sub("", fold(text))


def words(query: str) -> list[str]:
    return [w for w in (compact(part) for part in fold(strip_kind(query)).split()) if w]


def search_term(query: str) -> str:
    """The longest word of a query, for directories that search by one substring."""
    return max(fold(strip_kind(query)).split(), key=len, default="")


def matches(label: str, query: str) -> bool:
    """Every word of the query appears in the label, ignoring case, punctuation and kind."""
    target = compact(strip_kind(label))
    return all(w in target for w in words(query))


def natural_key(label: str) -> tuple[tuple[int, int, str], ...]:
    """Sort "2" before "10" and "10" before "10-А"."""
    return tuple(
        (0, int(part), "") if _DIGITS_RE.fullmatch(part) else (1, 0, part)
        for part in _DIGITS_RE.split(fold(label))
        if part
    )


def pick(choices: Iterable[Choice], query: str, limit: int = MAX_CHOICES) -> list[Choice]:
    """Choices matching ``query``: exact first, then by prefix, then the rest; no duplicates."""
    unique = list({c.id: c for c in choices}.values())
    if not query.strip():
        return sorted(unique, key=lambda c: natural_key(strip_kind(c.label)))[:limit]
    whole = "".join(words(query))

    def rank(choice: Choice) -> tuple[int, tuple[tuple[int, int, str], ...]]:
        name = strip_kind(choice.label)
        target = compact(name)
        order = 0 if target == whole else 1 if target.startswith(whole) else 2
        return order, natural_key(name)

    return sorted((c for c in unique if matches(c.label, query)), key=rank)[:limit]
