"""HTTP access for sources: only the hosts a source declares, bounded in size and time.

Everything a source returns is untrusted. A redirect to another host, a huge body or a
slow answer must not turn the bot into a proxy or stall it.
"""

from __future__ import annotations

import asyncio
import codecs
import json
import re
import ssl
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from typing import Any

import aiohttp
import certifi
from multidict import CIMultiDict
from yarl import URL

USER_AGENT = "ukraine-energy-monitor/0.4.6 (+https://github.com/fastiuk/ukraine-energy-monitor)"
#: The biggest real page (a DTEK shutdowns page) is about 1 MB.
MAX_BYTES = 5 * 1024 * 1024
#: Never contacted, whatever a source declares: the aggressor's domains, where the occupation
#: authorities of Crimea and other occupied territories publish ("рф" is "xn--p1ai").
FORBIDDEN_SUFFIXES = (
    ".ru", ".su", ".xn--p1ai", ".рф",
    ".xn--p1acf", ".рус", ".xn--80adxhks", ".москва", ".moscow", ".tatar", ".xn--d1acj3b", ".дети",
)  # fmt: skip
MAX_REDIRECTS = 3
_REDIRECTS = {301, 302, 303, 307, 308}


@cache
def ssl_context() -> ssl.SSLContext:
    """Mozilla's CA set (same on macOS and in the image) plus intermediates some servers omit."""
    context = ssl.create_default_context(cafile=certifi.where())
    context.load_verify_locations(cadata=(files(__package__) / "intermediates.pem").read_text("ascii"))
    # Python 3.13 trusts any certificate in the store as an anchor (PARTIAL_CHAIN). The bundled
    # intermediates are there to complete chains, not to be trusted on their own: a chain must
    # still end at a root from certifi.
    context.verify_flags &= ~ssl.VERIFY_X509_PARTIAL_CHAIN
    return context


def new_session() -> aiohttp.ClientSession:
    """A session for fetching sources: no cookies kept, bounded connections.

    Any session works with ``Fetcher`` (Home Assistant passes its shared one): the TLS
    context goes with each request.
    """
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(limit=20, limit_per_host=2),
        cookie_jar=aiohttp.DummyCookieJar(),
    )


def forbidden_host(host: str | None) -> bool:
    name = (host or "").lower().rstrip(".")
    return any(name == suffix[1:] or name.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES)


#: Charsets a source's Content-Type may select; anything else is read as UTF-8.
CHARSETS = frozenset({"utf-8", "cp1251", "koi8-u", "iso8859-1", "ascii"})  # codecs.lookup() names


def _charset(name: str | None) -> str:
    try:
        codec = codecs.lookup(name or "utf-8").name
    except LookupError:
        return "utf-8"
    return codec if codec in CHARSETS else "utf-8"


class FetchError(Exception):
    """A source could not be fetched or did not look like what we expect."""


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    headers: CIMultiDict[str]
    body: bytes
    charset: str | None

    def text(self, encoding: str | None = None) -> str:
        """The body as text: ``encoding`` if the source knows it, else an allowed charset from the response."""
        return self.body.decode(_charset(encoding or self.charset), errors="replace")

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except (ValueError, RecursionError) as err:  # RecursionError: absurdly deep nesting
            raise FetchError(f"{self.url}: response is not JSON ({err})") from err


