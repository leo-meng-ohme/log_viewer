# Ohme Log Viewer — Design

Date: 2026-08-04

## Purpose

A standalone PySide6 desktop application that streams EasyLogger output from an
Ohme Home Pro V2 device over USB serial, displays it colour-coded, and lets the
user filter by log TAG and clear the view.

It is a fresh project. It talks to `pyserial` directly and does not import from,
depend on, or vendor any part of the `device_cli` package that ships the
existing devcli-gui log viewer. That existing viewer
(`ohme_fw_cli/src/device_cli/gui/log_viewer_window.py`) is the behavioural
reference only.

## Requirements

1. The user selects the USB serial port to connect to.
2. The user filters displayed lines by log TAG.
3. Streaming is always continuous — there is no one-shot mode.
4. The user can clear the log.

## Confirmed firmware behaviour

These facts were read out of the firmware and drive the parser. Sources are in
`fw_v2_products/apps/fw_v2_network_app/fw_hlv/utils/easylogger/`.

**Line format.** `elog.c:788-795` enables `ELOG_FMT_LVL | ELOG_FMT_TAG |
ELOG_FMT_TIME` for error, warn, info, debug and verbose. Every normal log line
is therefore:

```
I/MODEM [2026-07-01T10:11:11.950Z] Registered on network
```

**Level.** One of `A E W I D V`, emitted as `"<letter>/"` (`elog.c:121-128`).
The firmware's default runtime filter is `ELOG_LVL_INFO` (`elog.c:191`), so `D`
and `V` do not normally appear, but the parser accepts all six.

**Tag.** Written verbatim at whatever length it is (`elog.c:446-455`). The
padding branch fires only when the tag is 4 characters or shorter, and pads only
up to 4, followed in all cases by a single space. Long tags are neither padded
nor truncated. `ELOG_FILTER_TAG_MAX_LEN` (9) bounds the tag used for filtering
*inside the firmware*; it does not bound the tag printed on the line.

```
I/OTA   [2026-07-01T10:11:11.950Z] msg        3 chars: 1 pad + 1 space
I/MODEM [2026-07-01T10:11:11.950Z] msg        5 chars: no pad, 1 space
I/CHARGING_SESSION_MGR [2026-07-01T...] msg   long:    no pad, 1 space
```

The parser must make no fixed-width assumption about the tag.

**Timestamp.** ISO-8601 UTC with milliseconds, `YYYY-MM-DDThh:mm:ss.sssZ`
(`elog_port.c:190-207`). A failed conversion on the device yields
`0000-00-00T00:00:00.000Z`, which is a valid line, not a parse error.

**Trailing space inside the brackets.** `ELOG_FMT_T_INFO` is enabled for assert
lines, and `elog_port_get_t_info()` returns `""` (`elog_port.c:223-226`), so the
bracket group can end with a space before `]`. The extracted timestamp is
stripped.

**Assert lines** use `ELOG_FMT_ALL & ~ELOG_FMT_P_INFO`, adding file, function
and line in parentheses directly after the bracket group:
`A/TAG [<time> ] (<file> <func>:<line>)message`.

**Colour.** Disabled at init (`elog.c:184`), so lines carry no ANSI escapes.
The parser strips ANSI defensively anyway, so a build with colour enabled
degrades to correct text rather than garbage tags.

**Line length.** `ELOG_LINE_BUF_SIZE` is `FW_FWK_LOG_LINE_SIZE` = 1024
(`configuration/fw_fwk_log_configuration.h:5`). Longer messages are truncated on
the device.

**Extraction protocol.** Sending `extract_logs` streams log payload and ends
with a single `LOG_EXTRACT_DONE` sentinel line. The firmware also emits bare
`\r\n` at some transitions to flush a log line left dangling by a
chunk-boundary-unaligned read, which surfaces as an empty line with no
diagnostic value. Older firmware emitted additional multi-line banner text
around the payload.

**Serial parameters.** 115200 baud, 8N1, CRLF line endings.

## Architecture

```
log_viewer/
  pyproject.toml
  README.md
  src/ohme_log_viewer/
    __main__.py       entry point (python -m ohme_log_viewer / ohme-log-viewer)
    log_line.py       LogLine dataclass, parse(), is_noise()          pure logic
    log_buffer.py     ring buffer + tag filter -> visible lines       pure logic
    serial_link.py    pyserial wrapper: open/close, write_line, read_line
    reader.py         QThread running the continuous extract_logs loop
    ui/main_window.py QMainWindow: toolbar + tag panel + log pane + status bar
    ui/tag_panel.py   auto-discovered checkable tag list
    ui/log_pane.py    coloured read-only view, renders from the buffer
  tests/              pytest over log_line, log_buffer, serial_link
```

