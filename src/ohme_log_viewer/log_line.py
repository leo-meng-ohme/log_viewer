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

Lines also arrive carrying NUL padding. The CLI framework sends its entire
512-byte TX buffer for every chunk of a continued command — ``sizeof``, not
``strlen``, over a buffer it zeroed first (``cli.c``, the
``CLI_CMD_CONTINUE_EXTRACTION`` branch) — so each chunk of an extraction is
padded out with NULs. There is no newline among them, so they are not a line of
their own: they arrive glued to the text on either side, and left in place they
show as glyph-less boxes and stop the following log line parsing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: SGR and other CSI escapes. Colour is disabled in firmware by default, but a
#: build with it enabled must degrade to correct text rather than a mangled tag.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: Control characters that carry no text: chunk padding (NUL) and any other C0
#: control or DEL. Tab is kept, being real message content, and so are CR and LF
#: — the caller's line framing depends on them. Undecodable bytes are already
#: U+FFFD by this point and are deliberately left alone: unlike padding, they
#: mark data genuinely lost on the wire and should stay visible.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_non_text(raw: str) -> str:
    """Remove escape sequences and textless control characters from *raw*.

    Public because the reader needs the same notion of "characters the device
    put on the wire that are not text" to recognise its sentinel through chunk
    padding, and two definitions of that would drift apart.

    Order matters: taking the control characters out first would leave the
    printable remainder of an escape sequence — ``[0m`` — as message text.
    """
    return _CONTROL_RE.sub("", _ANSI_RE.sub("", raw))


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
    clean = strip_non_text(raw).rstrip("\r\n")

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


#: Lines matched verbatim after stripping. The first is the current firmware's
#: only framing output; the rest are pre-rewrite banner text, kept so devices
#: that have not been reflashed still display cleanly.
_NOISE_EXACT = frozenset(
    {
        "LOG_EXTRACT_DONE",
        "========== LOG EXTRACTION START ==========",
        "========== LOG EXTRACTION END ==========",
        "No archived logs found, checking ring buffer...",
        "--- All log files read and deleted, checking ring buffer ---",
        "--- Next archive ---",
        "Ring buffer is empty",
        "Log extraction completed successfully.",
        "All archived log files have been deleted.",
        "Ring buffer has been cleared.",
    }
)

#: (prefix, suffix) pairs for framing lines with variable content in the middle.
_NOISE_AFFIXES = (
    ("Found ", "reading in chronological order"),
    ("--- Archive ", "---"),
    ("--- Ring buffer:", "---"),
)


def is_noise(raw: str) -> bool:
    """Return True if *raw* is extract_logs framing text rather than a log
    entry.

    Device-emitted warnings and errors are deliberately not treated as noise —
    they signal a real problem and must stay visible.
    """
    stripped = strip_non_text(raw).strip()
    if not stripped:
        return True
    if stripped in _NOISE_EXACT:
        return True
    return any(
        stripped.startswith(prefix) and stripped.endswith(suffix)
        for prefix, suffix in _NOISE_AFFIXES
    )