class Fetcher:
    """Makes requests on behalf of one source, limited to the hosts it declares."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        hosts: Iterable[str],
        *,
        timeout: float = 30,
        max_bytes: int = MAX_BYTES,
        user_agent: str = USER_AGENT,
        schemes: Iterable[str] = ("https",),
        ports: Iterable[int] = (443,),
        tls: ssl.SSLContext | None = None,
    ) -> None:
        """``tls`` defaults to ``ssl_context()``, which reads CA files: in an event loop that
        must not block (Home Assistant), call it once in an executor first."""
        self._session = session
        self._ssl = tls or ssl_context()
        self._hosts = frozenset(hosts)
        self._schemes = frozenset(schemes)
        self._ports = frozenset(ports)
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._max_bytes = max_bytes
        self._user_agent = user_agent

    def allowed(self, url: str | URL) -> bool:
        url = URL(url)
        return (
            url.scheme in self._schemes
            and url.host in self._hosts
            and url.port in self._ports
            and not forbidden_host(url.host)
        )

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        return await self.request("GET", url, params=params, headers=headers)

    async def post(
        self,
        url: str,
        *,
        data: Mapping[str, str] | None = None,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        return await self.request("POST", url, params=params, data=data, headers=headers)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        request_headers = {"User-Agent": self._user_agent, **(headers or {})}
        target = URL(url)
        for _ in range(MAX_REDIRECTS + 1):
            if not self.allowed(target):
                raise FetchError(f"Refusing to fetch {target.scheme}://{target.host}: host not allowed")
            try:
                async with self._session.request(
                    method,
                    target,
                    params=params,
                    data=data,
                    headers=request_headers,
                    timeout=self._timeout,
                    allow_redirects=False,
                    ssl=self._ssl,
                ) as resp:
                    if resp.status in _REDIRECTS and "Location" in resp.headers:
                        target = resp.url.join(URL(resp.headers["Location"]))
                        params = None  # already part of the URL we were sent to
                        if resp.status == 303 or (resp.status in (301, 302) and method == "POST"):
                            method, data = "GET", None
                        continue
                    if resp.status >= 400:
                        raise FetchError(f"{method} {resp.url}: HTTP {resp.status}")
                    body = bytearray()
                    async for chunk in resp.content.iter_chunked(64 * 1024):
                        body += chunk
                        if len(body) > self._max_bytes:
                            raise FetchError(f"{method} {resp.url}: response is over {self._max_bytes} bytes")
                    return Response(
                        str(resp.url), resp.status, CIMultiDict(resp.headers), bytes(body), resp.charset
                    )
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                raise FetchError(f"{method} {target}: {err!r}") from err
        raise FetchError(f"{url}: too many redirects")


#: A query string runs to whitespace or the ": " that ends a URL in our error messages.
_QUERY_RE = re.compile(r"\?(?:[^\s:]|:(?!\s))*")


def redact(message: str) -> str:
    """An error message without query strings, which may carry what users typed or their address."""
    return _QUERY_RE.sub("?…", message)


class Throttle:
    """One request at a time, at least ``gap`` seconds apart (for directories users query)."""

    def __init__(self, gap: float) -> None:
        self.gap = gap
        self._lock: asyncio.Lock | None = None
        self._next = 0.0

    async def __aenter__(self) -> None:
        if self._lock is None:  # created on first use, inside the running loop
            self._lock = asyncio.Lock()
        await self._lock.acquire()
        loop = asyncio.get_running_loop()
        if (wait := self._next - loop.time()) > 0:
            try:
                await asyncio.sleep(wait)
            except BaseException:  # cancelled while waiting: __aexit__ won't run
                self._lock.release()
                raise

    async def __aexit__(self, *exc: object) -> None:
        assert self._lock is not None
        self._next = asyncio.get_running_loop().time() + self.gap
        self._lock.release()


class ResponseCache:
    """Successful responses for ``ttl`` seconds, at most ``max_bytes`` in total and ``max_entries`` (LRU).

    Each entry counts its body, headers, key and some overhead, so many tiny answers can't
    grow it past its budget either.
    """

    OVERHEAD = 1024

    def __init__(
        self, ttl: float, max_bytes: int, clock: Callable[[], float] = time.monotonic, *, max_entries: int = 5000
    ) -> None:
        self.ttl = ttl
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self._clock = clock
        self._items: OrderedDict[tuple[Any, ...], tuple[float, Response, int]] = OrderedDict()
        self._bytes = 0

    def get(self, key: tuple[Any, ...]) -> Response | None:
        item = self._items.get(key)
        if item is None:
            return None
        if item[0] <= self._clock():
            self._drop(key)
            return None
        self._items.move_to_end(key)
        return item[1]

    def _size(self, key: tuple[Any, ...], response: Response) -> int:
        headers = sum(len(k) + len(v) for k, v in response.headers.items())
        return len(response.body) + headers + len(repr(key)) + self.OVERHEAD

    def put(self, key: tuple[Any, ...], response: Response) -> None:
        size = self._size(key, response)
        if size > self.max_bytes:
            return
        if key in self._items:
            self._drop(key)
        now = self._clock()
        # Expired entries at the old end go first, then the least recently used.
        while self._items and next(iter(self._items.values()))[0] <= now:
            self._drop(next(iter(self._items)))
        self._items[key] = (now + self.ttl, response, size)
        self._bytes += size
        while self._bytes > self.max_bytes or len(self._items) > self.max_entries:
            self._drop(next(iter(self._items)))

    def _drop(self, key: tuple[Any, ...]) -> None:
        _, _, size = self._items.pop(key)
        self._bytes -= size


class CachingFetcher(Fetcher):
    """A ``Fetcher`` that answers repeated requests from a cache and spaces out the rest."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        hosts: Iterable[str],
        *,
        cache: ResponseCache,
        throttle: Throttle,
        **kwargs: Any,
    ) -> None:
        super().__init__(session, hosts, **kwargs)
        self._cache = cache
        self._throttle = throttle

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Response:
        key = (
            method,
            url,
            tuple(sorted((params or {}).items())),
            tuple(sorted((data or {}).items())),
            tuple(sorted((headers or {}).items())),
        )
        if (cached := self._cache.get(key)) is not None:
            return cached
        async with self._throttle:
            # Another search may have fetched the same thing while this one waited.
            if (cached := self._cache.get(key)) is not None:
                return cached
            response = await super().request(method, url, params=params, data=data, headers=headers)
        self._cache.put(key, response)
        return response
