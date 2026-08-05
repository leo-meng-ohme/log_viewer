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
.venv/bin/ruff check src/ tests/
.venv/bin/black --check src/ tests/
```

The parser (`log_line.py`), buffer (`log_buffer.py`), transport
(`serial_link.py`) and extraction loop (`reader.py`) are covered by unit tests
and need no hardware. Nor does the reader's `QThread`: `tests/test_log_reader.py`
drives the real thread and its real signal connections against a fake link, with
Qt on its offscreen platform. The widgets themselves are checked by the headless
scripts in the implementation plan and by the on-device smoke test.
