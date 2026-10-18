"""One fetch per operator (or per address) for every config entry that needs it.

Several entries often watch the same operator (two groups in one city): they share the
answer instead of each asking the operator's site. Sources are tried in order, like the bot
does: the primary one first, then the fallbacks.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import date

from aiohttp import DummyCookieJar
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import SHARED_FETCH_TTL
from .ua_outages import AddressSource, DaySchedule, FetchError, Fetcher, Operator, now_kyiv
from .ua_outages.http import CachingFetcher, ResponseCache, Throttle, redact, ssl_context

_LOGGER = logging.getLogger(__name__)

#: Address directories are queried while a person waits in the config flow: answers are
#: cached briefly so going back a step costs nothing.
LOOKUP_CACHE_TTL = 600
LOOKUP_CACHE_BYTES = 8 * 1024 * 1024


class SourceHub:
    def __init__(self, hass: HomeAssistant) -> None:
        # Its own session: some sites set bot-protection cookies we don't want in HA's shared jar.
        self.session = async_create_clientsession(hass, cookie_jar=DummyCookieJar())
        self._cache: dict[tuple[str, str | None], tuple[float, dict[date, DaySchedule]]] = {}
        self._locks: defaultdict[tuple[str, str | None], asyncio.Lock] = defaultdict(asyncio.Lock)
        #: Which source served the last successful fetch, per operator/address.
        self.last_source: dict[tuple[str, str | None], str] = {}
        self._lookup_cache = ResponseCache(LOOKUP_CACHE_TTL, LOOKUP_CACHE_BYTES)
        self._lookup_throttles: dict[str, Throttle] = {}

    def fetcher(self, hosts: frozenset[str], **kwargs) -> Fetcher:
        # ssl_context() was loaded in an executor when the integration was set up.
        return Fetcher(self.session, hosts, tls=ssl_context(), **kwargs)

    def lookup_fetcher(self, op: Operator) -> CachingFetcher:
        lookup = op.lookup
        assert lookup is not None
        throttle = self._lookup_throttles.setdefault(op.id, Throttle(lookup.request_gap))
        return CachingFetcher(
            self.session,
            lookup.hosts,
            cache=self._lookup_cache,
            throttle=throttle,
            max_bytes=lookup.max_bytes,
            tls=ssl_context(),
        )

    async def fetch(self, op: Operator, address: str | None = None) -> dict[date, DaySchedule]:
        """Schedules from the first of the operator's sources that answers."""
        key = (op.id, address)
        async with self._locks[key]:
            cached = self._cache.get(key)
            if cached is not None and time.monotonic() - cached[0] < SHARED_FETCH_TTL:
                return cached[1]
            errors = []
            for source in op.sources:
                http = self.fetcher(source.hosts)
                try:
                    if isinstance(source, AddressSource):
                        if address is None or not source.valid_address(address):
                            raise FetchError("this entry has no valid address")
                        result = await source.fetch_address(http, now_kyiv(), address)
                    else:
                        result = await source.fetch(http, now_kyiv())
                except FetchError as err:
                    errors.append(f"{source.name}: {redact(str(err))}")
                    continue
                except Exception as err:  # a response the parser never expected
                    _LOGGER.debug("%s crashed", source.name, exc_info=True)
                    errors.append(f"{source.name}: {type(err).__name__}")
                    continue
                if errors:
                    _LOGGER.info("%s: used fallback %s (%s)", op.id, source.name, "; ".join(errors))
                self._cache[key] = (time.monotonic(), result)
                self.last_source[key] = source.name
                return result
            raise FetchError("; ".join(errors) or f"{op.id} has no sources")