Three boundaries carry the design:

- `log_line` and `log_buffer` are pure Python — no Qt, no serial. Fully
  unit-testable, and they hold all the logic worth testing.
- `serial_link` is the only module that moves bytes on and off the port, and
  `reader` the only one that runs the `extract_logs` loop on its own thread.
- `ui/` owns the pipeline and sequences it: `MainWindow` calls
  `list_usb_ports()`, `link.open()`/`link.close()`, `is_noise()` and `parse()`
  in the order the data flow below describes. What it does not do is any
  byte-level I/O of its own or any parsing of its own — nothing in `ui/`
  imports pyserial, and nothing in `ui/` knows the log line format.

### Module contracts

**`log_line.py`**

```python
@dataclass(frozen=True)
class LogLine:
    raw: str                  # line as received, ANSI stripped
    level: str | None         # 'A' 'E' 'W' 'I' 'D' 'V', or None if unparsed
    tag: str | None           # arbitrary length, or None if unparsed
    timestamp: str | None     # ISO-8601 string as sent, stripped
    message: str              # payload, or the whole raw line if unparsed
    source: str | None        # '(file func:line)' contents on assert lines

def parse(raw: str) -> LogLine
def is_noise(raw: str) -> bool
```

The regex is
`^([AEWIDV])/(\S+)\s+\[([^\]]+)\]\s*(?:\(([^)]*)\))?\s*(.*)$`. `\S+` takes the
tag at any length and `\s+` absorbs however many spaces follow, so short and
long tags parse identically.

The parenthesised source group is only honoured when the level is `A`. Only
assert lines enable `ELOG_FMT_DIR | ELOG_FMT_FUNC | ELOG_FMT_LINE`, so on any
other level a leading `(...)` is part of the message — a line like
`I/TAG [ts] (retry 3) connecting` must keep `(retry 3)` in `message` rather than
mistaking it for source info. For non-assert levels `source` is always `None`
and the group is folded back into `message`.

Unmatched lines — boot banners, CLI echo, a message wrapped across a chunk
boundary — are **not dropped**. They return a `LogLine` with `tag=None`, grouped
in the UI under the pseudo-tag `(unparsed)`, so they can be hidden but are never
silently lost.

`is_noise` returns True for the `LOG_EXTRACT_DONE` sentinel, blank lines, and
the legacy banner patterns. These are the only lines genuinely discarded.

**`log_buffer.py`**

Owns a `collections.deque(maxlen=50_000)` of `LogLine`. This deque is the single
source of truth; the text pane is a projection of it.

```python
UNPARSED_TAG = "(unparsed)"

class LogBuffer:
    def append(self, line: LogLine) -> bool     # True if currently visible
    def clear(self) -> None                     # empties lines, keeps tag state
    def tags(self) -> list[str]                 # discovery order
    def count_for(self, tag: str) -> int
    def is_tag_enabled(self, tag: str) -> bool
    def set_tag_enabled(self, tag: str, on: bool) -> None
    def visible(self) -> Iterator[LogLine]      # full re-render source
```

`LogLine.tag` is `None` for unparsed lines, but every buffer key is a string: the
buffer maps `None` to the `UNPARSED_TAG` constant `"(unparsed)"`. That single
key is what `tags()`, `count_for()` and `set_tag_enabled()` use, so the UI treats
unparsed lines as an ordinary checkable tag with no special-casing. A device tag
literally named `(unparsed)` is impossible, since `ELOG_TAG` values are C
identifiers.

`append` returns whether the line passes the current filter, which is what the
incremental display path uses. `visible()` is the separate full re-render path,
used when a checkbox changes. Because that path reads the deque rather than only
gating new arrivals, **tag filtering is retroactive**: re-checking a previously
unchecked tag brings its earlier lines back.

**`serial_link.py`**

Thin synchronous wrapper over `serial.Serial`: `list_usb_ports()`, `open()`,
`close()`, `write_line()`, and a line-buffered `read_line()` that accumulates
into a `bytearray` and splits on `\n`, so a chunk boundary mid-line does not
produce two half-lines. Decodes UTF-8 with `errors="replace"`.

