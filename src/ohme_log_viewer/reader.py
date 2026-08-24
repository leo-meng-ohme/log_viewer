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

Marker detection runs before any noise filtering, so hiding the sentinel from
the display cannot desynchronise the TX/RX cycle. It does normalise the line
first: the previous chunk's NUL padding runs into the sentinel with no newline
between them, and matching it verbatim would miss the sentinel and stall the
cycle until its timeout. See :func:`ohme_log_viewer.log_line.strip_non_text`.

``extract_logs`` is a protected command: a provisioned device answers it with
an authentication challenge instead of log payload until the session is
authenticated. The challenge is therefore part of this loop's protocol, not log
output — see :mod:`ohme_log_viewer.auth`.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from PySide6.QtCore import QThread, Signal

from .auth import AuthError, parse_auth_challenge
from .log_line import strip_non_text
from .serial_link import SerialLink, SerialLinkError

#: Command that makes the device stream its logs.
COMMAND = "extract_logs"

#: Sentinel line the device sends at the end of each extraction.
MARKER = "LOG_EXTRACT_DONE"

#: Pause between cycles.
CYCLE_DELAY_S = 1.0

#: How long a single cycle may run without seeing the marker.
CYCLE_TIMEOUT_S = 120.0

#: Handshakes attempted within one cycle before giving up.
#:
#: One is normally enough. A second covers the window after a device reboot in
#: which cli_authenticate reports success but the resent command is dispatched
#: before the session is marked authenticated, and the device challenges again.
#: Beyond that the device is refusing us, and retrying forever would look
#: exactly like streaming while producing nothing.
MAX_AUTH_ATTEMPTS = 2

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
    authenticator=None,
) -> bool:
    """Run one extract_logs cycle.

    Sends the command, then passes every received line to *emit* until the
    completion marker arrives. Returns True on a completed cycle and False if
    *should_stop* asked to stop partway. Raises :class:`CycleTimeout` if the
    marker does not arrive within *timeout_s*.

    A challenge from the device is answered through *authenticator* and the
    command resent. The challenge is never emitted: it is protocol, and shown
    as a log line it would leave the user watching a stalled cycle wondering
    what the JSON meant. Raises :class:`~ohme_log_viewer.auth.AuthError` if the
    session cannot be authenticated.
    """
    link.write_line(COMMAND)
    deadline = now() + timeout_s
    auth_attempts = 0

    while True:
        if should_stop():
            return False
        if now() > deadline:
            raise CycleTimeout(f"No '{MARKER}' within {timeout_s:.0f}s")

        line = link.read_line()
        if line is None:
            continue  # read timeout; keep waiting until the deadline
        if strip_non_text(line).strip() == MARKER:
            return True

        challenge = parse_auth_challenge(line)
        if challenge is None:
            emit(line)
            continue

        if authenticator is None:
            raise AuthError(
                f"Device requires an authenticated CLI session for '{COMMAND}', "
                f"but no authenticator is configured"
            )
        auth_attempts += 1
        if auth_attempts > MAX_AUTH_ATTEMPTS:
            raise AuthError(
                f"Device kept asking for authentication after "
                f"{MAX_AUTH_ATTEMPTS} successful handshakes"
            )
        if not authenticator.authenticate(link, challenge, emit, should_stop):
            return False  # asked to stop mid-handshake
        link.write_line(COMMAND)
        # The handshake spends real time on the device's slow signature check,
        # and the extraction it precedes has not started yet. Timing that
        # extraction from before the handshake would cut it short.
        deadline = now() + timeout_s


class LogReader(QThread):
    """Runs :func:`run_extract_cycle` back to back until interrupted."""

    line_received: Signal = Signal(str)

    #: A failure worth another attempt: the port, the device, or a transient on
    #: the way to the backend.
    error: Signal = Signal(str)

    #: A refusal. Separate from ``error`` because the two demand opposite
    #: responses: the window retries the one and gives up on the other, and no
    #: amount of waiting turns a rejected credential into an accepted one.
    auth_failed: Signal = Signal(str)

    def __init__(self, link: SerialLink, authenticator=None, parent=None) -> None:
        super().__init__(parent)
        self._link = link
        self._authenticator = authenticator

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
                    authenticator=self._authenticator,
                )
                if not completed:
                    return
                if not self._sleep_interruptibly(CYCLE_DELAY_S):
                    return
        except AuthError as exc:
            # Sorted here rather than in the window, because only the exception
            # knows whether it describes a refusal or a transient.
            if exc.retryable:
                self.error.emit(str(exc))
            else:
                self.auth_failed.emit(str(exc))
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
