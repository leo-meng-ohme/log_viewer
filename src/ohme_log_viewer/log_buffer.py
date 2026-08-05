"""Bounded store of parsed log lines, plus the per-tag display filter.

The deque held here is the single source of truth for what has been received.
The text pane is only a projection of it, which is what makes tag filtering
retroactive: re-enabling a tag re-renders its earlier lines rather than only
letting new ones through.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterator

from .log_line import LogLine

#: Buffer key for lines that did not parse. ``ELOG_TAG`` values are C
#: identifiers, so no real device tag can collide with this.
UNPARSED_TAG = "(unparsed)"

#: Ring buffer capacity.
MAX_LINES = 50_000


class LogBuffer:
    """Ring buffer of :class:`LogLine` with a per-tag enabled flag."""

    def __init__(self, max_lines: int = MAX_LINES) -> None:
        self._lines: deque[LogLine] = deque(maxlen=max_lines)
        self._tags: list[str] = []
        self._enabled: dict[str, bool] = {}
        self._counts: dict[str, int] = {}

    @staticmethod
    def key_for(line: LogLine) -> str:
        """Return the buffer key for *line*, mapping a null tag to UNPARSED_TAG."""
        return line.tag if line.tag is not None else UNPARSED_TAG

    def append(self, line: LogLine) -> bool:
        """Store *line*. Returns True if it passes the current filter."""
        key = self.key_for(line)
        if key not in self._enabled:
            self._tags.append(key)
            self._enabled[key] = True
            self._counts[key] = 0
        self._counts[key] += 1
        self._lines.append(line)
        return self._enabled[key]

    def clear(self) -> None:
        """Drop every stored line, keeping discovered tags and their flags.

        Clearing is for cutting noise before a test run, so losing the filter
        setup at that moment would be the wrong behaviour.
        """
        self._lines.clear()
        for key in self._counts:
            self._counts[key] = 0

    def tags(self) -> list[str]:
        """Discovered tags, in the order they were first seen."""
        return list(self._tags)

    def count_for(self, tag: str) -> int:
        """Lines received for *tag* since the last clear, ignoring eviction."""
        return self._counts.get(tag, 0)

    def is_tag_enabled(self, tag: str) -> bool:
        return self._enabled.get(tag, True)

    def set_tag_enabled(self, tag: str, on: bool) -> None:
        if tag in self._enabled:
            self._enabled[tag] = on

    def visible(self) -> Iterator[LogLine]:
        """Yield stored lines whose tag is enabled, oldest first."""
        for stored in self._lines:
            if self._enabled.get(self.key_for(stored), True):
                yield stored

    def total(self) -> int:
        """Number of lines currently stored."""
        return len(self._lines)
