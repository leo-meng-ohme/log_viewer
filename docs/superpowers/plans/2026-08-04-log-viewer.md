# Ohme Log Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone PySide6 desktop app that streams EasyLogger output from an Ohme Home Pro V2 device over USB serial, colour-codes it, and lets the user filter by log TAG and clear the view.

**Architecture:** Three layers with hard boundaries. `log_line.py` and `log_buffer.py` are pure Python (no Qt, no serial) and hold all the logic worth testing. `serial_link.py` is the only module that moves bytes on and off the port, and `reader.py` the only one that runs the `extract_logs` loop on its own thread. `ui/` owns the pipeline and sequences it — `MainWindow` calls `list_usb_ports()`, `link.open()`/`link.close()`, `is_noise()` and `parse()` to drive enumerate → open → noise-filter → parse → buffer → display — but it does no byte-level I/O of its own and implements no parsing of its own.

**Tech Stack:** Python 3.13, PySide6, pyserial, pytest.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-04-log-viewer-design.md`. Read it before starting.
- **Fresh project.** Do not import from, depend on, or vendor any part of the `device_cli` package in `firmware-hq/ohme_fw_cli`. It is a behavioural reference only. All serial code is written directly against `pyserial`.
- **Commit once per task**, after its tests pass. The repository owner's standing no-commit rule is explicitly waived for this repo only. Use a Conventional Commits subject (`feat:`, `test:`, `docs:`) and end every commit message with:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
  Never amend or rebase another task's commit. Never commit `.venv/`.
- Package name: `ohme_log_viewer`, under `src/`. Console entry point: `ohme-log-viewer`.
- Serial parameters are fixed: 115200 baud, 8 data bits, no parity, 1 stop bit, CRLF line endings.
- Streaming is always continuous. There must be no continuous-mode toggle anywhere in the UI.
- Log levels are exactly `A E W I D V`.
- The tag on a log line is an arbitrary-length non-whitespace token. No code may assume a fixed tag width.
- Unparseable lines are never silently dropped; they are grouped under the pseudo-tag `(unparsed)`.
- `LOG_EXTRACT_DONE`, blank lines, and the legacy banner lines listed in Task 2 are the only lines discarded.
- Ring buffer cap: 50,000 lines.
- All Python is formatted to a 100-character line length.

---

### Task 1: Project scaffold and the log line parser

Creates the project, the venv, and the parser. The parser is the foundation every other module consumes, so its scaffold belongs here rather than in a separate setup task.

**Files:**
- Create: `pyproject.toml`
- Create: `src/ohme_log_viewer/__init__.py`
- Create: `src/ohme_log_viewer/log_line.py`
- Create: `tests/test_log_line.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `LogLine` frozen dataclass with fields `raw: str`, `level: str | None`, `tag: str | None`, `timestamp: str | None`, `message: str`, `source: str | None`; and `parse(raw: str) -> LogLine`.

- [ ] **Step 1: Create the project scaffold and virtual environment**

Create `pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "ohme-log-viewer"
version = "0.1.0"
description = "Live USB log viewer for Ohme Home Pro V2 devices"
requires-python = ">=3.11"
dependencies = [
    "PySide6>=6.6",
    "pyserial>=3.5",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "black>=24.0", "ruff>=0.5"]

[project.scripts]
ohme-log-viewer = "ohme_log_viewer.__main__:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.black]
line-length = 100
# Pinned to the floor of requires-python. Left unset, black infers the whole
# open-ended range ">=3.11" — including Python versions that do not exist yet —
# and then warns that the interpreter running it cannot verify its own output.
target-version = ["py311"]

[tool.ruff]
line-length = 100
```

Create an empty `src/ohme_log_viewer/__init__.py`.

Then run, from the `log_viewer` directory:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Expected: installs PySide6, pyserial and pytest without error. PySide6 is a large download; allow it a few minutes.

All later `pytest` and `python` commands in this plan mean `.venv/bin/pytest` and `.venv/bin/python`.

- [ ] **Step 2: Write the failing tests**

Create `tests/test_log_line.py`:

```python
from ohme_log_viewer.log_line import LogLine, parse


def test_parses_a_normal_info_line():
    line = parse("I/MODEM [2026-07-01T10:11:11.950Z] Registered on network")
    assert line.level == "I"
    assert line.tag == "MODEM"
    assert line.timestamp == "2026-07-01T10:11:11.950Z"
    assert line.message == "Registered on network"
    assert line.source is None


def test_parses_a_short_padded_tag_without_padding_artefacts():
    # A 3-char tag gets 1 pad space plus the mandatory separator space.
    line = parse("I/OTA   [2026-07-01T10:11:11.950Z] Download started")
    assert line.tag == "OTA"
    assert line.message == "Download started"


def test_parses_a_long_unpadded_tag():
    raw = "W/CHARGING_SESSION_MGR [2026-07-01T10:11:11.950Z] Session aborted"
    line = parse(raw)
    assert line.tag == "CHARGING_SESSION_MGR"
    assert line.level == "W"
    assert line.message == "Session aborted"


def test_parses_every_level_letter():
    for letter in ("A", "E", "W", "I", "D", "V"):
        line = parse(f"{letter}/TAG  [2026-07-01T10:11:11.950Z] hello")
        assert line.level == letter, letter
        assert line.tag == "TAG"


def test_parses_assert_line_with_source_info_and_strips_timestamp():
    # Assert lines enable DIR|FUNC|LINE, and the empty thread-info field
    # leaves a trailing space inside the bracket group.
    raw = "A/SAFETY [2026-07-01T10:11:11.950Z ] (bsp_rcd.c check_rcd:142)RCD self-test failed"
    line = parse(raw)
    assert line.level == "A"
    assert line.tag == "SAFETY"
    assert line.timestamp == "2026-07-01T10:11:11.950Z"
    assert line.source == "bsp_rcd.c check_rcd:142"
    assert line.message == "RCD self-test failed"


def test_non_assert_parenthesised_text_stays_in_the_message():
    # Only assert lines carry source info, so this must not be mistaken for it.
    line = parse("I/OCPP  [2026-07-01T10:11:11.950Z] (retry 3) connecting")
    assert line.source is None
    assert line.message == "(retry 3) connecting"


def test_accepts_the_device_fallback_timestamp():
    line = parse("E/RTC   [0000-00-00T00:00:00.000Z] clock not set")
    assert line.timestamp == "0000-00-00T00:00:00.000Z"
    assert line.message == "clock not set"


def test_strips_ansi_escapes_before_parsing():
    line = parse("\x1b[32mI/MODEM [2026-07-01T10:11:11.950Z] link up\x1b[0m")
    assert line.tag == "MODEM"
    assert line.message == "link up"
    assert "\x1b" not in line.raw


def test_unparseable_line_keeps_its_text_under_a_null_tag():
    line = parse("MainMenu> ")
    assert line.level is None
    assert line.tag is None
    assert line.timestamp is None
    assert line.message == "MainMenu> "
    assert isinstance(line, LogLine)


def test_strips_trailing_eol():
    line = parse("I/MODEM [2026-07-01T10:11:11.950Z] link up\r\n")
    assert line.message == "link up"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_log_line.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'ohme_log_viewer.log_line'`.

- [ ] **Step 4: Write the implementation**

Create `src/ohme_log_viewer/log_line.py`:

```python
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
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_log_line.py -v`
Expected: 10 passed.

---

### Task 2: The noise filter

**Files:**
- Modify: `src/ohme_log_viewer/log_line.py` (append `is_noise`)
- Modify: `tests/test_log_line.py` (append noise tests)

**Interfaces:**
- Consumes: nothing from Task 1's runtime code; lives in the same module.
- Produces: `is_noise(raw: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_log_line.py`:

```python
from ohme_log_viewer.log_line import is_noise


def test_the_completion_sentinel_is_noise():
    assert is_noise("LOG_EXTRACT_DONE") is True
    assert is_noise("LOG_EXTRACT_DONE\r\n") is True


def test_blank_lines_are_noise():
    # Firmware emits a bare CRLF to flush a line left dangling by a
    # chunk-boundary-unaligned read.
    assert is_noise("") is True
    assert is_noise("\r\n") is True
    assert is_noise("   ") is True


def test_legacy_banner_lines_are_noise():
    for banner in (
        "========== LOG EXTRACTION START ==========",
        "========== LOG EXTRACTION END ==========",
        "No archived logs found, checking ring buffer...",
        "Ring buffer is empty",
        "Ring buffer has been cleared.",
        "Found 3 archived log file(s), reading in chronological order",
        "--- Archive 1/3: log_20260701.txt (512 bytes) ---",
        "--- Ring buffer: 78 bytes ---",
    ):
        assert is_noise(banner) is True, banner


def test_real_log_lines_are_not_noise():
    assert is_noise("I/MODEM [2026-07-01T10:11:11.950Z] link up") is False


def test_device_warnings_and_errors_are_not_noise():
    # These indicate a real problem on the device and must stay visible.
    assert is_noise("Warning: Could not delete log_20260701.txt") is False
    assert is_noise("Error reading archive") is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_log_line.py -v`
