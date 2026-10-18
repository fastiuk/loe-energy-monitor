"""A tiny, tolerant HTML tree for picking cells out of schedule pages.

Built on the standard library parser, without recursion, so hostile nesting can neither
blow the stack nor take more than linear time.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from html.parser import HTMLParser

from ..http import FetchError

_VOID = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)
#: Elements a new sibling closes implicitly, as in browsers.
_AUTO_CLOSE = {
    "p": frozenset({"p"}),
    "li": frozenset({"li"}),
    "tr": frozenset({"tr", "td", "th"}),
    "td": frozenset({"td", "th"}),
    "th": frozenset({"td", "th"}),
    "option": frozenset({"option"}),
}
_SKIP = frozenset({"script", "style", "template", "noscript"})
_BLOCK = frozenset(
    "address article br dd div dl dt h1 h2 h3 h4 h5 h6 header footer li ol p section table td th tr ul".split()
)
#: Deeper elements are attached flat to the last open one. Real pages nest a few dozen levels.
MAX_DEPTH = 256
#: Elements in one page. Real schedule pages have under 2,000; a node costs ~450 bytes, so
#: this keeps the tree of a hostile page to ~25 MB.
MAX_NODES = 50_000
_SPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list[Node | str] = field(default_factory=list)

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    def iter(self) -> Iterator[Node]:
        """This node and all element descendants, in document order."""
        stack: list[Node] = [self]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(c for c in reversed(node.children) if isinstance(c, Node))

    def find_all(self, tag: str | None = None, *, cls: str | None = None, id: str | None = None) -> list[Node]:
        return [
            n
            for n in self.iter()
            if (tag is None or n.tag == tag)
            and (cls is None or cls in n.classes)
            and (id is None or n.attrs.get("id") == id)
        ]

    def find(self, tag: str | None = None, *, cls: str | None = None, id: str | None = None) -> Node | None:
        return next(
            (
                n
                for n in self.iter()
                if (tag is None or n.tag == tag)
                and (cls is None or cls in n.classes)
                and (id is None or n.attrs.get("id") == id)
            ),
            None,
        )

    def text(self) -> str:
        """All text inside, with runs of whitespace collapsed to one space."""
        parts: list[str] = []
        stack: list[Node | str] = [self]
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                parts.append(item)
            else:
                if item.tag in _BLOCK:
                    # "<p>08:00-10:00</p><p>20:00-22:00</p>" must not run together.
                    parts.append(" ")
                    stack.append(" ")
                stack.extend(reversed(item.children))
        return _SPACE_RE.sub(" ", "".join(parts)).strip()


class TooManyNodes(FetchError):
    """The page has more elements than any real schedule page (see ``MAX_NODES``)."""


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.stack = [self.root]
        self.nodes = 0
        #: Open elements by tag, so an end tag nothing matches costs O(1) instead of a scan.
        self.open = Counter[str]()
        self.skip = 0

    def _node(self, tag: str, attrs: list[tuple[str, str | None]]) -> Node:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise TooManyNodes(f"over {MAX_NODES} elements")
        return Node(tag, {k: v or "" for k, v in attrs})

    def _close_from(self, index: int) -> None:
        for node in self.stack[index:]:
            self.open[node.tag] -= 1
        del self.stack[index:]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip:
            if tag in _SKIP:
                self.skip += 1
            return
        if tag in _SKIP:
            self.skip = 1
            return
        closes = _AUTO_CLOSE.get(tag)
        if closes and len(self.stack) > 1 and self.stack[-1].tag in closes:
            self._close_from(len(self.stack) - 1)
        node = self._node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in _VOID and len(self.stack) < MAX_DEPTH:
            self.stack.append(node)
            self.open[tag] += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if not self.skip:
            self.stack[-1].children.append(self._node(tag, attrs))

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag in _SKIP:
                self.skip -= 1
            return
        # Close up to the matching element; a stray end tag is ignored. Each scan removes
        # what it passes, so the total work stays linear in the number of elements.
        if self.open[tag] <= 0:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                self._close_from(i)
                return

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.stack[-1].children.append(data)


def parse_html(markup: str) -> Node:
    builder = _Builder()
    builder.feed(markup)
    builder.close()
    return builder.root