**`reader.py`**

One long-lived `QThread` for the whole connected session:

```
while not interrupted:
    write "extract_logs\r\n"
    read lines until LOG_EXTRACT_DONE (or per-line timeout, re-checking interrupt)
    sleep 1 s in short interruptible slices
```

Signals: `line_received(str)`, `error(str)`, `finished()`.

This is deliberately simpler than the devcli reference, which constructs and
destroys a `StreamWorker` every cycle and needs a `QTimer` re-arm plus a careful
`wait()`/`deleteLater()` sequence to avoid destroying a running `QThread`. Since
continuous mode is always on here, a single thread living from Connect to
Disconnect removes that entire class of lifecycle bug. Requirement 3 holds by
construction: there is no continuous-mode toggle to fall out of sync, because
there is no non-continuous mode.

Marker detection runs on the raw line, before noise filtering, so hiding the
sentinel from the display cannot desynchronise the TX/RX cycle.

## Data flow

`MainWindow` owns the pipeline. Its `_on_line_received` slot is the one place
that sequences noise filtering, parsing and buffering; `LogBuffer` never parses
and `reader` never interprets.

```
reader thread
  -> line_received(str)          (queued cross-thread signal)
  -> MainWindow._on_line_received:
       is_noise? discard
       parse -> LogLine
       LogBuffer.append -> visible?
       if visible, add to pending-display list
  -> 100 ms flush timer -> one batched append to the pane
```

The flush timer means a burst of a few thousand lines costs one repaint rather
than a few thousand.

A tag checkbox toggle takes the other path: `set_tag_enabled` followed by a full
re-render of the pane from `visible()`, with the pending-display list dropped so
a line cannot be appended twice.

## UI

**Toolbar** — port combo populated from `serial.tools.list_ports.comports()`
with USB devices annotated `VID:PID`; Refresh; Connect/Disconnect; Clear.

**Left panel** — tag checkboxes, added on first sighting of each tag, all
checked by default, with a per-tag line count and All/None buttons. The column
is fixed-width with elided labels and the full tag in a tooltip, so a long tag
name cannot squeeze the log pane.

**Main pane** — monospace dark read-only view. Colour by level: assert and error
red, warn amber, info default, debug and verbose dimmed.

**Status bar** — connection state, port, total lines received, lines currently
shown.

**Clear** empties the buffer and the pane but keeps the discovered tag list and
the user's checkbox choices. Clearing is for cutting noise before a test run;
losing the filter setup at that exact moment would be the wrong behaviour.

## Error handling

A port that will not open, or one that vanishes mid-stream because the USB cable
was pulled, causes the reader to emit `error` and stop. The UI returns to the
disconnected state and shows the message in the status bar and as an inline
`[error]` line in the pane. The buffer survives, so previously captured lines
remain readable and filterable. No modal dialogs in the streaming path.

## Testing

`pytest` over the pure modules:

- `parse` of every level letter.
- A 3-character padded tag, a 5-character unpadded tag, and a 20+ character long
  tag — all asserting the extracted tag string with no padding artefacts.
- An assert line with `(file func:line)` source info.
- A non-assert line whose message begins with `(...)`, asserting `source` is
  `None` and the parenthesised text stays in `message`.
- A bracket group with a trailing space, asserting the timestamp is stripped.
- The device's `0000-00-00T00:00:00.000Z` fallback timestamp.
- A line carrying ANSI escapes, asserting they are stripped and the tag is still
  extracted.
- Unparsed fallback: an unmatched line yields `tag=None` and preserves the raw
  text.
- `is_noise` for the sentinel, blank lines, and legacy banners.
- Ring-buffer eviction at `maxlen`.
- An unparsed line lands under the `(unparsed)` key and can be hidden and shown
  like any other tag.
- `clear()` empties the lines but preserves `tags()` and the enabled/disabled
  state of each.
- Filter rendering, including the retroactive case: append lines, disable a tag,
  re-enable it, assert the earlier lines are visible again.
- `serial_link.read_line` against a fake serial object, including a line split
  across two chunk reads.

The Qt widgets get a manual smoke test against a device, not unit tests.

## Out of scope

Deliberately excluded to keep the first version small. Each is a clean addition
later:

- Minimum-level filter (hide debug/verbose). Level is already parsed, so this is
  a small change.
- Save-to-file.
- Opening previously saved log files for offline review.
- Search or text highlighting within the pane.