Expected: collection error — `ImportError: cannot import name 'is_noise'`.

- [ ] **Step 3: Write the implementation**

Append to `src/ohme_log_viewer/log_line.py`:

```python
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
    """Return True if *raw* is extract_logs framing text rather than a log entry.

    Device-emitted warnings and errors are deliberately not treated as noise —
    they signal a real problem and must stay visible.
    """
    stripped = _ANSI_RE.sub("", raw).strip()
    if not stripped:
        return True
    if stripped in _NOISE_EXACT:
        return True
    return any(
        stripped.startswith(prefix) and stripped.endswith(suffix)
        for prefix, suffix in _NOISE_AFFIXES
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_log_line.py -v`
Expected: 15 passed.

---

### Task 3: The log buffer and tag filter

**Files:**
- Create: `src/ohme_log_viewer/log_buffer.py`
- Create: `tests/test_log_buffer.py`

**Interfaces:**
- Consumes: `LogLine` from `ohme_log_viewer.log_line`.
- Produces: constants `UNPARSED_TAG = "(unparsed)"` and `MAX_LINES = 50_000`; class `LogBuffer` with `__init__(max_lines: int = MAX_LINES)`, `key_for(line: LogLine) -> str` (staticmethod), `append(line: LogLine) -> bool`, `clear() -> None`, `tags() -> list[str]`, `count_for(tag: str) -> int`, `is_tag_enabled(tag: str) -> bool`, `set_tag_enabled(tag: str, on: bool) -> None`, `visible() -> Iterator[LogLine]`, `total() -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_log_buffer.py`:

```python
from ohme_log_viewer.log_buffer import UNPARSED_TAG, LogBuffer
from ohme_log_viewer.log_line import parse


def line(tag: str, message: str = "msg", level: str = "I"):
    return parse(f"{level}/{tag} [2026-07-01T10:11:11.950Z] {message}")


def messages(buffer: LogBuffer) -> list[str]:
    return [item.message for item in buffer.visible()]


def test_appended_line_is_visible_by_default():
    buffer = LogBuffer()
    assert buffer.append(line("MODEM", "one")) is True
    assert messages(buffer) == ["one"]


def test_tags_are_discovered_in_arrival_order():
    buffer = LogBuffer()
    buffer.append(line("MODEM"))
    buffer.append(line("OCPP"))
    buffer.append(line("MODEM"))
    assert buffer.tags() == ["MODEM", "OCPP"]


def test_counts_are_kept_per_tag():
    buffer = LogBuffer()
    buffer.append(line("MODEM"))
    buffer.append(line("MODEM"))
    buffer.append(line("OCPP"))
    assert buffer.count_for("MODEM") == 2
    assert buffer.count_for("OCPP") == 1
    assert buffer.count_for("NOPE") == 0


def test_disabling_a_tag_hides_its_lines_and_reports_new_ones_hidden():
    buffer = LogBuffer()
    buffer.append(line("MODEM", "shown"))
    buffer.set_tag_enabled("MODEM", False)
    assert buffer.append(line("MODEM", "hidden")) is False
    assert messages(buffer) == []
    assert buffer.is_tag_enabled("MODEM") is False


def test_filtering_is_retroactive():
    buffer = LogBuffer()
    buffer.append(line("MODEM", "early"))
    buffer.append(line("OCPP", "other"))
    buffer.set_tag_enabled("MODEM", False)
    assert messages(buffer) == ["other"]
    buffer.set_tag_enabled("MODEM", True)
    assert messages(buffer) == ["early", "other"]


def test_unparsed_lines_group_under_the_unparsed_tag():
    buffer = LogBuffer()
    buffer.append(parse("MainMenu> "))
    assert buffer.tags() == [UNPARSED_TAG]
    assert buffer.count_for(UNPARSED_TAG) == 1
    buffer.set_tag_enabled(UNPARSED_TAG, False)
    assert messages(buffer) == []


def test_clear_empties_lines_but_keeps_tags_and_their_enabled_state():
    buffer = LogBuffer()
    buffer.append(line("MODEM"))
    buffer.append(line("OCPP"))
    buffer.set_tag_enabled("OCPP", False)

    buffer.clear()

    assert messages(buffer) == []
    assert buffer.total() == 0
    assert buffer.tags() == ["MODEM", "OCPP"]
    assert buffer.is_tag_enabled("OCPP") is False
    assert buffer.count_for("MODEM") == 0


def test_oldest_lines_are_evicted_at_the_cap():
    buffer = LogBuffer(max_lines=3)
    for index in range(5):
        buffer.append(line("MODEM", str(index)))
    assert messages(buffer) == ["2", "3", "4"]
    assert buffer.total() == 3


def test_setting_an_unknown_tag_is_ignored():
    buffer = LogBuffer()
    buffer.set_tag_enabled("NEVER_SEEN", False)
    assert buffer.tags() == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_log_buffer.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'ohme_log_viewer.log_buffer'`.

- [ ] **Step 3: Write the implementation**

Create `src/ohme_log_viewer/log_buffer.py`:

```python
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
```

- [ ] **Step 4: Run the full suite to verify it passes**

Run: `.venv/bin/pytest -v`
Expected: 24 passed.

---

### Task 4: The serial link

**Files:**
- Create: `src/ohme_log_viewer/serial_link.py`
- Create: `tests/test_serial_link.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `SerialLinkError(Exception)`; `PortInfo` frozen dataclass with `device: str` and `label: str`; `list_usb_ports() -> list[PortInfo]`; class `SerialLink` with `__init__(read_timeout_s: float = 0.3, serial_factory=serial.Serial)`, `is_open` property, `open(device: str, baud: int = 115200) -> None`, `close() -> None`, `write_line(text: str) -> None`, `read_line() -> str | None`.

`read_line` returns a complete line with its EOL stripped, or `None` when the read timed out with no complete line available. It never returns a partial line.

The timeout bounds the **call**, not just each underlying `port.read()`. A device streaming bytes with no newline in them — wrong baud rate, binary noise, a garbled boot message — would otherwise keep the loop fed forever: `port.read()` never returns empty, so the call never returns, the reader thread never gets to check for interruption (hanging Disconnect), and the buffer grows without bound. A deadline taken on entry prevents all three.

The injectable `serial_factory` is what makes this testable without hardware — tests pass a fake in place of `serial.Serial`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_serial_link.py`:

```python
import pytest

from ohme_log_viewer.serial_link import SerialLink, SerialLinkError


class FakeSerial:
    """Stands in for serial.Serial. Yields one scripted chunk per read()."""

    def __init__(self, chunks=None, raise_on_read=None, raise_on_write=None):
        self._chunks = list(chunks or [])
        self._raise_on_read = raise_on_read
        self._raise_on_write = raise_on_write
        self.written = b""
        self.closed = False

    @property
    def in_waiting(self):
        return len(self._chunks[0]) if self._chunks else 0

    def read(self, _size):
        if self._raise_on_read is not None:
            raise self._raise_on_read
        if not self._chunks:
            return b""  # timeout with nothing available
        return self._chunks.pop(0)

    def write(self, data):
        if self._raise_on_write is not None:
            raise self._raise_on_write
        self.written += data
        return len(data)

    def close(self):
        self.closed = True


def link_with(fake):
    link = SerialLink(serial_factory=lambda **kwargs: fake)
    link.open("/dev/fake")
    return link


def test_open_configures_the_port_as_115200_8n1():
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return FakeSerial()

    link = SerialLink(read_timeout_s=0.3, serial_factory=factory)
    link.open("/dev/fake")

    assert captured["port"] == "/dev/fake"
    assert captured["baudrate"] == 115200
    assert captured["bytesize"] == 8
    assert captured["parity"] == "N"
    assert captured["stopbits"] == 1
    assert captured["timeout"] == 0.3
    assert link.is_open is True


def test_open_failure_is_reported_as_serial_link_error():
    def factory(**kwargs):
        raise OSError("no such port")

    link = SerialLink(serial_factory=factory)
    with pytest.raises(SerialLinkError, match="no such port"):
        link.open("/dev/nope")
    assert link.is_open is False


def test_write_line_appends_crlf():
    fake = FakeSerial()
    link_with(fake).write_line("extract_logs")
    assert fake.written == b"extract_logs\r\n"


def test_read_line_strips_the_eol():
    link = link_with(FakeSerial([b"I/MODEM [t] up\r\n"]))
    assert link.read_line() == "I/MODEM [t] up"


def test_read_line_reassembles_a_line_split_across_chunks():
    link = link_with(FakeSerial([b"I/MODEM [t] li", b"nk up\r\n"]))
    assert link.read_line() == "I/MODEM [t] link up"


def test_read_line_returns_buffered_lines_one_at_a_time():
    link = link_with(FakeSerial([b"first\r\nsecond\r\n"]))
    assert link.read_line() == "first"
    assert link.read_line() == "second"


def test_read_line_returns_none_on_timeout_without_a_complete_line():
    link = link_with(FakeSerial([b"partial, no newline yet"]))
    assert link.read_line() is None


def test_read_line_gives_up_on_a_stream_that_never_completes_a_line():
    """A newline-less stream must not block the call or grow the buffer forever.

    port.read() never returns empty here, so only the call's own deadline can
    end it. Without that, the reader thread would never regain control to check
    for interruption and Disconnect would hang.
    """

    class EndlessNoise:
        in_waiting = 5

        def read(self, _size):
            return b"noise"

        def write(self, data):
            return len(data)

        def close(self):
            pass

    link = SerialLink(read_timeout_s=0.05, serial_factory=lambda **kwargs: EndlessNoise())
    link.open("/dev/fake")
    assert link.read_line() is None


def test_read_line_keeps_the_partial_line_for_the_next_call():
    fake = FakeSerial([b"par", b""])
    link = link_with(fake)
    assert link.read_line() is None
    fake._chunks.append(b"tial\r\n")
    assert link.read_line() == "partial"


def test_undecodable_bytes_are_replaced_rather_than_raising():
    link = link_with(FakeSerial([b"I/MODEM \xff\xfe up\r\n"]))
    result = link.read_line()
    assert result is not None
    assert "I/MODEM" in result


def test_read_error_is_reported_as_serial_link_error():
    link = link_with(FakeSerial(raise_on_read=OSError("device disconnected")))
    with pytest.raises(SerialLinkError, match="device disconnected"):
        link.read_line()


def test_write_error_is_reported_as_serial_link_error():
    link = link_with(FakeSerial(raise_on_write=OSError("write failed")))
    with pytest.raises(SerialLinkError, match="write failed"):
        link.write_line("extract_logs")


def test_operations_on_a_closed_link_raise():
    link = SerialLink(serial_factory=lambda **kwargs: FakeSerial())
    with pytest.raises(SerialLinkError, match="not connected"):
        link.read_line()
    with pytest.raises(SerialLinkError, match="not connected"):
        link.write_line("x")


def test_close_closes_the_port_and_is_idempotent():
    fake = FakeSerial()
    link = link_with(fake)
    link.close()
    link.close()
    assert fake.closed is True
    assert link.is_open is False


def test_reopening_discards_the_previous_partial_buffer():
    first = FakeSerial([b"leftover partial"])
    link = SerialLink(serial_factory=lambda **kwargs: first)
    link.open("/dev/fake")
    assert link.read_line() is None  # buffers the partial text

    second = FakeSerial([b"clean\r\n"])
    link._serial_factory = lambda **kwargs: second
    link.open("/dev/fake")
    assert link.read_line() == "clean"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_serial_link.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'ohme_log_viewer.serial_link'`.

