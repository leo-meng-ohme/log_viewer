"""Parsing of EasyLogger-formatted log lines emitted by Ohme V2 firmware.

The firmware enables ``ELOG_FMT_LVL | ELOG_FMT_TAG | ELOG_FMT_TIME`` for every
level, so a normal line looks like::

    I/MODEM [2026-07-01T10:11:11.950Z] Registered on network

The tag is written verbatim at whatever length it has. Tags of four characters
or fewer are space-padded up to four, followed in all cases by one separator
space, so no fixed tag width may be assumed.

Assert lines additionally enable directory, function and line info, which
appears in parentheses straight after the bracket group. No other level does,
so a leading ``(...)`` on any other level is ordinary message text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: SGR and other CSI escapes. Colour is disabled in firmware by default, but a
#: build with it enabled must degrade to correct text rather than a mangled tag.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: ``\S+`` takes the tag at any length; ``\s+`` absorbs padding plus separator.
_LINE_RE = re.compile(
    r"^(?P<level>[AEWIDV])/(?P<tag>\S+)\s+\[(?P<timestamp>[^\]]+)\]\s*(?P<rest>.*)$"
)

#: Applied to the remainder of assert lines only.
_SOURCE_RE = re.compile(r"^\((?P<source>[^)]*)\)\s*(?P<message>.*)$")

_ASSERT_LEVEL = "A"


@dataclass(frozen=True)
class LogLine:
    """One log line, parsed as far as it can be."""

    raw: str
    level: str | None
    tag: str | None
    timestamp: str | None
    message: str
    source: str | None


def parse(raw: str) -> LogLine:
    """Parse *raw* into a :class:`LogLine`.

    A line that does not match the EasyLogger format is returned with
    ``level``, ``tag``, ``timestamp`` and ``source`` all None and its full text
    as ``message``. Unparseable input is never discarded.
    """
    clean = _ANSI_RE.sub("", raw).rstrip("\r\n")

    match = _LINE_RE.match(clean)
    if match is None:
        return LogLine(
            raw=clean,
            level=None,
            tag=None,
            timestamp=None,
            message=clean,
            source=None,
        )

    level = match.group("level")
    rest = match.group("rest")
    source: str | None = None

    if level == _ASSERT_LEVEL:
        source_match = _SOURCE_RE.match(rest)
        if source_match is not None:
            source = source_match.group("source")
            rest = source_match.group("message")

    return LogLine(
        raw=clean,
        level=level,
        tag=match.group("tag"),
        timestamp=match.group("timestamp").strip(),
        message=rest,
        source=source,
    )
