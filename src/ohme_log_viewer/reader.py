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
from typing import Callable

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

    def run(self) -> None:  # noqa: D102 - QThread entry point
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