- [ ] **Step 3: Write the implementation**

Create `src/ohme_log_viewer/serial_link.py`:

```python
"""Line-buffered serial transport, written directly against pyserial.

Reads are chunk-based and a chunk boundary can fall mid-line, so incoming
bytes accumulate in a buffer and only complete lines are handed out. Callers
therefore never see half a log line.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import serial
from serial.tools import list_ports

#: Fixed device parameters (115200 8N1).
_BAUD = 115200
_BYTESIZE = 8
_PARITY = "N"
_STOPBITS = 1
_EOL = b"\r\n"

_WRITE_TIMEOUT_S = 1.0


class SerialLinkError(Exception):
    """Any failure opening, reading from or writing to the port."""


@dataclass(frozen=True)
class PortInfo:
    """A serial port offered to the user."""

    device: str
    label: str


def list_usb_ports() -> list[PortInfo]:
    """Enumerate serial ports, annotating USB devices with their VID:PID."""
    found: list[PortInfo] = []
    for port in list_ports.comports():
        label = port.description or port.device
        if port.vid is not None and port.pid is not None:
            label = f"{label} ({port.vid:04X}:{port.pid:04X})"
        found.append(PortInfo(device=port.device, label=label))
    return found


class SerialLink:
    """Open/close a port and exchange whole lines with the device."""

    def __init__(self, read_timeout_s: float = 0.3, serial_factory=serial.Serial) -> None:
        self._read_timeout_s = read_timeout_s
        self._serial_factory = serial_factory
        self._port = None
        self._buf = bytearray()

    @property
    def is_open(self) -> bool:
        return self._port is not None

    def open(self, device: str, baud: int = _BAUD) -> None:
        """Open *device*. Raises SerialLinkError if it cannot be opened."""
        self.close()
        try:
            self._port = self._serial_factory(
                port=device,
                baudrate=baud,
                bytesize=_BYTESIZE,
                parity=_PARITY,
                stopbits=_STOPBITS,
                timeout=self._read_timeout_s,
                write_timeout=_WRITE_TIMEOUT_S,
            )
        except (serial.SerialException, OSError, ValueError) as exc:
            self._port = None
            raise SerialLinkError(str(exc)) from exc
        # A partial line left over from a previous session would corrupt the
        # first line of this one.
        self._buf.clear()

    def close(self) -> None:
        """Close the port if open. Safe to call repeatedly."""
        if self._port is None:
            return
        try:
            self._port.close()
        except (serial.SerialException, OSError):
            pass  # already gone; nothing useful to do while tearing down
        self._port = None

    def write_line(self, text: str) -> None:
        """Send *text* followed by CRLF."""
        port = self._require_port()
        try:
            port.write(text.encode("utf-8") + _EOL)
        except (serial.SerialException, OSError) as exc:
            raise SerialLinkError(str(exc)) from exc

    def read_line(self) -> str | None:
        """Return the next complete line, or None if the read timed out.

        Any bytes read that do not yet form a complete line stay buffered for
        the following call.

        The deadline bounds this call as a whole, not merely each underlying
        read. A device emitting bytes with no newline among them keeps
        ``port.read()`` returning data indefinitely, so without the deadline
        this loop would never hand control back: the caller's thread could not
        check for interruption and the buffer would grow without limit.
        """
        port = self._require_port()
        deadline = time.monotonic() + self._read_timeout_s
        while True:
            newline = self._buf.find(b"\n")
            if newline >= 0:
                raw = bytes(self._buf[:newline])
                del self._buf[: newline + 1]
                return raw.decode("utf-8", errors="replace").rstrip("\r")

            if time.monotonic() > deadline:
                return None  # no complete line within the timeout

            try:
                pending = port.in_waiting or 1
                chunk = port.read(pending)
            except (serial.SerialException, OSError) as exc:
                raise SerialLinkError(str(exc)) from exc

            if not chunk:
                return None  # timed out; keep whatever is buffered
            self._buf.extend(chunk)

    def _require_port(self):
        if self._port is None:
            raise SerialLinkError("not connected")
        return self._port
```

- [ ] **Step 4: Run the full suite to verify it passes**

Run: `.venv/bin/pytest -v`
Expected: 39 passed.

---

### Task 5: The continuous reader

The streaming loop is split into a pure function and a thin `QThread` around it. `run_extract_cycle` holds all the logic — sending the command, detecting the marker, honouring the deadline — and takes no Qt types, so it is testable without a Qt application. `LogReader` only adapts it to signals.

**Files:**
- Create: `src/ohme_log_viewer/reader.py`
- Create: `tests/test_reader.py`
- Create: `tests/test_log_reader.py`

`test_reader.py` covers `run_extract_cycle`, which takes no Qt types at all. `test_log_reader.py` covers the `LogReader` thread around it. Both are pure unit tests — no port is opened by either.

**Interfaces:**
- Consumes: `SerialLink`, `SerialLinkError` from `ohme_log_viewer.serial_link`.
- Produces: constants `COMMAND = "extract_logs"`, `MARKER = "LOG_EXTRACT_DONE"`, `CYCLE_DELAY_S = 1.0`, `CYCLE_TIMEOUT_S = 120.0`; `CycleTimeout(SerialLinkError)`; `run_extract_cycle(link, should_stop, emit, timeout_s=CYCLE_TIMEOUT_S, now=time.monotonic) -> bool`; class `LogReader(QThread)` with `__init__(link: SerialLink, parent=None)`, signals `line_received = Signal(str)` and `error = Signal(str)`, and `stop() -> None`.

`run_extract_cycle` returns True when a cycle completed on the marker, and False when `should_stop()` asked it to stop. It raises `CycleTimeout` if the marker does not arrive within `timeout_s`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reader.py`:

```python
import pytest

from ohme_log_viewer.reader import COMMAND, MARKER, CycleTimeout, run_extract_cycle


class FakeLink:
    """Scripted stand-in for SerialLink. A None entry models a read timeout."""

    def __init__(self, lines=None, raise_on_read=None):
        self._lines = list(lines or [])
        self._raise_on_read = raise_on_read
        self.written: list[str] = []

    def write_line(self, text):
        self.written.append(text)

    def read_line(self):
        if self._raise_on_read is not None:
            raise self._raise_on_read
        if not self._lines:
            return None
        return self._lines.pop(0)


def collect(link, should_stop=lambda: False, **kwargs):
    emitted: list[str] = []
    completed = run_extract_cycle(link, should_stop, emitted.append, **kwargs)
    return completed, emitted


def test_cycle_sends_the_extract_command():
    link = FakeLink([MARKER])
    run_extract_cycle(link, lambda: False, lambda _line: None)
    assert link.written == [COMMAND]


