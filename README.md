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

## Authenticated devices

`extract_logs` is a protected command. A device that has finished QA — or one
that is mid-QA — refuses it until the CLI session is authenticated, answering
with a challenge instead of logs:

```json
{"result":"auth_required","challenge":"da641d121b05c32f76ffc52735cedf76"}
```

The viewer completes that handshake itself: it asks the device for its
`charger_id` with `get_cli_status`, has the Ohme backend sign the challenge for
that charger, sends the resulting JWT as `cli_authenticate <jwt>`, and re-sends
`extract_logs`. The CLI auth private key never leaves the backend. Progress
appears in the log as `[auth]` lines. A factory-fresh device never challenges,
and nothing below applies to it.

Signing needs backend credentials, which are read from the `device-cli`
configuration this machine already has — so if `devcli` works here, the viewer
does too, and there is no second place keeping your password:

- macOS: `~/Library/Application Support/device-cli/config.toml`, `[app]` section
  (`be_url`, `email`, `password`)
- Linux: `~/.config/device-cli/config.toml`
- Windows: `%APPDATA%/device-cli/config.toml`

`OHME_BE_URL`, `OHME_EMAIL` and `OHME_PASSWORD` override the file. A Firebase
token that `devcli` has already cached alongside that config is reused, so an
authenticated device usually costs one HTTP call. The cache is only read, never
written.

If the credentials are missing or the backend refuses to sign, the session ends
with that reason in the status bar rather than a silent stall.

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
- **Flipping several tags at once** — select a group first (shift-click for a
  run, ⌘-click to add or drop one, or drag), then tick or untick any one of them
  and the whole selection follows. Space does the same. The selection survives,
  so the same group can be flipped back without picking it out again. A tag you
  have not selected still toggles on its own, as before.
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
(`serial_link.py`), extraction loop (`reader.py`) and authentication
(`auth.py`, including its HTTP calls against a fake opener) are covered by unit
tests and need no hardware. Nor does the reader's `QThread`:
`tests/test_log_reader.py` drives the real thread and its real signal
connections against a fake link, with Qt on its offscreen platform.
`tests/test_main_window.py` covers what the window decides per session — which
link and authenticator a connection is given — by injecting both, and follows one
tag filter change from the panel through to the lines left in view.
`tests/test_tag_panel.py` drives the real tag list with `QTest` clicks and
keystrokes, so the multi-select behaviour is checked against Qt itself rather
than a stub. What remains for the on-device smoke test is the parts only a real
device exercises: the authenticated handshake and a live extraction.
