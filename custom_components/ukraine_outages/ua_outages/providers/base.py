"""The interfaces sources implement: schedules, and optionally finding a group by address."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar, NoReturn

from ..http import MAX_BYTES, FetchError, Fetcher
from ..models import DaySchedule

# Only a plain path on the source's host; "@evil.example/x" or "//evil.example" would change the host.
_PLAIN_PATH_RE = re.compile(r"/[A-Za-z0-9_.-][A-Za-z0-9_./-]{0,299}")


class SourceFormatError(FetchError):
    """The source answered, but not in the format we parse."""


class SourceBlocked(FetchError):
    """The site's bot protection (Imperva, Cloudflare) refused the request."""


class NotApplied(FetchError):
    """The operator says hourly schedules do not apply now and shows nothing to read in their place
    (e.g. its address search is gone until they do)."""


class Kind(StrEnum):
    """What a source reads the schedule from."""

    API = "API"
    PAGE = "сторінка сайту"
    PICTURE = "картинка з сайту"
    #: The operator's own channel, one its website links to.
    TELEGRAM = "Telegram-канал"
    #: Someone else's copy of the operator's data.
    MIRROR = "неофіційне дзеркало"


def fail(message: str) -> NoReturn:
    raise SourceFormatError(message)


def url_on_host(base_url: str, path: object) -> str | None:
    """``base_url + path`` when the source gave a plain absolute path, else ``None``."""
    if isinstance(path, str) and _PLAIN_PATH_RE.fullmatch(path) and "//" not in path:
        return base_url + path
    return None


class Provider(ABC):
    """Fetches one operator's schedules from one source.

    Implementations are stateless apart from their parameters and caches that never change
    a result, so one instance can serve every fetch. ``fetch`` raises ``FetchError`` (or ``SourceFormatError``) when the source
    is unreachable or unrecognisable; an empty dict means nothing is published.
    """

    #: Hosts this source may contact; requests anywhere else are refused.
    hosts: ClassVar[frozenset[str]] = frozenset()
    #: Shortest sensible interval between fetches, in seconds.
    min_interval: ClassVar[int] = 300
    #: The site drops requests from outside Ukraine or from datacenters (GitHub's runners
    #: among them) but answers a home connection in Ukraine.
    ukraine_only: ClassVar[bool] = False
    #: The source tells on which days a schedule applies, not the hours: those are only on the
    #: operator's picture (``DaySchedule.image_url``), so every group is "unknown" by design.
    hours_on_picture: ClassVar[bool] = False
    #: What the schedule is read from.
    kind: ClassVar[Kind] = Kind.PAGE
    #: A public address of what is read, when it is the same for every fetch (no queue or
    #: address in it); ``link`` gives it.
    page: ClassVar[str | None] = None

    @property
    def link(self) -> str | None:
        return self.page

    @property
    def official(self) -> bool:
        """The operator's own website, API or channel, not someone else's mirror."""
        return self.kind is not Kind.MIRROR

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        """Schedules keyed by day, as of ``now`` (Kyiv time)."""

    def __repr__(self) -> str:
        return self.name


class AddressSource(Provider):
    """A source that serves only the groups of one address per request.

    The caller polls one address per group it needs (see ``AddressLookup`` for where the
    address keys come from) and combines the results. ``fetch`` alone has nothing to ask for.
    """

    #: Pause between requests for different addresses, in seconds.
    request_gap: ClassVar[float] = 1.0

    async def fetch(self, http: Fetcher, now: datetime) -> dict[date, DaySchedule]:
        return {}

    @abstractmethod
    def valid_address(self, address: str) -> bool:
        """Whether ``address`` is a key this source understands (it comes from storage)."""

    @abstractmethod
    async def fetch_address(self, http: Fetcher, now: datetime, address: str) -> dict[date, DaySchedule]:
        """Schedules of the groups ``address`` belongs to (usually one)."""


@dataclass(frozen=True)
class Choice:
    """One answer to an address step: an id for the next requests and a label to show.

    Both come from the operator's directory and are untrusted: the label is cleaned and
    shortened (``clean_label``), and lookups check ids before putting them in a request.
    """

    id: str
    label: str


@dataclass(frozen=True)
class Step:
    """One step of an address search, e.g. the street."""

    #: What to ask the user, in Ukrainian: "Напишіть назву вулиці".
    prompt: str
    #: Shown with the prompt: "Хрещатик".
    example: str = ""
    #: The whole list is one cheap request: offer it before anything is typed.
    browse: bool = False
    #: Part of the address shown to the user ("Тернопіль, вул. Руська, 1"); districts are not.
    in_label: bool = True
    #: The step asks for a house number rather than a name.
    house: bool = False


@dataclass(frozen=True)
class Resolution:
    """What an operator's directory says about one address."""

    #: Groups the address belongs to, most common first; empty when it is in none.
    groups: tuple[str, ...]
    #: Values the directory gave that are not groups (e.g. "Не входить"), for the message.
    other: tuple[str, ...] = ()
    #: For operators whose source works per address: the key ``AddressSource`` needs.
    address: str | None = None
    #: Why the address has no group right now, said instead of "it is in none" (e.g. the
    #: operator shows groups only while schedules apply).
    reason: str | None = None


class AddressLookup(ABC):
    """Finds the group of an address in an operator's open directory.

    A search walks ``steps`` (e.g. settlement → street → house): ``options`` lists the
    choices for the next step given the ids chosen so far (``path``), and ``resolve`` turns
    a full path into groups. Implementations are stateless; callers cache and rate-limit
    (see ``CachingFetcher``). They never look anything up by personal account (особовий
    рахунок), EIC or name, and never use endpoints behind a captcha.
    """

    #: Hosts the directory lives on; requests anywhere else are refused.
    hosts: ClassVar[frozenset[str]] = frozenset()
    #: Pause between requests to the directory, in seconds.
    request_gap: ClassVar[float] = 0.5
    #: Largest directory response to accept.
    max_bytes: ClassVar[int] = MAX_BYTES
    #: Shortest query the directory searches by (for steps that are not ``browse``).
    min_query: ClassVar[int] = 2
    #: Like ``Provider.ukraine_only``: the site does not answer connections from abroad.
    ukraine_only: ClassVar[bool] = False
    steps: tuple[Step, ...] = ()

    @property
    def name(self) -> str:
        return type(self).__name__

    @abstractmethod
    async def options(self, http: Fetcher, path: Sequence[str], query: str) -> list[Choice]:
        """Choices for step ``len(path)`` that match ``query``, best first, at most ``MAX_CHOICES``.

        An empty ``query`` lists the step if it is ``browse``, and returns nothing otherwise.
        """

    @abstractmethod
    async def resolve(self, http: Fetcher, path: Sequence[str]) -> Resolution:
        """The groups of the address ``path`` (one id per step) names."""

    def __repr__(self) -> str:
        return self.name