def test_cycle_emits_lines_until_the_marker():
    link = FakeLink(["first", "second", MARKER, "after"])
    completed, emitted = collect(link)
    assert completed is True
    assert emitted == ["first", "second"]


def test_the_marker_itself_is_not_emitted():
    completed, emitted = collect(FakeLink([MARKER]))
    assert completed is True
    assert emitted == []


def test_marker_is_matched_after_stripping():
    completed, emitted = collect(FakeLink([f"  {MARKER}  "]))
    assert completed is True
    assert emitted == []


def test_read_timeouts_do_not_end_the_cycle():
    link = FakeLink([None, "line", None, MARKER])
    completed, emitted = collect(link)
    assert completed is True
    assert emitted == ["line"]


def test_stop_request_ends_the_cycle_without_completing():
    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2

    completed, emitted = collect(FakeLink(["a", "b", "c", MARKER]), should_stop=should_stop)
    assert completed is False
    assert emitted == ["a", "b"]


def test_missing_marker_raises_cycle_timeout():
    clock = iter([0.0, 0.0, 1.0, 500.0])
    link = FakeLink([None, None, None, None])
    with pytest.raises(CycleTimeout):
        run_extract_cycle(
            link, lambda: False, lambda _line: None, timeout_s=120.0, now=lambda: next(clock)
        )


def test_read_failure_propagates():
    link = FakeLink(raise_on_read=OSError("boom"))
    with pytest.raises(OSError, match="boom"):
        run_extract_cycle(link, lambda: False, lambda _line: None)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_reader.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'ohme_log_viewer.reader'`.

- [ ] **Step 3: Write the implementation**

Create `src/ohme_log_viewer/reader.py`:

```python
"""The continuous log extraction loop.

One long-lived thread runs for the whole connected session:

    while not stopping:
        send "extract_logs"
        read lines until LOG_EXTRACT_DONE
        wait 1 s

Streaming is always continuous, so a single thread from Connect to Disconnect
is enough. There is no per-cycle thread to create and destroy, and therefore
none of the lifecycle hazard that comes with tearing a QThread down every
second.

Marker detection runs on the raw line before any noise filtering, so hiding
the sentinel from the display cannot desynchronise the TX/RX cycle.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from .serial_link import SerialLink, SerialLinkError

#: Command that makes the device stream its logs.
COMMAND = "extract_logs"

#: Sentinel line the device sends at the end of each extraction.
MARKER = "LOG_EXTRACT_DONE"

#: Pause between cycles.
CYCLE_DELAY_S = 1.0

#: How long a single cycle may run without seeing the marker.
CYCLE_TIMEOUT_S = 120.0

#: Granularity of interruptible sleeping.
_SLEEP_SLICE_S = 0.05


class CycleTimeout(SerialLinkError):
    """The device did not send the completion marker in time."""


def run_extract_cycle(
    link: SerialLink,
    should_stop: Callable[[], bool],
    emit: Callable[[str], None],
    timeout_s: float = CYCLE_TIMEOUT_S,
    now: Callable[[], float] = time.monotonic,
) -> bool:
    """Run one extract_logs cycle.

    Sends the command, then passes every received line to *emit* until the
    completion marker arrives. Returns True on a completed cycle and False if
    *should_stop* asked to stop partway. Raises :class:`CycleTimeout` if the
    marker does not arrive within *timeout_s*.
    """
    link.write_line(COMMAND)
    deadline = now() + timeout_s

    while True:
        if should_stop():
            return False
        if now() > deadline:
            raise CycleTimeout(f"No '{MARKER}' within {timeout_s:.0f}s")

        line = link.read_line()
        if line is None:
            continue  # read timeout; keep waiting until the deadline
        if line.strip() == MARKER:
            return True
        emit(line)


class LogReader(QThread):
    """Runs :func:`run_extract_cycle` back to back until interrupted."""

    line_received: Signal = Signal(str)
    error: Signal = Signal(str)

    def __init__(self, link: SerialLink, parent=None) -> None:
        super().__init__(parent)
        self._link = link

    def stop(self) -> None:
        """Ask the loop to finish. Detected within one read timeout."""
        self.requestInterruption()

    def run(self) -> None:  # QThread entry point; Qt calls this, we never do
        try:
            while not self.isInterruptionRequested():
                completed = run_extract_cycle(
                    self._link,
                    self.isInterruptionRequested,
                    self.line_received.emit,
                )
                if not completed:
                    return
                if not self._sleep_interruptibly(CYCLE_DELAY_S):
                    return
        except (SerialLinkError, OSError) as exc:
            # One clause, not two identical ones. SerialLink converts every
            # serial/OS failure to SerialLinkError before it reaches here, so
            # OSError is only reachable via a non-conforming link — worth
            # catching, not worth a duplicated body.
            self.error.emit(str(exc))

    def _sleep_interruptibly(self, seconds: float) -> bool:
        """Sleep in short slices. Returns False if interrupted partway."""
        remaining = seconds
        while remaining > 0:
            if self.isInterruptionRequested():
                return False
            slice_s = min(_SLEEP_SLICE_S, remaining)
            self.msleep(int(slice_s * 1000))
            remaining -= slice_s
        return True
```

- [ ] **Step 4: Cover the QThread glue**

`run_extract_cycle` is now tested, but the thread that drives it is not: whether it actually starts, whether the lines it reads reach a slot across the thread boundary, whether `stop()` ends it, and whether a link failure surfaces on the `error` signal. None of that needs hardware — a fake link and an offscreen `QApplication` exercise the real thread and the real connections. Leaving it to the on-device smoke test would put the branch's most delicate layer behind the one check that requires destroying a device's logs to run.

Signals must be received by a `QObject` built on the main thread, as `MainWindow` does, so Qt delivers them queued rather than calling straight through — a plain function would be invoked directly on the reader's thread and would prove nothing about the real arrangement.

Create `tests/test_log_reader.py`:

```python
"""Headless tests for the LogReader QThread glue.

The plan assumed this layer could only be checked on hardware. It cannot be
checked on hardware *safely* — ``extract_logs`` destroys the device's logs — but
it does not need hardware at all: a fake link and an offscreen QApplication
exercise the real thread, the real signal connections and the real stop path.

