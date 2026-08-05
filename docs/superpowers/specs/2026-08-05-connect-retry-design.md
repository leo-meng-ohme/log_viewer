# Connect retries until the device is there

**Date:** 2026-08-05
**Status:** approved

## Problem

Connect made exactly one attempt. A port that had not enumerated yet, a device
mid-reboot, or a cable seated a second later all produced an error and a
Disconnected window, leaving the operator to click Connect repeatedly. Firmware
work reboots devices constantly — DFU, watchdog resets, power cycling — so a
session that ends on the first read failure ends many times an hour.

## Behaviour

Three states where there were two:

| State | Button | Behaviour |
|-------|--------|-----------|
| Disconnected | **Connect** | idle |
| Retrying | **Disconnect** | one attempt per interval, no link held |
| Streaming | **Disconnect** | as before |

- **Connect** attempts immediately — no one-second wait when the device is
  already present — then every second until the port opens.
- **A session that drops goes back to Retrying** and resumes on its own. The
  buffer survives, so the lines that led up to the drop stay readable and
  filterable.
- **Disconnect stops everything**, from either state. It is the only way out.
- **"No port selected" does not retry.** There is nothing to retry against.
- **A repeated failure is reported once.** An absent port fails identically
  every second; reported each time it would be a pane line per second, burying
  what came before. The status bar carries the live state and attempt count.

## Retryable versus refused

The requirement to resume after a drop and the requirement to stop on an
authentication failure collide on one case, so `AuthError` distinguishes them
with a `retryable` flag:

| Failure | Retried |
|---------|---------|
| Port absent, busy, or gone | yes |
| Read failure mid-stream | yes |
| No `LOG_EXTRACT_DONE` within the cycle timeout | yes |
| Device did not answer `get_cli_status` / `cli_authenticate` in time | yes — a device mid-reboot answers nothing and is fine moments later |
| Backend unreachable, or HTTP 5xx | yes |
| HTTP 4xx from the backend, rejected JWT, bad password | no |
| Missing or unreadable credentials | no |
| Device reports no `charger_id` | no |

Without the transient cases on the retry side, a reboot landing mid-handshake
would kill the retry loop the feature exists to provide. Without the refusals on
the stop side, a permanent condition would cost a Firebase sign-in and a signing
request every second, indefinitely, from every engineer's machine.

## Design

`LogReader` gains an `auth_failed(str)` signal beside `error(str)`. The
classification happens in `LogReader.run`, because only the exception knows
which kind it is; the window then has two slots with opposite responses and no
message-sniffing anywhere.

`MainWindow` gains a single-shot `QTimer`. Single-shot rather than repeating so
that an armed timer always means "an attempt is pending" — the flag and the
state cannot disagree — and so it can never fire into an established session.
`_is_trying` is derived from the timer rather than held separately. `_attempt`
arms the next attempt only when the current one fails. `retry_interval_ms` is
constructor-injected (default 1000) so tests run in milliseconds, matching how
the link and authenticator factories are already injected.

`_connect`/`_disconnect` become `_start`/`_stop` around a shared `_teardown`,
because tearing the session down and giving up on it are now different things:
a drop does the first without the second.

## Testing

In `tests/test_main_window.py`, against the real window, timer and reader
thread: a port that fails three times then opens; the button offering to stop
while retrying; Disconnect halting further attempts; a repeated failure
producing one pane line; a mid-stream drop resuming and keeping its earlier
lines; a refusal stopping dead; a transient handshake failure retrying and then
succeeding; and "no port selected" reporting without retrying.

In `tests/test_log_reader.py`: which signal each kind of `AuthError` reaches. In
`tests/test_auth.py`: which failures are marked retryable, covering device
timeout, 403, 503, unreachable host, rejected password, missing credentials,
rejected JWT and unprovisioned device.

Window tests clear the port combo before adding their fake, because the
constructor enumerates the host's real ports and no test may depend on what is
plugged into the machine running it.