Signals are received by a QObject created on the main thread, which is what
MainWindow does, so Qt delivers them queued across the thread boundary exactly
as it does in the running application. That is the part worth testing; the loop
logic itself is covered by ``test_reader.py`` without any Qt at all.
"""

import os
import time
from contextlib import contextmanager

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from ohme_log_viewer.reader import COMMAND, MARKER, LogReader
from ohme_log_viewer.serial_link import SerialLinkError

#: Stand-in for the link's real 0.3 s read timeout. Short enough to keep the
#: tests quick, long enough that an idle reader does not spin on the CPU.
_FAKE_READ_TIMEOUT_S = 0.005

#: Ceiling for every wait in this module. Nothing here should come close to it;
#: it exists so a broken reader fails the test instead of hanging the suite.
_WAIT_TIMEOUT_S = 5.0


class FakeLink:
    """Scripted stand-in for SerialLink, read from the reader's thread.

    Each scripted entry is handed out once; once they run out ``read_line``
    reports a read timeout, which is what a quiet device looks like.
    """

    def __init__(self, lines=None, raise_on_read=None):
        self._lines = list(lines or [])
        self._raise_on_read = raise_on_read
        self.written: list[str] = []

    def write_line(self, text):
        self.written.append(text)

    def feed(self, line):
        """Queue another line mid-test. List ops are atomic, so no lock."""
        self._lines.append(line)

    def drained(self) -> bool:
        """True once the reader has consumed every queued line."""
        return not self._lines

    def read_line(self):
        if self._raise_on_read is not None:
            raise self._raise_on_read
        if self._lines:
            return self._lines.pop(0)
        time.sleep(_FAKE_READ_TIMEOUT_S)
        return None


class Collector(QObject):
    """Slots living on the main thread, so deliveries are genuinely queued."""

    def __init__(self):
        super().__init__()
        self.lines: list[str] = []
        self.errors: list[str] = []
        self.finished = False

    def on_line(self, text: str) -> None:
        self.lines.append(text)

    def on_error(self, message: str) -> None:
        self.errors.append(message)

    def on_finished(self) -> None:
        self.finished = True


@pytest.fixture(scope="session")
def qapp():
    """The one QApplication. Reused if the process already made one."""
    return QApplication.instance() or QApplication([])


@contextmanager
def running(reader: LogReader):
    """Start *reader* and guarantee its thread is joined before the test ends.

    Leaving a QThread running past the end of a test aborts the interpreter, so
    the join is unconditional rather than an assertion the test body could skip.
    """
    reader.start()
    try:
        yield reader
    finally:
        reader.stop()
        reader.wait(int(_WAIT_TIMEOUT_S * 1000))


def pump_until(app, predicate, timeout_s: float = _WAIT_TIMEOUT_S) -> bool:
    """Run the event loop until *predicate* holds or the deadline passes."""
    deadline = time.monotonic() + timeout_s
    while True:
        app.processEvents()
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.002)


def test_started_thread_sends_the_command_and_emits_received_lines(qapp):
    link = FakeLink(["first", "second", MARKER])
    reader = LogReader(link)
    collector = Collector()
    reader.line_received.connect(collector.on_line)

    with running(reader):
        assert pump_until(qapp, lambda: len(collector.lines) >= 2), collector.lines

    assert collector.lines[:2] == ["first", "second"]
    assert MARKER not in collector.lines
    assert link.written[0] == COMMAND


def test_stop_ends_the_thread_promptly(qapp):
    # No marker is ever sent, so the reader stays in its read loop and only
    # leaves it because it was asked to.
    reader = LogReader(FakeLink())
    reader.start()
    try:
        assert pump_until(qapp, reader.isRunning), "thread never started"
        start = time.monotonic()
        reader.stop()
        stopped = reader.wait(int(_WAIT_TIMEOUT_S * 1000))
        elapsed = time.monotonic() - start
    finally:
        reader.stop()
        reader.wait(int(_WAIT_TIMEOUT_S * 1000))

    assert stopped, "thread did not exit after stop()"
    assert not reader.isRunning()
    # Generous next to the 5 s ceiling, tight next to a reader that only
    # notices interruption between whole extract cycles.
    assert elapsed < 1.0, elapsed


def test_link_error_reaches_the_error_signal(qapp):
    reader = LogReader(FakeLink(raise_on_read=SerialLinkError("port went away")))
    collector = Collector()
    reader.error.connect(collector.on_error)
    reader.finished.connect(collector.on_finished)

    with running(reader):
        assert pump_until(qapp, lambda: collector.errors != []), "no error emitted"
        assert pump_until(qapp, lambda: collector.finished), "thread did not finish"

    assert collector.errors == ["port went away"]
    assert not reader.isRunning()


def test_no_lines_are_emitted_after_the_signal_is_disconnected(qapp):
    # The path MainWindow relies on when it parks a reader it could not stop:
    # a disconnected reader must be inert even though it is still running. No
    # marker is scripted, so the reader stays inside one cycle and picks up a
    # line fed to it within a read timeout rather than after a cycle delay.
    link = FakeLink(["before"])
    reader = LogReader(link)
    collector = Collector()
    reader.line_received.connect(collector.on_line)

    with running(reader):
        assert pump_until(qapp, lambda: collector.lines == ["before"]), collector.lines
        reader.line_received.disconnect(collector.on_line)

        link.feed("after")
        # The reader really did read it — it just had nowhere to deliver it.
        assert pump_until(qapp, link.drained), "reader never consumed the fed line"
        assert not pump_until(qapp, lambda: "after" in collector.lines, timeout_s=0.2)

    assert collector.lines == ["before"]
```

- [ ] **Step 5: Run the full suite to verify it passes**

Run: `.venv/bin/pytest -v`
Expected: 51 passed.

---

### Task 6: The log pane

**Files:**
- Create: `src/ohme_log_viewer/ui/__init__.py`
- Create: `src/ohme_log_viewer/ui/log_pane.py`

**Interfaces:**
- Consumes: `LogLine` from `ohme_log_viewer.log_line`.
- Produces: class `LogPane(QWidget)` with `__init__(max_lines: int, parent: QWidget | None = None)`, `append_lines(lines: Sequence[LogLine]) -> None`, `render(lines: Iterable[LogLine]) -> int` (clears then appends, returning the number rendered), `append_error(message: str) -> None`, `clear() -> None`, `line_count() -> int`.

`max_lines` is required, not defaulted: the pane's block cap must be the same number as the buffer's ring cap, and the caller passing it explicitly is what keeps the two from drifting apart.

No unit tests: the spec puts Qt widgets under manual smoke test. Verification is the scripted headless check in Step 3.

- [ ] **Step 1: Create the ui package**

Create an empty `src/ohme_log_viewer/ui/__init__.py`.

- [ ] **Step 2: Write the implementation**

Create `src/ohme_log_viewer/ui/log_pane.py`:

```python
"""Read-only, colour-coded view of log lines.

The widget is a projection of LogBuffer, never a store in its own right, so its
block cap is set from the same number as the buffer's ring cap. Leaving it
uncapped would let the view keep every line ever received while the buffer
evicted at 50,000, so the two would silently disagree about what exists and the
view's memory would grow without bound on a long session.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QVBoxLayout, QWidget

from ..log_line import LogLine

_FONT_FAMILIES = ("Menlo", "Consolas", "DejaVu Sans Mono", "monospace")
_FONT_SIZE_PT = 10

_BACKGROUND = "#1e1e1e"
_BORDER = "#3c3c3c"

#: Foreground per level letter. Assert and error share red; debug and verbose
#: are dimmed so they recede when a device is running at a chatty level.
_LEVEL_COLOURS = {
    "A": "#f14c4c",
    "E": "#f14c4c",
    "W": "#ffcc66",
    "I": "#d4d4d4",
    "D": "#9a9a9a",
    "V": "#7a7a7a",
}
_UNPARSED_COLOUR = "#9cdcfe"
_ERROR_COLOUR = "#f14c4c"

#: Scroll position tolerance, in LINES, for "the user is at the bottom".
#:
#: The unit matters. Unlike QTextEdit, QPlainTextEdit's vertical scrollbar is
#: measured in blocks — one step is one line, not one pixel. Any non-zero slack
#: is therefore a dead zone several lines deep, and a default mouse-wheel notch
#: is only three lines: the commonest way of scrolling up to read something
#: would land inside it and the next append would yank the view back down.
#: Zero means "exactly at the end", which is what following the tail means.
_AT_BOTTOM_SLACK = 0


class LogPane(QWidget):
    """Scrollable coloured log output."""

    def __init__(self, max_lines: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_lines = max_lines
        self._build_ui()

    # ── public API ───────────────────────────────────────────────────────

    def line_count(self) -> int:
        """Lines currently displayed.

        Read from the document rather than tracked in a counter, so it stays
        exact across block-cap eviction and full re-renders alike. Each
        appended line ends in a newline, which leaves one trailing empty
        block — hence the -1.
        """
        return self._view.blockCount() - 1

    def append_lines(self, lines: Sequence[LogLine]) -> None:
        """Append a batch of lines in one repaint."""
        if not lines:
            return
        follow = self._is_at_bottom()
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        for line in lines:
            self._insert(cursor, self._text_for(line), self._colour_for(line))
        if follow:
            self._scroll_to_bottom()

    def render(self, lines: Iterable[LogLine]) -> int:
        """Replace the contents with *lines*. Returns how many were rendered."""
        materialised = list(lines)
        self._view.clear()
        self.append_lines(materialised)
        self._scroll_to_bottom()
        return len(materialised)

    def append_error(self, message: str) -> None:
        """Append an internal error notice, distinct from device output."""
        follow = self._is_at_bottom()
        cursor = self._view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._insert(cursor, f"[error] {message}", _ERROR_COLOUR)
        if follow:
            self._scroll_to_bottom()

    def clear(self) -> None:
        self._view.clear()

    # ── internals ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._view = QPlainTextEdit()
        self._view.setReadOnly(True)
        self._view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._view.setUndoRedoEnabled(False)
        # +1 for the trailing empty block, so the pane holds exactly as many
        # lines as the buffer does.
        self._view.setMaximumBlockCount(self._max_lines + 1)

        font = QFont()
        font.setFamilies(list(_FONT_FAMILIES))
        font.setPointSize(_FONT_SIZE_PT)
        self._view.setFont(font)

        self._view.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {_BACKGROUND};"
            f" border: 1px solid {_BORDER}; color: {_LEVEL_COLOURS['I']}; }}"
        )
        layout.addWidget(self._view)

    @staticmethod
    def _text_for(line: LogLine) -> str:
        return line.raw

    @staticmethod
    def _colour_for(line: LogLine) -> str:
        if line.level is None:
            return _UNPARSED_COLOUR
        return _LEVEL_COLOURS.get(line.level, _LEVEL_COLOURS["I"])

    @staticmethod
    def _insert(cursor: QTextCursor, text: str, colour: str) -> None:
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(colour))
        cursor.setCharFormat(fmt)
        cursor.insertText(text + "\n")

    def _is_at_bottom(self) -> bool:
        """True if the view is scrolled to the end.

        Appending must not yank the view away from a user who has scrolled up
        to read something.
        """
        bar = self._view.verticalScrollBar()
        return bar.value() >= bar.maximum() - _AT_BOTTOM_SLACK

    def _scroll_to_bottom(self) -> None:
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())
```

- [ ] **Step 3: Verify it renders headlessly**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -c "
from PySide6.QtWidgets import QApplication
from ohme_log_viewer.log_line import parse
from ohme_log_viewer.ui.log_pane import LogPane

app = QApplication([])
pane = LogPane(max_lines=4)
lines = [
    parse('I/MODEM [2026-07-01T10:11:11.950Z] link up'),
    parse('E/OCPP  [2026-07-01T10:11:12.000Z] connect failed'),
    parse('MainMenu> '),
]
pane.append_lines(lines)
assert pane.line_count() == 3, pane.line_count()
assert pane.render(lines) == 3
assert pane.line_count() == 3, pane.line_count()

pane.append_error('device disconnected')
text = pane._view.toPlainText()
assert 'link up' in text and '[error] device disconnected' in text
assert pane.line_count() == 4, pane.line_count()

# Cap is 4, so a fifth line evicts the oldest rather than growing the view.
pane.append_lines([parse('I/RTC   [2026-07-01T10:11:13.000Z] synced')])
assert pane.line_count() == 4, pane.line_count()
assert 'link up' not in pane._view.toPlainText()

pane.clear()
assert pane._view.toPlainText() == ''
assert pane.line_count() == 0, pane.line_count()
print('log pane OK')
"
```

Expected: prints `log pane OK`.

- [ ] **Step 4: Run the full suite to confirm nothing regressed**

Run: `.venv/bin/pytest -v`
Expected: 51 passed.

---

### Task 7: The tag panel

**Files:**
- Create: `src/ohme_log_viewer/ui/tag_panel.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: class `TagPanel(QWidget)` with signal `tag_toggled = Signal(str, bool)`, and methods `sync(tags: Sequence[str], counts: dict[str, int], enabled: dict[str, bool]) -> None` and `set_all(on: bool) -> None`.

`sync` is idempotent — calling it repeatedly never duplicates a row — and it **mirrors** the caller's `enabled` map onto the checkboxes every time. `LogBuffer` owns filter state, exactly as it owns the lines; the panel is a projection of it, just as `LogPane` is. Mirroring is what makes that projection self-healing: any divergence is corrected on the next flush.

This is safe against the obvious worry — a user's click being reverted by the next flush — because `MainWindow._on_tag_toggled` writes the new state to the buffer as its first statement, and Qt delivers signals synchronously in-thread, so the handler always completes before the flush timer can run. Task 7's verification asserts the mirror behaviour and Task 8's asserts a toggle survives a flush.

The alternative — letting the panel own its check state after creation — would make it a second source of truth for filter state and leave it unable to resync if the buffer's state ever changed by another route.

No unit tests, per the spec. Verification is the headless check in Step 2.

- [ ] **Step 1: Write the implementation**

Create `src/ohme_log_viewer/ui/tag_panel.py`:

```python
"""Auto-discovered, checkable list of log tags.

Tags appear as soon as a line carrying them arrives, checked by default. A
device tag can be arbitrarily long, so the list has a fixed width and elides
long names with the full text in a tooltip — sizing the column to the longest
tag would squeeze the log pane, which is the part worth the space.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

_PANEL_WIDTH_PX = 200

#: Item data role holding the full, unelided tag.
_TAG_ROLE = Qt.ItemDataRole.UserRole


class TagPanel(QWidget):
    """Checkable tag list driving the display filter."""

    tag_toggled: Signal = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: dict[str, QListWidgetItem] = {}
        self._suppress_signals = False
        self._build_ui()

    # ── public API ───────────────────────────────────────────────────────

    def sync(
        self,
        tags: Sequence[str],
        counts: dict[str, int],
        enabled: dict[str, bool],
    ) -> None:
        """Add any new tags, refresh every count, and mirror *enabled*.

        Safe to call repeatedly — it updates rows in place and never
        duplicates one. Check state is mirrored from *enabled* on every call,
        not just when a row is created: the buffer owns filter state and this
        panel is a projection of it, so mirroring keeps the two from drifting.
        The caller must therefore pass its authoritative map.

        Emits nothing. Writing check states fires Qt's ``itemChanged``, which
        is suppressed here — otherwise every flush would re-apply the filter.
        """
        self._suppress_signals = True
        try:
            for tag in tags:
                item = self._items.get(tag)
                if item is None:
                    item = QListWidgetItem()
                    item.setData(_TAG_ROLE, tag)
                    item.setToolTip(tag)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    self._list.addItem(item)
                    self._items[tag] = item
                item.setCheckState(
                    Qt.CheckState.Checked if enabled.get(tag, True) else Qt.CheckState.Unchecked
                )
                item.setText(f"{tag}  ({counts.get(tag, 0)})")
        finally:
            self._suppress_signals = False

    def set_all(self, on: bool) -> None:
        """Check or uncheck every tag, emitting one signal per actual change."""
        state = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
        for item in self._items.values():
            if item.checkState() != state:
                item.setCheckState(state)  # emits itemChanged -> tag_toggled

    # ── internals ────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("Tags"))

        self._list = QListWidget()
        self._list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self._list.setFixedWidth(_PANEL_WIDTH_PX)
        self._list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list, stretch=1)

        buttons = QHBoxLayout()
        all_btn = QPushButton("All")
        all_btn.clicked.connect(lambda: self.set_all(True))
        buttons.addWidget(all_btn)

        none_btn = QPushButton("None")
        none_btn.clicked.connect(lambda: self.set_all(False))
        buttons.addWidget(none_btn)
        layout.addLayout(buttons)

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._suppress_signals:
            return
        tag = item.data(_TAG_ROLE)
        self.tag_toggled.emit(tag, item.checkState() == Qt.CheckState.Checked)
```

- [ ] **Step 2: Verify it works headlessly**

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -c "
from PySide6.QtWidgets import QApplication
from ohme_log_viewer.ui.tag_panel import TagPanel

app = QApplication([])
panel = TagPanel()
seen = []
panel.tag_toggled.connect(lambda tag, on: seen.append((tag, on)))

long_tag = 'CHARGING_SESSION_MGR'
panel.sync(['MODEM', long_tag], {'MODEM': 3, long_tag: 1}, {'MODEM': True, long_tag: True})
assert panel._list.count() == 2
assert seen == [], 'sync must not emit'

panel.sync(['MODEM', long_tag], {'MODEM': 9, long_tag: 1}, {'MODEM': True, long_tag: True})
assert panel._list.count() == 2, 'sync must be idempotent'
assert '(9)' in panel._list.item(0).text()
assert panel._list.item(1).toolTip() == long_tag

panel.set_all(False)
assert seen == [('MODEM', False), (long_tag, False)], seen

# sync MIRRORS the caller's enabled map: the buffer owns filter state and this
# panel projects it. A sync carrying enabled=True must therefore re-check a row
# the user had unchecked — and must still emit nothing while doing it.
from PySide6.QtCore import Qt
seen.clear()
panel.sync(['MODEM', long_tag], {'MODEM': 9, long_tag: 1}, {'MODEM': True, long_tag: True})
assert panel._list.item(0).checkState() == Qt.CheckState.Checked
assert seen == [], 'mirroring must not emit'

# ...and a sync carrying False leaves it unchecked, still silently.
panel.sync(['MODEM', long_tag], {'MODEM': 9, long_tag: 1}, {'MODEM': False, long_tag: True})
assert panel._list.item(0).checkState() == Qt.CheckState.Unchecked
assert seen == [], 'mirroring must not emit'
print('tag panel OK')
"
```

Expected: prints `tag panel OK`.

- [ ] **Step 3: Run the full suite to confirm nothing regressed**

Run: `.venv/bin/pytest -v`
Expected: 51 passed.

---

### Task 8: The main window and entry point

Wires everything together. `MainWindow` owns the one pipeline that sequences noise-filter → parse → buffer → display, so neither `LogBuffer` nor `LogReader` needs to know about the other.

**Files:**
- Create: `src/ohme_log_viewer/ui/main_window.py`
- Create: `src/ohme_log_viewer/__main__.py`

**Interfaces:**
- Consumes: `is_noise`, `parse` from `log_line`; `LogBuffer` from `log_buffer`; `SerialLink`, `SerialLinkError`, `list_usb_ports` from `serial_link`; `LogReader` from `reader`; `LogPane` from `ui.log_pane`; `TagPanel` from `ui.tag_panel`.
- Produces: class `MainWindow(QMainWindow)`; `main() -> int` in `__main__.py`.

- [ ] **Step 1: Write the main window**

Create `src/ohme_log_viewer/ui/main_window.py`:

```python
"""Application window: port selection, tag filter, log pane, status bar.

This class owns the single receive pipeline. Its ``_on_line_received`` slot is
the only place that sequences noise filtering, parsing and buffering — the
buffer never parses and the reader never interprets.

Streaming is always continuous, so there is no continuous-mode control: the
reader loops from Connect to Disconnect and nothing can fall out of sync with
it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStatusBar,
    QWidget,
)

from ..log_buffer import MAX_LINES, LogBuffer
from ..log_line import LogLine, is_noise, parse
from ..reader import LogReader
from ..serial_link import SerialLink, SerialLinkError, list_usb_ports
from .log_pane import LogPane
from .tag_panel import TagPanel

#: Display batching interval. A burst of lines costs one repaint, not one each.
_FLUSH_INTERVAL_MS = 100

#: How long to wait for the reader thread to exit before giving up on it.
#:
#: This blocks the GUI thread, so it is sized to the reader's own worst case
#: rather than picked for comfort. Every operation the loop performs is
#: bounded: a read is capped at the link's 0.3 s timeout, and the inter-cycle
#: wait is 50 ms slices that check for interruption between each. A reader
#: that is going to stop at all has therefore stopped well inside 1.5 s;
#: anything slower is pathological and waiting longer will not rescue it.
_READER_STOP_TIMEOUT_MS = 1500


class MainWindow(QMainWindow):
    """Top-level window."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Ohme Log Viewer")
        self.resize(1100, 700)

        # One link per connection, created in _connect. Never a long-lived
        # instance shared between sessions: a reader that outlives its session
        # would otherwise be holding the very object the next session opens.
        self._link: SerialLink | None = None
        self._buffer = LogBuffer()
        self._reader: LogReader | None = None
        self._pending: list[LogLine] = []
        self._rerender_pending = False
        self._received = 0
        self._last_error: str | None = None
        # Readers that would not stop. Held so neither Qt nor Python frees a
        # thread object while its OS thread is still running.
        self._orphaned_readers: list[LogReader] = []

        self._build_ui()
        self._refresh_ports()
        self._update_status()

        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(_FLUSH_INTERVAL_MS)
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start()

    # ── UI ───────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)

        self._tag_panel = TagPanel()
        self._tag_panel.tag_toggled.connect(self._on_tag_toggled)

        # Same cap for both, so the view and the buffer never disagree about
        # which lines still exist.
        self._pane = LogPane(max_lines=MAX_LINES)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._tag_panel)
        splitter.addWidget(self._pane)
        splitter.setStretchFactor(1, 1)
        outer.addWidget(splitter)

        self.setCentralWidget(central)
        self._build_toolbar()

        self.setStatusBar(QStatusBar())

    def _build_toolbar(self) -> None:
        bar = self.addToolBar("Main")
        bar.setMovable(False)

        bar.addWidget(QLabel(" Port: "))
        self._port_combo = QComboBox()
        self._port_combo.setMinimumWidth(320)
        bar.addWidget(self._port_combo)

        self._refresh_btn = QPushButton("Refresh")
        self._refresh_btn.clicked.connect(self._refresh_ports)
        bar.addWidget(self._refresh_btn)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._on_connect_clicked)
        bar.addWidget(self._connect_btn)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        bar.addWidget(self._clear_btn)

    def _refresh_ports(self) -> None:
        previous = self._port_combo.currentData()
        self._port_combo.clear()
        for port in list_usb_ports():
            self._port_combo.addItem(port.label, port.device)
        if previous is not None:
            index = self._port_combo.findData(previous)
            if index >= 0:
                self._port_combo.setCurrentIndex(index)

    # ── connection ───────────────────────────────────────────────────────

    @property
    def _is_streaming(self) -> bool:
        return self._reader is not None

    def _on_connect_clicked(self) -> None:
        if self._is_streaming:
            self._disconnect()
        else:
            self._connect()

    def _connect(self) -> None:
        device = self._port_combo.currentData()
        if device is None:
            # Via _report_error, not showMessage: the flush timer rewrites the
            # status text every 100 ms and would erase it before it was read.
            self._report_error("No serial port selected")
            return

        # A fresh link per connection. If a previous reader had to be parked it
        # still holds the *previous* link, which _disconnect closed, so it can
        # neither read this session's port nor write its own extract_logs to
        # it — and it cannot race this session on a shared receive buffer.
        link = SerialLink()
        try:
            link.open(device)
        except SerialLinkError as exc:
            self._report_error(f"Could not open {device}: {exc}")
            return

        self._link = link
        self._last_error = None

        self._reader = LogReader(link)
        self._reader.line_received.connect(self._on_line_received)
        self._reader.error.connect(self._on_reader_error)
        self._reader.finished.connect(self._on_reader_finished)
        self._reader.start()

        self._connect_btn.setText("Disconnect")
        self._port_combo.setEnabled(False)
        self._refresh_btn.setEnabled(False)
        self._update_status()

    def _disconnect(self) -> None:
        reader, self._reader = self._reader, None
        if reader is not None:
            reader.stop()
            if reader.wait(_READER_STOP_TIMEOUT_MS):
                reader.deleteLater()
            else:
                # Destroying a QThread Qt still considers running is fatal, and
                # letting Python collect the wrapper is no better. Park it and
                # carry on: leaking one stuck thread beats killing the app. The
                # reader's own reads and sleeps are bounded, so reaching this
                # means something pathological — say so rather than hiding it.
                #
                # Cut its signals first. A parked reader is still running and
                # will emit whenever it unblocks; left connected it would push
                # lines into the buffer, or trigger a second _disconnect, long
                # after the UI said "Disconnected". Parked means inert.
                reader.line_received.disconnect(self._on_line_received)
                reader.error.disconnect(self._on_reader_error)
                reader.finished.disconnect(self._on_reader_finished)
                self._orphaned_readers.append(reader)
                self._report_error(
                    f"Reader thread did not stop within "
                    f"{_READER_STOP_TIMEOUT_MS / 1000:g}s; leaving it parked"
                )

        # Closed even for a parked reader: the link drops its port reference,
        # so the orphan's next call raises instead of touching the device.
        link, self._link = self._link, None
        if link is not None:
            link.close()

        self._connect_btn.setText("Connect")
        self._port_combo.setEnabled(True)
        self._refresh_btn.setEnabled(True)
        self._update_status()

    def _on_reader_error(self, message: str) -> None:
        # The buffer survives, so what was captured before the failure stays
        # readable and filterable.
        self._report_error(message)
        self._disconnect()

    def _report_error(self, message: str) -> None:
        """Show *message* in the pane and keep it in the status bar.

        Held in ``_last_error`` rather than pushed straight to the status bar,
        because ``_update_status`` runs on every 100 ms flush and would
        otherwise overwrite it before it could be read.
        """
        self._last_error = message
        self._pane.append_error(message)
        self._update_status()

    def _on_reader_finished(self) -> None:
        if self._is_streaming:
            self._disconnect()

    # ── receive pipeline ─────────────────────────────────────────────────

    def _on_line_received(self, raw: str) -> None:
        if is_noise(raw):
            return
        line = parse(raw)
        self._received += 1
        if self._buffer.append(line):
            self._pending.append(line)

    def _flush(self) -> None:
        if self._rerender_pending:
            # A filter change invalidates the whole view, so the incremental
            # batch is dropped rather than appended on top of the re-render.
            self._pending.clear()
            self._pane.render(self._buffer.visible())
            self._rerender_pending = False
        elif self._pending:
            self._pane.append_lines(self._pending)
            self._pending.clear()
        self._sync_tags()
        self._update_status()

    def _sync_tags(self) -> None:
        tags = self._buffer.tags()
        self._tag_panel.sync(
            tags,
            {tag: self._buffer.count_for(tag) for tag in tags},
            {tag: self._buffer.is_tag_enabled(tag) for tag in tags},
        )

    def _on_tag_toggled(self, tag: str, on: bool) -> None:
        """Record a filter change and let the next flush repaint once.

        Re-enabling a tag must bring its earlier lines back, which costs a
        full re-render of the buffer. Doing that inline made All/None — which
        emit one signal per tag — cost tags × lines: measured at 3.9 s frozen
        for None and 4.3 s for All over a full 50,000-line buffer with 30
        tags. Deferring to the 100 ms flush collapses any number of toggles
        into a single render. A lone toggle repaints up to 100 ms later, which
        is exactly how arriving lines already behave.
        """
        self._buffer.set_tag_enabled(tag, on)
        self._rerender_pending = True

    def _on_clear_clicked(self) -> None:
        self._buffer.clear()
        self._pending.clear()
        self._rerender_pending = False
        self._pane.clear()
        self._received = 0
        # Clear is the user saying "start from what I can see now", and the
        # pane's [error] notice goes with it, so the status bar must not keep
        # reporting an error whose line has just been wiped.
        self._last_error = None
        # Tags and their check states deliberately survive a clear.
        self._sync_tags()
        self._update_status()

    def _update_status(self) -> None:
        if self._is_streaming:
            state = f"Streaming from {self._port_combo.currentData()}"
        else:
            state = "Disconnected"
        # Read from the pane rather than counted here, so it stays exact across
        # both block-cap eviction and full re-renders. That makes it a count of
        # everything on display, [error] notices included — hence "in view"
        # rather than "shown", which would read as a count of log lines and be
        # wrong by one every time an error is reported.
        text = f"{state}   |   received: {self._received}   in view: {self._pane.line_count()}"
        if self._last_error is not None:
            text = f"{text}   |   last error: {self._last_error}"
        self.statusBar().showMessage(text)

    # ── lifecycle ────────────────────────────────────────────────────────

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Stop the reader thread before the process exits."""
        self._flush_timer.stop()
        self._disconnect()
        event.accept()
```

- [ ] **Step 2: Write the entry point**

Create `src/ohme_log_viewer/__main__.py`:

```python
"""Entry point for the Ohme Log Viewer."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow


def main() -> int:
    """Run the application. Returns the Qt exit code."""
    app = QApplication(sys.argv)
    app.setApplicationName("Ohme Log Viewer")

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 3: Verify the window builds and the pipeline works headlessly**

This exercises the real pipeline without a device by feeding lines straight into the slot the reader would have driven.

Run:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -c "
from PySide6.QtWidgets import QApplication
from ohme_log_viewer.ui.main_window import MainWindow

app = QApplication([])
window = MainWindow()

for raw in [
    'I/MODEM [2026-07-01T10:11:11.950Z] link up',
    'E/OCPP  [2026-07-01T10:11:12.000Z] connect failed',
    'LOG_EXTRACT_DONE',
    '',
    'MainMenu> ',
]:
    window._on_line_received(raw)

window._flush()
assert window._received == 3, window._received
assert window._pane.line_count() == 3, window._pane.line_count()
assert window._buffer.tags() == ['MODEM', 'OCPP', '(unparsed)'], window._buffer.tags()

# A toggle re-renders on the next flush, not inline: that is what stops All /
# None — one signal per tag — costing tags x lines. So flush, then assert.
window._on_tag_toggled('MODEM', False)
window._flush()
assert window._pane.line_count() == 2, window._pane.line_count()

# A user's toggle must survive the next flush. TagPanel.sync mirrors the
# buffer's filter state onto the checkboxes, so this only holds because
# _on_tag_toggled writes to the buffer before any flush can read it.
window._flush()
assert window._buffer.is_tag_enabled('MODEM') is False
assert window._pane.line_count() == 2, 'flush must not revert the toggle'

window._on_tag_toggled('MODEM', True)
window._flush()
assert window._pane.line_count() == 3, window._pane.line_count()
window._flush()
assert window._pane.line_count() == 3, 'flush must not revert the toggle'

# Many toggles must collapse into a single render. Without this, None over a
# full buffer with 30 tags froze the GUI for about four seconds.
renders = []
real_render = window._pane.render
window._pane.render = lambda lines: (renders.append(1), real_render(lines))[1]
window._tag_panel.set_all(False)
assert renders == [], 'a toggle must not render before the flush'
window._flush()
assert len(renders) == 1, f'{len(renders)} renders for 3 toggles'
assert window._pane.line_count() == 0, window._pane.line_count()
window._tag_panel.set_all(True)
window._flush()
assert len(renders) == 2, f'{len(renders)} renders for 6 toggles'
window._pane.render = real_render
assert window._pane.line_count() == 3, window._pane.line_count()

# An error must survive the next status refresh rather than being overwritten.
window._report_error('device disconnected')
window._flush()
assert 'last error: device disconnected' in window.statusBar().currentMessage()

# Connecting with no port selected must report through the same durable path,
# not a showMessage that the next flush erases. The combo is emptied first so
# this holds on a machine that does enumerate serial ports — otherwise the
# check would try to open a real device instead of taking the no-port branch.
window._port_combo.clear()
window._on_connect_clicked()
window._flush()
assert window._reader is None, 'no-port path must not start a reader'
assert window._link is None, 'no-port path must not leave a link behind'
assert 'No serial port selected' in window.statusBar().currentMessage()

window._on_clear_clicked()
assert window._received == 0
assert window._pane.line_count() == 0, window._pane.line_count()
assert window._buffer.tags() == ['MODEM', 'OCPP', '(unparsed)'], 'tags must survive clear'
# Clear wipes the [error] notice out of the pane, so the status bar must stop
# reporting it too. The count is labelled 'in view' rather than 'shown'
# because it counts every line on display, [error] notices included.
assert 'last error' not in window.statusBar().currentMessage()
assert 'received: 0   in view: 0' in window.statusBar().currentMessage()
window.close()
print('main window OK')
"
```

Expected: prints `main window OK`. Note the noise lines (`LOG_EXTRACT_DONE` and the blank) are counted nowhere, and `MainMenu>` lands under `(unparsed)`.

A tag toggle re-renders on the next flush rather than inline, so every assertion about the pane after a toggle takes a `_flush()` first. That deferral is what stops All/None — one signal per tag — costing tags × lines; the render-counting block pins it, and the intent of the surrounding assertions is unchanged.

- [ ] **Step 4: Verify the app launches**

Run: `.venv/bin/ohme-log-viewer`

Expected: a window titled "Ohme Log Viewer" with a port dropdown, Refresh, Connect and Clear buttons, an empty Tags panel on the left, and a dark empty log pane. Close it.

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/pytest -v`
Expected: 51 passed.

---

### Task 9: README and the on-device smoke test

The only step in this plan that needs real hardware. Everything before it is verifiable without a device.

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write the README**

Create `README.md`:

```markdown
# Ohme Log Viewer

Standalone live log viewer for Ohme Home Pro V2 devices. Streams the device's
EasyLogger output over USB serial, colour-codes it by level, and filters it by
tag.

This is a self-contained tool. It talks to `pyserial` directly and shares no
code with the `devcli-gui` log viewer in `firmware-hq/ohme_fw_cli`.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Run

```bash
.venv/bin/ohme-log-viewer
```

Pick the device's USB serial port from the dropdown and press **Connect**.

## ⚠️ Connecting consumes the device's logs

This viewer streams by sending `extract_logs`, and that command is **destructive
on the device**: the firmware deletes its archived log files and clears its ring
buffer once it has read them out. The logs move to your screen rather than being
copied to it.

So if you need to preserve a device's logs — a field return, a fault you have not
finished diagnosing, anything you might want a second look at — capture them
another way first. Connecting is not a read-only operation.

## What it does

- **Port selection** — every serial port on the machine, USB devices annotated
  with their VID:PID. **Refresh** re-enumerates.
- **Continuous streaming** — on Connect it sends `extract_logs`, reads until the
  device's `LOG_EXTRACT_DONE` sentinel, waits one second, and repeats until you
  disconnect. There is no one-shot mode.
- **Tag filter** — tags appear in the left panel as soon as a line carrying one
  arrives, checked by default, with a running count each. Unchecking hides that
  tag's lines; re-checking brings them back, including lines that arrived while
  it was hidden. Lines that do not match the EasyLogger format are grouped under
  `(unparsed)` rather than discarded.
- **Clear** — empties the view and the buffer but keeps your tag list and
  check states, so clearing before a test run does not cost you the filter
  setup.

The last 50,000 lines are held in memory. Levels are coloured: assert and error
red, warn amber, info plain, debug and verbose dimmed.

## Development

```bash
.venv/bin/pytest
```

The parser (`log_line.py`), buffer (`log_buffer.py`), transport
(`serial_link.py`) and extraction loop (`reader.py`) are covered by unit tests
and need no hardware. The Qt widgets are checked by the headless scripts in the
implementation plan and by the on-device smoke test.
```

- [ ] **Step 2: Hand the smoke test to the repository owner**

**This step is not run by the implementer.** `extract_logs` deletes the device's
archived logs and clears its ring buffer, so running it decides on the owner's
behalf that a particular charger's logs are expendable. That is theirs to
decide, not ours. Write the checklist below into the report as a handover and
stop there.

The owner runs `.venv/bin/ohme-log-viewer` with a Home Pro V2 plugged in — one
whose logs they are content to clear — and confirms each of these:

1. The device's port appears in the dropdown with a VID:PID annotation.
2. **Connect** starts lines flowing within a couple of seconds, and the button becomes **Disconnect**.
3. The status bar reads `Streaming from <device>   |   received: N   in view: M`, both numbers rising. (`in view` counts every line on display, `[error]` notices included, which is why it is not called `shown`.)
4. Tags accumulate in the left panel with counts that keep climbing.
5. Unchecking a busy tag makes its lines disappear from the pane, including ones already on screen.
6. Re-checking it brings those earlier lines back in the right order.
7. **None** then **All** empties and restores the pane — run this after several minutes of streaming, against a large buffer. On a near-empty one it looks instant however expensive it is, which is exactly how a tags × lines cost stayed hidden before.
8. Errors show red, warnings amber, info plain.
9. **Clear** empties the pane but leaves the tag list and check states as they were.
10. **One mouse-wheel notch** up stops the view auto-scrolling; scrolling back to the bottom resumes it. One notch specifically: a default notch is three lines, and overshooting by a screenful would pass this check even with the tolerance mis-set.
11. **Disconnect** stops the stream within about a second and re-enables the port dropdown.
12. Unplugging the USB cable mid-stream shows a red `[error]` line and a status-bar message, returns the UI to disconnected, and leaves the already-captured lines readable and filterable.
13. Closing the window exits cleanly with no `QThread: Destroyed while thread is still running` message on the console.
14. A `(unparsed)` group appears in the tag panel. Record what actually lands in it — command echo, the `MainMenu>` prompt, boot banner, something else — and confirm unchecking it leaves a clean view of parsed log lines only. Nothing else tests this group.
15. **Disconnect**, then **Connect** again on the same port. The second session is clean: lines flow, counts rise from where they were, no duplicated or interleaved output, and no `[error]` about a parked reader. This is the one check that exercises the per-connection `SerialLink`.
16. Leave it streaming past 50,000 lines. The pane and the buffer still agree — with every tag checked, `in view` stops rising at the cap rather than drifting away from it — and the process's memory stays flat rather than climbing.

Any of the 16 that fail become findings. Until the owner has run it, the branch
is complete only up to what can be verified without hardware — say so plainly
rather than implying the app is proven end to end.

- [ ] **Step 3: Final full-suite run**

Run: `.venv/bin/pytest -v`
Expected: 51 passed.
